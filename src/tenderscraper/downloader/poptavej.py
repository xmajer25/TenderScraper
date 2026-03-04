from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from playwright.sync_api import Page, sync_playwright

from tenderscraper.scraping.auth.poptavej_auth import ensure_storage_state
from tenderscraper.scraping.files import guess_mime_type, sanitize_filename, sha256_file, unique_path
from tenderscraper.scraping.overlays import dismiss_common_overlays


# ---- selectors on detail page (logged-in) ----
# Structure: <h4>Přílohy:</h4> then repeated:
#   <a target="_blank" href="...">filename.ext</a><br>
ATTACH_LINKS_SEL = "div.main-text h4:has-text('Přílohy') ~ a[target='_blank'][href]"

# url pattern:
# https://sta.poptavej.cz/data/procurement/file/YYYY/MM/DD/FILENAME
_FILENAME_FROM_URL_RE = re.compile(
    r"/data/procurement/file/\d{4}/\d{2}/\d{2}/([^/?#]+)$", re.IGNORECASE
)

# Placeholder pattern in meta.json: "Příloha č. 1.pdf"
_PLACEHOLDER_RE = re.compile(r"^Příloha\s+č\.\s*(\d+)\b", re.IGNORECASE)


@dataclass(frozen=True)
class _Attachment:
    url: str
    filename: str  # prefer link text; fallback to url segment


def _safe_tmp_path(raw_dir: Path) -> Path:
    return raw_dir / f"__tmp__{uuid.uuid4().hex}"


def _filename_from_poptavej_url(url: str) -> Optional[str]:
    m = _FILENAME_FROM_URL_RE.search(url)
    if m:
        return m.group(1)
    path = urlparse(url).path
    seg = path.rsplit("/", 1)[-1]
    return seg or None


def _extract_attachments(page: Page) -> List[_Attachment]:
    out: List[_Attachment] = []
    links = page.locator(ATTACH_LINKS_SEL)
    for i in range(links.count()):
        a = links.nth(i)
        href = (a.get_attribute("href") or "").strip()
        if not href:
            continue

        text = None
        try:
            text = (a.inner_text() or "").strip() or None
        except Exception:
            text = None

        fn = text or _filename_from_poptavej_url(href) or "attachment"
        out.append(_Attachment(url=href, filename=fn))
    return out


def _is_logged_in(page: Page) -> bool:
    # Your post-login header example:
    # <a href="/dodavatel/zaslane-poptavky">Meta IT, s.r.o.</a>
    try:
        if page.locator("a[href='/dodavatel/zaslane-poptavky']").count() > 0:
            return True
    except Exception:
        pass
    return False


def _parse_placeholder_index(filename: str) -> Optional[int]:
    """
    'Příloha č. 2.zip' -> 2
    """
    m = _PLACEHOLDER_RE.match((filename or "").strip())
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def download_poptavej_docs(*, meta_path: Path, timeout_ms: int = 60_000) -> None:
    """
    Logged-in downloader for poptavej.cz:
      - ensures storage_state
      - opens meta.notice_url
      - extracts attachment links + filenames
      - maps them to meta['documents'] (by placeholder index if present, else by order)
      - downloads via context.request.get (reliable for PDF + other files)
      - writes to raw/ and updates meta.json
    """
    meta: Dict[str, Any] = json.loads(meta_path.read_text(encoding="utf-8"))
    notice_url = meta.get("notice_url")
    if not notice_url:
        return

    tender_dir = meta_path.parent
    raw_dir = tender_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    docs_meta: List[Dict[str, Any]] = meta.get("documents", []) or []
    meta["documents"] = docs_meta  # ensure exists

    # Ensure auth state exists & valid
    storage_state = ensure_storage_state(headless=True, timeout_ms=30_000, force_relogin=False)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(storage_state))
        page = context.new_page()

        try:
            page.goto(notice_url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(250)
            dismiss_common_overlays(page)

            if not _is_logged_in(page):
                raise RuntimeError(
                    "Not authenticated on poptavej detail page (storage_state invalid or expired)."
                )

            attachments = _extract_attachments(page)
            if not attachments:
                return

            # If documents list empty -> populate from attachments (real filenames + urls)
            if len(docs_meta) == 0:
                for att in attachments:
                    docs_meta.append(
                        {
                            "url": att.url,
                            "filename": att.filename,
                            "mime_type": None,
                            "local_path": None,
                            "size_bytes": None,
                            "sha256": None,
                            "downloaded_at": None,
                        }
                    )

            # Build mapping from docs entries to attachment by:
            # 1) If doc filename is placeholder "Příloha č. X" => map to attachments[X-1]
            # 2) Else if doc filename matches some attachment filename exactly => map by name
            # 3) Else fallback by order (index)
            att_by_name: Dict[str, _Attachment] = {a.filename: a for a in attachments}

            def pick_attachment_for_doc(doc: Dict[str, Any], idx: int) -> Optional[_Attachment]:
                fn = (doc.get("filename") or "").strip()
                if fn:
                    n = _parse_placeholder_index(fn)
                    if n is not None:
                        j = n - 1
                        if 0 <= j < len(attachments):
                            return attachments[j]
                    if fn in att_by_name:
                        return att_by_name[fn]
                # fallback by order
                if 0 <= idx < len(attachments):
                    return attachments[idx]
                return None

            # Download loop
            for i, doc in enumerate(docs_meta):
                # already downloaded?
                if doc.get("local_path") and doc.get("sha256"):
                    continue

                att = pick_attachment_for_doc(doc, i)
                if not att:
                    continue

                # Prefer server filename (attachment filename)
                server_name = sanitize_filename(att.filename)
                if not Path(server_name).suffix:
                    # fallback to url filename extension
                    url_fn = _filename_from_poptavej_url(att.url) or ""
                    ext = Path(url_fn).suffix
                    if ext:
                        server_name = server_name + ext

                target = unique_path(raw_dir / server_name)
                tmp = _safe_tmp_path(raw_dir)

                # ---- real download via HTTP (within same authenticated context) ----
                # This avoids PDF viewer / popups entirely.
                resp = context.request.get(att.url, timeout=timeout_ms)
                if not resp.ok:
                    # keep URL updated, but don't crash the whole run
                    doc["url"] = att.url
                    continue

                body = resp.body()
                tmp.write_bytes(body)

                # Move tmp to target (Windows-safe)
                try:
                    tmp.replace(target)
                except Exception:
                    data = tmp.read_bytes()
                    target.write_bytes(data)
                    tmp.unlink(missing_ok=True)

                size_bytes = target.stat().st_size
                sha = sha256_file(target)
                mime = guess_mime_type(target.name)

                # Update doc entry (also upgrade placeholder filename to real filename)
                doc["url"] = att.url
                doc["filename"] = att.filename
                doc["local_path"] = str(Path("raw") / target.name)
                doc["size_bytes"] = int(size_bytes)
                doc["sha256"] = sha
                doc["mime_type"] = mime
                doc["downloaded_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

                # pace: be polite
                page.wait_for_timeout(250)

        finally:
            try:
                context.close()
            except Exception:
                pass
            browser.close()

    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from playwright.sync_api import Page, sync_playwright

from tenderscraper.config import settings
from tenderscraper.repository import upsert_tender_meta
from tenderscraper.scraping.auth.poptavej_auth import ensure_storage_state
from tenderscraper.scraping.files import guess_mime_type, sanitize_filename, sha256_file, unique_path
from tenderscraper.scraping.overlays import dismiss_common_overlays
from tenderscraper.storage.object_store import persist_downloaded_file

ATTACH_LINKS_SEL = "div.main-text h4:has-text('Přílohy') ~ a[target='_blank'][href]"
_FILENAME_FROM_URL_RE = re.compile(r"/data/procurement/file/\d{4}/\d{2}/\d{2}/([^/?#]+)$", re.IGNORECASE)
_PLACEHOLDER_RE = re.compile(r"^Příloha\s+č\.\s*(\d+)\b", re.IGNORECASE)


@dataclass(frozen=True)
class _Attachment:
    url: str
    filename: str


def _scratch_dir(source: str, tender_id: str) -> Path:
    path = settings.scratch_dir / f"source={source}" / f"tender={tender_id}" / "raw"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_tmp_path(raw_dir: Path) -> Path:
    return raw_dir / f"__tmp__{uuid.uuid4().hex}"


def _filename_from_poptavej_url(url: str) -> Optional[str]:
    match = _FILENAME_FROM_URL_RE.search(url)
    if match:
        return match.group(1)
    path = urlparse(url).path
    segment = path.rsplit("/", 1)[-1]
    return segment or None


def _extract_attachments(page: Page) -> List[_Attachment]:
    out: List[_Attachment] = []
    links = page.locator(ATTACH_LINKS_SEL)
    for i in range(links.count()):
        link = links.nth(i)
        href = (link.get_attribute("href") or "").strip()
        if not href:
            continue

        text = None
        try:
            text = (link.inner_text() or "").strip() or None
        except Exception:
            text = None

        filename = text or _filename_from_poptavej_url(href) or "attachment"
        out.append(_Attachment(url=href, filename=filename))
    return out


def _is_logged_in(page: Page) -> bool:
    try:
        if page.locator("a[href='/dodavatel/zaslane-poptavky']").count() > 0:
            return True
    except Exception:
        pass
    return False


def _parse_placeholder_index(filename: str) -> Optional[int]:
    match = _PLACEHOLDER_RE.match((filename or "").strip())
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def download_poptavej_docs(*, meta: Dict[str, Any], timeout_ms: int = 60_000) -> None:
    notice_url = meta.get("notice_url")
    if not notice_url:
        return

    source = str(meta.get("source") or "")
    tender_id = str(meta.get("source_tender_id") or "")
    raw_dir = _scratch_dir(source, tender_id)

    docs_meta: List[Dict[str, Any]] = meta.get("documents", []) or []
    meta["documents"] = docs_meta

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
                raise RuntimeError("Not authenticated on poptavej detail page (storage_state invalid or expired).")

            attachments = _extract_attachments(page)
            if not attachments:
                return

            if len(docs_meta) == 0:
                for attachment in attachments:
                    docs_meta.append(
                        {
                            "url": attachment.url,
                            "filename": attachment.filename,
                            "mime_type": None,
                            "storage_key": None,
                            "storage_url": None,
                            "size_bytes": None,
                            "sha256": None,
                            "downloaded_at": None,
                        }
                    )

            attachments_by_name: Dict[str, _Attachment] = {attachment.filename: attachment for attachment in attachments}

            def pick_attachment_for_doc(document: Dict[str, Any], idx: int) -> Optional[_Attachment]:
                filename = (document.get("filename") or "").strip()
                if filename:
                    placeholder = _parse_placeholder_index(filename)
                    if placeholder is not None:
                        j = placeholder - 1
                        if 0 <= j < len(attachments):
                            return attachments[j]
                    if filename in attachments_by_name:
                        return attachments_by_name[filename]
                if 0 <= idx < len(attachments):
                    return attachments[idx]
                return None

            for i, document in enumerate(docs_meta):
                if document.get("storage_key") and document.get("sha256"):
                    continue

                attachment = pick_attachment_for_doc(document, i)
                if not attachment:
                    continue

                server_name = sanitize_filename(attachment.filename)
                if not Path(server_name).suffix:
                    url_filename = _filename_from_poptavej_url(attachment.url) or ""
                    ext = Path(url_filename).suffix
                    if ext:
                        server_name = server_name + ext

                target = unique_path(raw_dir / server_name)
                tmp = _safe_tmp_path(raw_dir)

                response = context.request.get(attachment.url, timeout=timeout_ms)
                if not response.ok:
                    document["url"] = attachment.url
                    continue

                body = response.body()
                tmp.write_bytes(body)

                try:
                    tmp.replace(target)
                except Exception:
                    data = tmp.read_bytes()
                    target.write_bytes(data)
                    tmp.unlink(missing_ok=True)

                size_bytes = target.stat().st_size
                sha = sha256_file(target)
                mime = guess_mime_type(target.name)
                stored = persist_downloaded_file(
                    file_path=target,
                    source=source,
                    tender_id=tender_id,
                )

                document["url"] = attachment.url
                document["filename"] = attachment.filename
                document["storage_key"] = stored.storage_key
                document["storage_url"] = stored.storage_url
                document["size_bytes"] = int(size_bytes)
                document["sha256"] = sha
                document["mime_type"] = mime
                document["downloaded_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

                page.wait_for_timeout(250)

        finally:
            try:
                context.close()
            except Exception:
                pass
            browser.close()

    upsert_tender_meta(meta)

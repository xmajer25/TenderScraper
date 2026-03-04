from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from playwright.sync_api import Error as PWError
from playwright.sync_api import sync_playwright

from tenderscraper.scraping.files import guess_mime_type, sanitize_filename, sha256_file, unique_path
from tenderscraper.scraping.label_value import get_value_by_label
from tenderscraper.scraping.overlays import dismiss_common_overlays


DOC_ROW_XPATH = "//*[@id='seznam-dokumentu']//app-dokument/section"
DOC_MODAL_XPATH = "//*[@id='detail-dokumentu-modalni-panel']"


def _wait_clickable(locator, *, timeout_ms: int = 15_000) -> None:
    """Wait until element is visible/enabled enough to click."""
    locator.wait_for(state="visible", timeout=timeout_ms)
    end = datetime.now(timezone.utc).timestamp() + (timeout_ms / 1000)
    while datetime.now(timezone.utc).timestamp() < end:
        try:
            if locator.is_enabled():
                return
        except Exception:
            pass
        locator.page.wait_for_timeout(250)


def _safe_tmp_path(raw_dir: Path) -> Path:
    # short deterministic temp name for Windows paths
    return raw_dir / f"__tmp__{uuid.uuid4().hex}"


def download_tender_arena_docs(*, meta_path: Path) -> None:
    """Open tender page, download docs into raw/, update meta.json in-place."""
    meta: Dict[str, Any] = json.loads(meta_path.read_text(encoding="utf-8"))
    notice_url = meta.get("notice_url")
    if not notice_url:
        return

    tender_dir = meta_path.parent
    raw_dir = tender_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    docs_meta: List[Dict[str, Any]] = meta.get("documents", [])
    by_filename: Dict[str, Dict[str, Any]] = {}
    for d in docs_meta:
        fn = (d.get("filename") or "").strip()
        if fn:
            by_filename[fn] = d

    if not by_filename:
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(accept_downloads=True)

        try:
            page.goto(notice_url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(300)
            dismiss_common_overlays(page)

            doc_sections = page.locator(f"xpath={DOC_ROW_XPATH}")
            if doc_sections.count() == 0:
                return

            for i in range(doc_sections.count()):
                sec = doc_sections.nth(i)

                info_btn = sec.locator("button[title='Zobrazit detail']").first
                if info_btn.count() == 0:
                    continue

                dismiss_common_overlays(page)
                try:
                    info_btn.click(timeout=5_000)
                except Exception:
                    dismiss_common_overlays(page)
                    info_btn.click(timeout=5_000, force=True)

                page.wait_for_selector(f"xpath={DOC_MODAL_XPATH}", timeout=30_000)
                canonical = get_value_by_label(page, "Název souboru")
                canonical = canonical.strip() if canonical else None

                # Close modal
                modal = page.locator(f"xpath={DOC_MODAL_XPATH}").first
                close_btn = modal.get_by_role("button", name="Zavřít")
                if close_btn.count() == 0:
                    close_btn = modal.get_by_role("button", name="Close")
                if close_btn.count() > 0:
                    close_btn.first.click(timeout=2_000)
                else:
                    page.keyboard.press("Escape")

                page.wait_for_timeout(150)
                dismiss_common_overlays(page)

                if not canonical:
                    continue

                doc_entry = by_filename.get(canonical)
                if not doc_entry:
                    continue

                if doc_entry.get("local_path") and doc_entry.get("sha256"):
                    continue

                dl_btn = sec.locator("button[title='Stáhnout']").first
                if dl_btn.count() == 0:
                    continue

                dismiss_common_overlays(page)
                _wait_clickable(dl_btn, timeout_ms=15_000)

                # First attempt
                with page.expect_download(timeout=90_000) as download_info:
                    try:
                        dl_btn.click(timeout=10_000)
                    except Exception:
                        dismiss_common_overlays(page)
                        dl_btn.click(timeout=10_000, force=True)

                download = download_info.value

                safe_name = sanitize_filename(canonical)
                if Path(safe_name).suffix == "":
                    sug = download.suggested_filename or ""
                    ext = Path(sug).suffix
                    if ext:
                        safe_name = safe_name + ext

                target = unique_path(raw_dir / safe_name)
                tmp = _safe_tmp_path(raw_dir)

                saved = False
                for _attempt in range(1, 4):
                    try:
                        download.save_as(str(tmp))
                        saved = True
                        break
                    except PWError as e:
                        if "canceled" not in str(e).lower():
                            raise

                        # cleanup temp
                        try:
                            if tmp.exists():
                                tmp.unlink()
                        except Exception:
                            pass

                        page.wait_for_timeout(700)
                        dismiss_common_overlays(page)
                        _wait_clickable(dl_btn, timeout_ms=15_000)

                        with page.expect_download(timeout=90_000) as download_info2:
                            try:
                                dl_btn.click(timeout=10_000)
                            except Exception:
                                dismiss_common_overlays(page)
                                dl_btn.click(timeout=10_000, force=True)

                        download = download_info2.value
                        tmp = _safe_tmp_path(raw_dir)

                if not saved:
                    continue

                try:
                    tmp.replace(target)
                except Exception:
                    data = tmp.read_bytes()
                    target.write_bytes(data)
                    tmp.unlink(missing_ok=True)

                size_bytes = target.stat().st_size
                sha = sha256_file(target)
                mime = guess_mime_type(target.name)

                doc_entry["local_path"] = str(Path("raw") / target.name)
                doc_entry["size_bytes"] = int(size_bytes)
                doc_entry["sha256"] = sha
                doc_entry["mime_type"] = mime
                doc_entry["downloaded_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

                # pace a bit (reduces throttling/cancel)
                page.wait_for_timeout(400)

        finally:
            browser.close()

    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

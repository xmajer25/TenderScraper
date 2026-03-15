from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from playwright.sync_api import Error as PWError
from playwright.sync_api import sync_playwright

from tenderscraper.config import settings
from tenderscraper.repository import upsert_tender_meta
from tenderscraper.scraping.files import guess_mime_type, sanitize_filename, sha256_file, unique_path
from tenderscraper.scraping.label_value import get_value_by_label
from tenderscraper.scraping.overlays import dismiss_common_overlays
from tenderscraper.storage.object_store import persist_downloaded_file

DOC_ROW_XPATH = "//*[@id='seznam-dokumentu']//app-dokument/section"
DOC_MODAL_XPATH = "//*[@id='detail-dokumentu-modalni-panel']"


def _wait_clickable(locator, *, timeout_ms: int = 15_000) -> None:
    locator.wait_for(state="visible", timeout=timeout_ms)
    end = datetime.now(timezone.utc).timestamp() + (timeout_ms / 1000)
    while datetime.now(timezone.utc).timestamp() < end:
        try:
            if locator.is_enabled():
                return
        except Exception:
            pass
        locator.page.wait_for_timeout(250)


def _scratch_dir(source: str, tender_id: str) -> Path:
    path = settings.scratch_dir / f"source={source}" / f"tender={tender_id}" / "raw"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_tmp_path(raw_dir: Path) -> Path:
    return raw_dir / f"__tmp__{uuid.uuid4().hex}"


def download_tender_arena_docs(*, meta: Dict[str, Any]) -> None:
    notice_url = meta.get("notice_url")
    if not notice_url:
        return

    source = str(meta.get("source") or "")
    tender_id = str(meta.get("source_tender_id") or "")
    raw_dir = _scratch_dir(source, tender_id)

    docs_meta: List[Dict[str, Any]] = meta.get("documents", [])
    by_filename: Dict[str, Dict[str, Any]] = {}
    for document in docs_meta:
        filename = (document.get("filename") or "").strip()
        if filename:
            by_filename[filename] = document

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
                section = doc_sections.nth(i)

                info_btn = section.locator("button[title='Zobrazit detail']").first
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

                document = by_filename.get(canonical)
                if not document:
                    continue
                if document.get("storage_key") and document.get("sha256"):
                    continue

                dl_btn = section.locator("button[title='Stáhnout']").first
                if dl_btn.count() == 0:
                    continue

                dismiss_common_overlays(page)
                _wait_clickable(dl_btn, timeout_ms=15_000)

                with page.expect_download(timeout=90_000) as download_info:
                    try:
                        dl_btn.click(timeout=10_000)
                    except Exception:
                        dismiss_common_overlays(page)
                        dl_btn.click(timeout=10_000, force=True)

                download = download_info.value

                safe_name = sanitize_filename(canonical)
                if Path(safe_name).suffix == "":
                    suggested = download.suggested_filename or ""
                    ext = Path(suggested).suffix
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
                    except PWError as exc:
                        if "canceled" not in str(exc).lower():
                            raise
                        try:
                            if tmp.exists():
                                tmp.unlink()
                        except Exception:
                            pass

                        page.wait_for_timeout(700)
                        dismiss_common_overlays(page)
                        _wait_clickable(dl_btn, timeout_ms=15_000)

                        with page.expect_download(timeout=90_000) as retry_info:
                            try:
                                dl_btn.click(timeout=10_000)
                            except Exception:
                                dismiss_common_overlays(page)
                                dl_btn.click(timeout=10_000, force=True)

                        download = retry_info.value
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
                stored = persist_downloaded_file(
                    file_path=target,
                    source=source,
                    tender_id=tender_id,
                )

                document["storage_key"] = stored.storage_key
                document["storage_url"] = stored.storage_url
                document["size_bytes"] = int(size_bytes)
                document["sha256"] = sha
                document["mime_type"] = mime
                document["downloaded_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

                page.wait_for_timeout(400)

        finally:
            browser.close()

    upsert_tender_meta(meta)

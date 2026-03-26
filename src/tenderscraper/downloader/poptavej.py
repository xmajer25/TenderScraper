from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from playwright.sync_api import Page, sync_playwright

from tenderscraper.config import settings
from tenderscraper.repository import upsert_tender_meta
from tenderscraper.scraping.archives import extract_zip_archive, is_zip_file
from tenderscraper.scraping.auth.poptavej_auth import ensure_storage_state
from tenderscraper.scraping.files import guess_mime_type, sanitize_filename, sha256_file, unique_path
from tenderscraper.scraping.overlays import dismiss_common_overlays
from tenderscraper.storage.object_store import delete_stored_file, download_stored_file, persist_downloaded_file

ATTACH_LINKS_SEL = "div.main-text h4:has-text('Přílohy') ~ a[target='_blank'][href]"
_FILENAME_FROM_URL_RE = re.compile(r"/data/procurement/file/\d{4}/\d{2}/\d{2}/([^/?#]+)$", re.IGNORECASE)
logger = logging.getLogger(__name__)


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


def _normalize_document_urls(document: Dict[str, Any]) -> None:
    source_url = (document.get("source_url") or document.get("url") or "").strip() or None
    storage_key = (document.get("storage_key") or "").strip() or None
    storage_url = (document.get("storage_url") or "").strip() or None
    if not storage_url and storage_key:
        storage_url = settings.public_object_url(storage_key)

    if source_url:
        document["source_url"] = source_url
    if storage_url:
        document["storage_url"] = storage_url
        document["download_url"] = storage_url


def _download_client_from_storage_state(
    storage_state_path: Path | str,
    *,
    referer: str,
    timeout_ms: int,
) -> httpx.Client:
    payload = json.loads(Path(storage_state_path).read_text(encoding="utf-8"))
    cookies = httpx.Cookies()

    for cookie in payload.get("cookies") or []:
        name = (cookie.get("name") or "").strip()
        if not name:
            continue
        kwargs = {"path": (cookie.get("path") or "/")}
        domain = (cookie.get("domain") or "").lstrip(".")
        if domain:
            kwargs["domain"] = domain
        cookies.set(name, cookie.get("value") or "", **kwargs)

    return httpx.Client(
        timeout=max(timeout_ms / 1000, 1),
        follow_redirects=True,
        cookies=cookies,
        headers={
            "accept": "*/*",
            "referer": referer,
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
            ),
        },
    )


def _stream_download_to_file(
    client: httpx.Client,
    *,
    url: str,
    target_path: Path,
) -> httpx.Headers:
    with client.stream("GET", url) as response:
        response.raise_for_status()
        with target_path.open("wb") as fh:
            for chunk in response.iter_bytes():
                if chunk:
                    fh.write(chunk)
        return response.headers


def _document_has_stored_payload(document: Dict[str, Any]) -> bool:
    return bool(document.get("storage_key") and document.get("sha256"))


def _build_document_record(
    *,
    file_path: Path,
    source: str,
    tender_id: str,
    source_url: str,
    filename: str,
    mime_type: str | None = None,
) -> Dict[str, Any]:
    size_bytes = file_path.stat().st_size
    sha = sha256_file(file_path)
    resolved_mime = mime_type or guess_mime_type(file_path.name)
    stored = persist_downloaded_file(
        file_path=file_path,
        source=source,
        tender_id=tender_id,
    )
    storage_url = stored.storage_url or (
        settings.public_object_url(stored.storage_key) if stored.storage_key else None
    )
    return {
        "source_url": source_url,
        "url": source_url,
        "filename": filename,
        "mime_type": resolved_mime,
        "storage_key": stored.storage_key,
        "storage_url": storage_url,
        "download_url": storage_url,
        "size_bytes": int(size_bytes),
        "sha256": sha,
        "downloaded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def _extract_zip_documents(
    *,
    archive_path: Path,
    raw_dir: Path,
    source: str,
    tender_id: str,
    source_url: str,
) -> List[Dict[str, Any]]:
    extracted_dir = raw_dir / f"unzipped__{uuid.uuid4().hex}"
    extracted_paths = extract_zip_archive(archive_path=archive_path, output_dir=extracted_dir)
    documents: List[Dict[str, Any]] = []

    for extracted_path in extracted_paths:
        documents.append(
            _build_document_record(
                file_path=extracted_path,
                source=source,
                tender_id=tender_id,
                source_url=source_url,
                filename=extracted_path.name,
            )
        )

    return documents


def _persist_downloaded_attachment(
    *,
    file_path: Path,
    raw_dir: Path,
    source: str,
    tender_id: str,
    source_url: str,
    filename: str,
    mime_type: str | None = None,
) -> List[Dict[str, Any]]:
    if is_zip_file(file_path):
        try:
            extracted_docs = _extract_zip_documents(
                archive_path=file_path,
                raw_dir=raw_dir,
                source=source,
                tender_id=tender_id,
                source_url=source_url,
            )
            if extracted_docs:
                file_path.unlink(missing_ok=True)
                return extracted_docs
        except Exception as exc:
            logger.warning("Poptavej ZIP extraction failed for %s: %s", file_path.name, exc)

    return [
        _build_document_record(
            file_path=file_path,
            source=source,
            tender_id=tender_id,
            source_url=source_url,
            filename=filename,
            mime_type=mime_type,
        )
    ]


def _document_is_zip(document: Dict[str, Any]) -> bool:
    filename = (document.get("filename") or "").strip().lower()
    mime_type = (document.get("mime_type") or "").strip().lower()
    storage_key = (document.get("storage_key") or "").strip().lower()
    source_url = (document.get("source_url") or document.get("url") or "").strip().lower()
    return (
        filename.endswith(".zip")
        or storage_key.endswith(".zip")
        or source_url.endswith(".zip")
        or "zip" in mime_type
    )


def backfill_poptavej_zip_documents(*, meta: Dict[str, Any]) -> Dict[str, int]:
    source = str(meta.get("source") or "")
    tender_id = str(meta.get("source_tender_id") or "")
    raw_dir = _scratch_dir(source, tender_id)

    docs_meta: List[Dict[str, Any]] = meta.get("documents", []) or []
    if not docs_meta:
        return {"archives_expanded": 0, "documents_uploaded": 0}

    archives_expanded = 0
    documents_uploaded = 0
    changed = False
    storage_keys_to_delete: List[str] = []
    updated_documents: List[Dict[str, Any]] = []

    for document in docs_meta:
        _normalize_document_urls(document)
        if not _document_is_zip(document):
            updated_documents.append(document)
            continue

        storage_key = (document.get("storage_key") or "").strip()
        if not storage_key or not settings.uses_s3_storage:
            updated_documents.append(document)
            continue

        archive_name = sanitize_filename(
            (document.get("filename") or "").strip() or Path(storage_key).name or "attachment.zip"
        )
        archive_path = unique_path(raw_dir / archive_name)
        source_url = (document.get("source_url") or document.get("url") or "").strip()

        try:
            download_stored_file(storage_key=storage_key, target_path=archive_path)
            extracted_docs = _extract_zip_documents(
                archive_path=archive_path,
                raw_dir=raw_dir,
                source=source,
                tender_id=tender_id,
                source_url=source_url,
            )
        except Exception as exc:
            logger.warning("Poptavej ZIP backfill failed for %s: %s", storage_key, exc)
            updated_documents.append(document)
            continue
        finally:
            archive_path.unlink(missing_ok=True)

        if not extracted_docs:
            updated_documents.append(document)
            continue

        updated_documents.extend(extracted_docs)
        documents_uploaded += len(extracted_docs)
        archives_expanded += 1
        storage_keys_to_delete.append(storage_key)
        changed = True

    if not changed:
        return {
            "archives_expanded": 0,
            "documents_uploaded": 0,
        }

    meta["documents"] = updated_documents
    upsert_tender_meta(meta)

    for storage_key in storage_keys_to_delete:
        try:
            delete_stored_file(storage_key=storage_key)
        except Exception as exc:
            logger.warning("Poptavej ZIP delete failed for %s: %s", storage_key, exc)

    return {
        "archives_expanded": archives_expanded,
        "documents_uploaded": documents_uploaded,
    }


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

    with _download_client_from_storage_state(
        storage_state,
        referer=str(notice_url),
        timeout_ms=timeout_ms,
    ) as download_client, sync_playwright() as p:
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

            docs_by_source_url: Dict[str, List[Dict[str, Any]]] = {}
            docs_by_filename: Dict[str, List[Dict[str, Any]]] = {}
            for document in docs_meta:
                _normalize_document_urls(document)
                source_url = (document.get("source_url") or document.get("url") or "").strip()
                filename = (document.get("filename") or "").strip()
                if source_url:
                    docs_by_source_url.setdefault(source_url, []).append(document)
                if filename:
                    docs_by_filename.setdefault(filename, []).append(document)

            used_document_ids: set[int] = set()
            updated_documents: List[Dict[str, Any]] = []

            for i, attachment in enumerate(attachments):
                existing_documents = docs_by_source_url.get(attachment.url) or docs_by_filename.get(attachment.filename) or []
                existing_documents = [document for document in existing_documents if id(document) not in used_document_ids]
                if not existing_documents and 0 <= i < len(docs_meta) and id(docs_meta[i]) not in used_document_ids:
                    existing_documents = [docs_meta[i]]

                if existing_documents and all(_document_has_stored_payload(document) for document in existing_documents):
                    updated_documents.extend(existing_documents)
                    used_document_ids.update(id(document) for document in existing_documents)
                    continue

                server_name = sanitize_filename(attachment.filename)
                if not Path(server_name).suffix:
                    url_filename = _filename_from_poptavej_url(attachment.url) or ""
                    ext = Path(url_filename).suffix
                    if ext:
                        server_name = server_name + ext

                target = unique_path(raw_dir / server_name)
                tmp = _safe_tmp_path(raw_dir)

                try:
                    headers = _stream_download_to_file(
                        download_client,
                        url=attachment.url,
                        target_path=tmp,
                    )
                except Exception:
                    tmp.unlink(missing_ok=True)
                    updated_documents.extend(existing_documents)
                    used_document_ids.update(id(document) for document in existing_documents)
                    continue

                try:
                    tmp.replace(target)
                except Exception:
                    with tmp.open("rb") as src, target.open("wb") as dst:
                        while True:
                            chunk = src.read(1024 * 1024)
                            if not chunk:
                                break
                            dst.write(chunk)
                    tmp.unlink(missing_ok=True)

                mime = headers.get("content-type") or guess_mime_type(target.name)
                uploaded_documents = _persist_downloaded_attachment(
                    file_path=target,
                    raw_dir=raw_dir,
                    source=source,
                    tender_id=tender_id,
                    source_url=attachment.url,
                    filename=attachment.filename,
                    mime_type=mime,
                )

                updated_documents.extend(uploaded_documents)
                used_document_ids.update(id(document) for document in existing_documents)
                page.wait_for_timeout(250)

            for document in docs_meta:
                if id(document) in used_document_ids:
                    continue
                updated_documents.append(document)

            meta["documents"] = updated_documents

        finally:
            try:
                context.close()
            except Exception:
                pass
            browser.close()

    upsert_tender_meta(meta)

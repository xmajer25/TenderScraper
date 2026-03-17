from __future__ import annotations

import logging
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import httpx

from tenderscraper.config import settings
from tenderscraper.repository import upsert_tender_meta
from tenderscraper.scraping.files import guess_mime_type, sanitize_filename, sha256_file, unique_path
from tenderscraper.storage.object_store import persist_downloaded_file

TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
FILENAME_RE = re.compile(r'filename="?([^";]+)"?')
logger = logging.getLogger(__name__)


def _scratch_dir(source: str, tender_id: str) -> Path:
    path = settings.scratch_dir / f"source={source}" / f"tender={tender_id}" / "raw"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_tmp_path(raw_dir: Path) -> Path:
    return raw_dir / f"__tmp__{uuid.uuid4().hex}"


def _download_client() -> httpx.Client:
    return httpx.Client(
        timeout=60,
        headers={
            "accept": "application/octet-stream",
            "accept-language": "en-US,en;q=0.9",
            "origin": "https://tenderarena.cz",
            "referer": "https://tenderarena.cz/",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
        },
        follow_redirects=True,
    )


def _download_bytes(url: str, *, max_attempts: int = 4) -> tuple[bytes, httpx.Headers]:
    delay_s = 1.0
    last_error: Exception | None = None

    with _download_client() as client:
        for attempt in range(1, max_attempts + 1):
            try:
                response = client.get(url)
                if response.status_code in TRANSIENT_STATUS_CODES:
                    raise httpx.HTTPStatusError(
                        f"Transient response {response.status_code} for {url}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                return response.content, response.headers
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
            except httpx.HTTPStatusError as exc:
                last_error = exc
                status_code = exc.response.status_code if exc.response is not None else None
                if status_code not in TRANSIENT_STATUS_CODES:
                    raise
                retry_after = (exc.response.headers.get("retry-after") or "").strip()
                if retry_after.isdigit():
                    delay_s = max(delay_s, float(retry_after))

            if attempt == max_attempts:
                break

            time.sleep(delay_s)
            delay_s = min(delay_s * 2, 8.0)

    if last_error is not None:
        raise last_error
    raise RuntimeError(f"Failed to download TenderArena document from {url}")


def _filename_from_headers(headers: httpx.Headers) -> str | None:
    disposition = headers.get("content-disposition") or ""
    match = FILENAME_RE.search(disposition)
    if match:
        return match.group(1).strip()
    return None


def download_tender_arena_docs(*, meta: Dict[str, Any]) -> None:
    source = str(meta.get("source") or "")
    tender_id = str(meta.get("source_tender_id") or "")
    raw_dir = _scratch_dir(source, tender_id)

    docs_meta: List[Dict[str, Any]] = meta.get("documents", [])
    if not docs_meta:
        return

    for document in docs_meta:
        if document.get("storage_key") and document.get("sha256"):
            continue

        download_url = (document.get("url") or "").strip()
        filename = (document.get("filename") or "").strip()
        if not download_url or not filename:
            continue

        try:
            payload, headers = _download_bytes(download_url)
        except Exception as exc:
            logger.warning("TenderArena document download failed for %s: %s", download_url, exc)
            continue

        header_filename = _filename_from_headers(headers)
        safe_name = sanitize_filename(header_filename or filename)
        target = unique_path(raw_dir / safe_name)
        tmp = _safe_tmp_path(raw_dir)
        tmp.write_bytes(payload)

        try:
            tmp.replace(target)
        except Exception:
            data = tmp.read_bytes()
            target.write_bytes(data)
            tmp.unlink(missing_ok=True)

        size_bytes = target.stat().st_size
        sha = sha256_file(target)
        mime = headers.get("content-type") or guess_mime_type(target.name)
        stored = persist_downloaded_file(
            file_path=target,
            source=source,
            tender_id=tender_id,
        )

        document["filename"] = header_filename or filename
        document["storage_key"] = stored.storage_key
        document["storage_url"] = stored.storage_url
        document["size_bytes"] = int(size_bytes)
        document["sha256"] = sha
        document["mime_type"] = mime
        document["downloaded_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    upsert_tender_meta(meta)

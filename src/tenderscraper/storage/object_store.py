from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import boto3
from botocore.client import Config

from tenderscraper.config import settings


@dataclass(frozen=True)
class StoredDocument:
    storage_key: str | None
    storage_url: str | None


def _s3_client():
    settings.require_s3_settings()
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url or None,
        region_name=settings.s3_region or None,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def build_storage_key(*, source: str, tender_id: str, filename: str) -> str:
    return f"source={source}/tender={tender_id}/raw/{filename}"


def persist_downloaded_file(
    *,
    file_path: Path,
    source: str,
    tender_id: str,
) -> StoredDocument:
    if not settings.uses_s3_storage:
        return StoredDocument(
            storage_key=None,
            storage_url=None,
        )

    key = build_storage_key(source=source, tender_id=tender_id, filename=file_path.name)
    client = _s3_client()
    client.upload_file(str(file_path), settings.s3_bucket, key)
    public_url = settings.public_object_url(key)
    file_path.unlink(missing_ok=True)
    return StoredDocument(storage_key=key, storage_url=public_url)


def download_stored_file(*, storage_key: str, target_path: Path) -> None:
    if not settings.uses_s3_storage:
        raise ValueError("download_stored_file is only available when STORAGE_BACKEND=s3")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    client = _s3_client()
    client.download_file(settings.s3_bucket, storage_key, str(target_path))


def delete_stored_file(*, storage_key: str) -> None:
    if not settings.uses_s3_storage:
        return

    client = _s3_client()
    client.delete_object(Bucket=settings.s3_bucket, Key=storage_key)


def generate_download_url(storage_key: str) -> str:
    if not settings.uses_s3_storage:
        raise ValueError("generate_download_url is only available when STORAGE_BACKEND=s3")
    client = _s3_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket, "Key": storage_key},
        ExpiresIn=settings.s3_presign_expiry_s,
    )

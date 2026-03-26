from __future__ import annotations

import zipfile
from pathlib import Path
from typing import List

from tenderscraper.scraping.files import sanitize_filename, unique_path

_SKIP_BASENAMES = {".ds_store", "thumbs.db"}
_SKIP_PREFIXES = ("__macosx/",)


def is_zip_file(path: Path) -> bool:
    try:
        return zipfile.is_zipfile(path)
    except OSError:
        return False


def extract_zip_archive(*, archive_path: Path, output_dir: Path) -> List[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    extracted: List[Path] = []

    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue

            member_name = (info.filename or "").replace("\\", "/").strip()
            if not member_name:
                continue

            lowered = member_name.lower()
            if lowered.startswith(_SKIP_PREFIXES):
                continue

            safe_name = sanitize_filename(Path(member_name).name)
            if not safe_name or safe_name.lower() in _SKIP_BASENAMES:
                continue

            target_path = unique_path(output_dir / safe_name)
            with archive.open(info, "r") as src, target_path.open("wb") as dst:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    dst.write(chunk)

            extracted.append(target_path)

    return extracted

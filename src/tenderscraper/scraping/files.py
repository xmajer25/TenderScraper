from __future__ import annotations

import hashlib
import mimetypes
import re
from pathlib import Path


def sanitize_filename(name: str) -> str:
    name = name.strip().replace("\xa0", " ")
    name = re.sub(r"\s+", " ", name).strip()
    # Keep extension, sanitize everything else
    name = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", name)
    return name[:180] if len(name) > 180 else name


def unique_path(target: Path) -> Path:
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    parent = target.parent
    for i in range(1, 10_000):
        candidate = parent / f"{stem}__{i}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not create unique filename for: {target}")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def guess_mime_type(filename: str) -> str | None:
    mt, _ = mimetypes.guess_type(filename)
    return mt

from __future__ import annotations

import re
from pathlib import Path


def _safe(s: str) -> str:
    s = s.strip()
    s = re.sub(r"[^a-zA-Z0-9._=-]+", "_", s)
    return s[:120] if len(s) > 120 else s


def tender_root(tenders_dir: Path, *, source: str, tender_key: str) -> Path:
    return tenders_dir / f"source={_safe(source)}" / f"tender={_safe(tender_key)}"


def raw_dir(tenders_dir: Path, *, source: str, tender_key: str) -> Path:
    return tender_root(tenders_dir, source=source, tender_key=tender_key) / "raw"


def normalized_dir(tenders_dir: Path, *, source: str, tender_key: str) -> Path:
    return tender_root(tenders_dir, source=source, tender_key=tender_key) / "normalized"

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List

from tenderscraper.connectors.base import TenderNotice
from tenderscraper.storage.layout import normalized_dir, raw_dir, tender_root
from tenderscraper.downloader.tender_arena import download_tender_arena_docs
from tenderscraper.downloader.poptavej import download_poptavej_docs

def write_tender(tenders_dir: Path, tender: TenderNotice) -> Path:
    tdir = tender_root(tenders_dir, source=tender.source, tender_key=tender.tender_key)
    rdir = raw_dir(tenders_dir, source=tender.source, tender_key=tender.tender_key)
    ndir = normalized_dir(tenders_dir, source=tender.source, tender_key=tender.tender_key)

    rdir.mkdir(parents=True, exist_ok=True)
    ndir.mkdir(parents=True, exist_ok=True)

    meta = tender.model_dump(mode="json")
    meta["_ingested_at"] = datetime.now(timezone.utc).isoformat()
    meta_path = tdir / "meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    return tdir


def ingest_all(*, tenders_dir: Path, tenders: Iterable[TenderNotice]) -> List[Path]:
    out: List[Path] = []
    for t in tenders:
        out.append(write_tender(tenders_dir, t))
    return out

def download_docs_for_ingested_tenders(tender_dirs: List[Path]) -> None:
    for tdir in tender_dirs:
        meta_path = tdir / "meta.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        src = meta.get("source")

        if src == "tender_arena":
            download_tender_arena_docs(meta_path=meta_path)

        elif src == "poptavej":
            download_poptavej_docs(meta_path=meta_path)

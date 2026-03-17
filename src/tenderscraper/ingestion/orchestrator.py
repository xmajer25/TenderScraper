from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, List, Tuple

from tenderscraper.connectors.base import TenderNotice
from tenderscraper.db import create_db_and_tables
from tenderscraper.repository import get_tender_meta, upsert_tender_meta

TenderRef = Tuple[str, str]


def write_tender(tender: TenderNotice) -> TenderRef:
    meta = tender.model_dump(mode="json")
    meta["_ingested_at"] = datetime.now(timezone.utc).isoformat()
    create_db_and_tables()
    upsert_tender_meta(meta)
    return (tender.source, tender.tender_key)


def ingest_all(*, tenders: Iterable[TenderNotice]) -> List[TenderRef]:
    out: List[TenderRef] = []
    for tender in tenders:
        out.append(write_tender(tender))
    return out


def download_docs_for_ingested_tenders(tender_refs: List[TenderRef]) -> None:
    for source, tender_id in tender_refs:
        meta = get_tender_meta(source, tender_id)
        if not meta:
            continue

        if source == "tender_arena":
            from tenderscraper.downloader.tender_arena import download_tender_arena_docs

            download_tender_arena_docs(meta=meta)
        elif source == "poptavej":
            from tenderscraper.downloader.poptavej import download_poptavej_docs

            download_poptavej_docs(meta=meta)

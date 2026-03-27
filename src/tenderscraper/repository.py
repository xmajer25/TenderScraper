from __future__ import annotations

from datetime import date as dt_date, datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, or_
from sqlmodel import Session, select

from tenderscraper.db import create_db_and_tables, session_scope
from tenderscraper.db_models import TenderRecord


def _winner_key_expr():
    return func.coalesce(
        func.nullif(TenderRecord.winner_ic, ""),
        func.nullif(TenderRecord.winner_name, ""),
    )


def _parse_datetime(value: Any) -> Optional[datetime]:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        normalized = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None
    return None


def _parse_date(value: Any) -> Optional[dt_date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, dt_date):
        return value
    if isinstance(value, str) and value:
        normalized = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized).date()
        except ValueError:
            try:
                return dt_date.fromisoformat(value)
            except ValueError:
                return None
    return None


def _record_from_meta(record: TenderRecord, meta: Dict[str, Any]) -> TenderRecord:
    record.source = str(meta.get("source") or "")
    record.source_tender_id = str(meta.get("source_tender_id") or "")
    record.title = str(meta.get("title") or "Unknown title")
    record.date = _parse_date(meta.get("date"))
    record.price = meta.get("price")
    record.original_url = meta.get("original_url")
    record.winner_name = meta.get("winner_name")
    record.winner_ic = meta.get("winner_ic")
    record.buyer = meta.get("buyer")
    record.buyer_ico = meta.get("buyer_ico")
    record.description = meta.get("description")
    record.submission_deadline_at = _parse_datetime(meta.get("submission_deadline_at"))
    record.bids_opening_at = _parse_datetime(meta.get("bids_opening_at"))
    record.notice_url = meta.get("notice_url")
    record.ingested_at = _parse_datetime(meta.get("_ingested_at"))
    record.meta_json = meta
    return record


def upsert_tender_meta(meta: Dict[str, Any], *, session: Session | None = None) -> None:
    source = str(meta.get("source") or "")
    source_tender_id = str(meta.get("source_tender_id") or "")
    if not source or not source_tender_id:
        raise ValueError("Tender metadata must include source and source_tender_id")

    def _execute(db: Session) -> None:
        statement = select(TenderRecord).where(
            TenderRecord.source == source,
            TenderRecord.source_tender_id == source_tender_id,
        )
        record = db.exec(statement).one_or_none()
        if record is None:
            record = TenderRecord()
            db.add(record)
        _record_from_meta(record, meta)
        db.commit()

    if session is not None:
        _execute(session)
        return

    with session_scope() as db:
        _execute(db)


def get_tender_meta(source: str, tender_id: str) -> Optional[Dict[str, Any]]:
    with session_scope() as db:
        statement = select(TenderRecord).where(
            TenderRecord.source == source,
            TenderRecord.source_tender_id == tender_id,
        )
        record = db.exec(statement).one_or_none()
        return dict(record.meta_json) if record else None


def list_sources() -> List[str]:
    with session_scope() as db:
        rows = db.exec(select(TenderRecord.source).distinct()).all()
        return sorted(str(row) for row in rows)


def list_tender_refs(*, source: str | None = None, limit: int | None = None) -> List[Tuple[str, str]]:
    with session_scope() as db:
        statement = select(TenderRecord.source, TenderRecord.source_tender_id).order_by(TenderRecord.ingested_at.desc())
        if source:
            statement = statement.where(TenderRecord.source == source)
        if limit is not None:
            statement = statement.limit(limit)
        rows = db.exec(statement).all()
        return [(str(row[0]), str(row[1])) for row in rows]


def list_tenders(
    *,
    source: str | None = None,
    q: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[int, List[Dict[str, Any]]]:
    with session_scope() as db:
        statement = select(TenderRecord)
        count_statement = select(func.count()).select_from(TenderRecord)

        if source:
            statement = statement.where(TenderRecord.source == source)
            count_statement = count_statement.where(TenderRecord.source == source)

        if q:
            pattern = f"%{q.strip()}%"
            predicate = or_(
                TenderRecord.title.ilike(pattern),
                TenderRecord.description.ilike(pattern),
            )
            statement = statement.where(predicate)
            count_statement = count_statement.where(predicate)

        statement = statement.order_by(TenderRecord.ingested_at.desc()).offset(offset).limit(limit)
        total = int(db.exec(count_statement).one())
        items = [dict(record.meta_json) for record in db.exec(statement).all()]
        return total, items


def get_db_stats() -> Dict[str, Any]:
    with session_scope() as db:
        records = db.exec(select(TenderRecord)).all()
        by_source: Dict[str, int] = {}
        documents_total = 0
        tenders_with_documents = 0

        for record in records:
            by_source[record.source] = by_source.get(record.source, 0) + 1
            documents = record.meta_json.get("documents") or []
            documents_total += len(documents)
            if documents:
                tenders_with_documents += 1

        return {
            "total_tenders": len(records),
            "documents_total": documents_total,
            "tenders_with_documents": tenders_with_documents,
            "by_source": dict(sorted(by_source.items())),
        }


def list_distinct_winners(
    *,
    source: str | None = "poptavej",
    q: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[int, List[Dict[str, Any]]]:
    winner_key = _winner_key_expr()

    with session_scope() as db:
        statement = (
            select(
                winner_key.label("winner"),
                func.max(func.nullif(TenderRecord.winner_name, "")).label("winner_name"),
                func.max(func.nullif(TenderRecord.winner_ic, "")).label("winner_ic"),
                func.count().label("tender_count"),
            )
            .where(winner_key.isnot(None))
        )
        count_statement = select(func.count(func.distinct(winner_key))).where(winner_key.isnot(None))

        if source:
            statement = statement.where(TenderRecord.source == source)
            count_statement = count_statement.where(TenderRecord.source == source)

        if q:
            pattern = f"%{q.strip()}%"
            predicate = or_(
                TenderRecord.winner_name.ilike(pattern),
                TenderRecord.winner_ic.ilike(pattern),
            )
            statement = statement.where(predicate)
            count_statement = count_statement.where(predicate)

        statement = (
            statement.group_by(winner_key)
            .order_by(func.count().desc(), winner_key.asc())
            .offset(offset)
            .limit(limit)
        )

        total = int(db.exec(count_statement).one() or 0)
        rows = db.exec(statement).all()
        items = [
            {
                "winner": str(row[0]),
                "winner_name": row[1],
                "winner_ic": row[2],
                "tender_count": int(row[3]),
            }
            for row in rows
        ]
        return total, items


def get_winner_tender_count(*, winner: str, source: str | None = "poptavej") -> Optional[Dict[str, Any]]:
    winner = winner.strip()
    if not winner:
        return None

    winner_key = _winner_key_expr()

    with session_scope() as db:
        statement = (
            select(
                winner_key.label("winner"),
                func.max(func.nullif(TenderRecord.winner_name, "")).label("winner_name"),
                func.max(func.nullif(TenderRecord.winner_ic, "")).label("winner_ic"),
                func.count().label("tender_count"),
            )
            .where(winner_key == winner)
        )

        if source:
            statement = statement.where(TenderRecord.source == source)

        statement = statement.group_by(winner_key)
        row = db.exec(statement).one_or_none()
        if row is None:
            return None

        return {
            "winner": str(row[0]),
            "winner_name": row[1],
            "winner_ic": row[2],
            "tender_count": int(row[3]),
        }

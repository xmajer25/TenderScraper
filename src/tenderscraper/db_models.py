from __future__ import annotations

from datetime import date as dt_date, datetime
from typing import Any, Dict, Optional

from sqlalchemy import Column, UniqueConstraint
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel


class TenderRecord(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("source", "source_tender_id", name="uq_tender_source_and_id"),)

    id: int | None = Field(default=None, primary_key=True)
    source: str = Field(index=True)
    source_tender_id: str = Field(index=True)
    title: str = Field(index=True)
    date: Optional[dt_date] = None
    price: Optional[str] = None
    original_url: Optional[str] = None
    winner_name: Optional[str] = Field(default=None, index=True)
    winner_ic: Optional[str] = Field(default=None, index=True)
    buyer: Optional[str] = None
    buyer_ico: Optional[str] = None
    description: Optional[str] = None
    submission_deadline_at: Optional[datetime] = None
    bids_opening_at: Optional[datetime] = None
    notice_url: Optional[str] = None
    ingested_at: Optional[datetime] = Field(default=None, index=True)
    meta_json: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))

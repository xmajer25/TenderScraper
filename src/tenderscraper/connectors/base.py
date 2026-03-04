from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class TenderDocument(BaseModel):
    # Provenance / source
    url: str

    # Canonical filename from modal ("Název souboru")
    filename: str

    # Filled after download
    mime_type: Optional[str] = None
    local_path: Optional[str] = None       # relative to tender folder
    size_bytes: Optional[int] = None
    sha256: Optional[str] = None
    downloaded_at: Optional[datetime] = None


class TenderNotice(BaseModel):
    source: str
    source_tender_id: str
    title: str

    buyer: Optional[str] = None
    buyer_ico: Optional[str] = None

    description: Optional[str] = None

    submission_deadline_at: Optional[datetime] = None
    bids_opening_at: Optional[datetime] = None

    notice_url: Optional[str] = None
    documents: List[TenderDocument] = Field(default_factory=list)

    @property
    def tender_key(self) -> str:
        return self.source_tender_id


class BaseConnector(ABC):
    source: str

    @abstractmethod
    def fetch(self, *, query: str | None = None, limit: int = 10) -> List[TenderNotice]:
        raise NotImplementedError

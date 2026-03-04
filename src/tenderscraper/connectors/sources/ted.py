from __future__ import annotations

from datetime import datetime, timezone

from tenderscraper.connectors.base import BaseConnector, TenderDocument, TenderNotice


class TedConnector(BaseConnector):
    source = "ted"

    def fetch(self, *, query: str | None = None, limit: int = 10):
        now = datetime.now(timezone.utc)

        items = [
            TenderNotice(
                source=self.source,
                source_tender_id="TED-2026-000001",
                title="IT Services Framework Agreement",
                buyer="Ministry of Something",
                published_at=now,
                deadline_at=now,
                notice_url="https://example.com/ted/000001",
                documents=[
                    TenderDocument(
                        url="https://example.com/ted/000001/spec.pdf",
                        filename="specification.pdf",
                        mime_type="application/pdf",
                    ),
                    TenderDocument(
                        url="https://example.com/ted/000001/annex.docx",
                        filename="annex.docx",
                        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    ),
                ],
            ),
            TenderNotice(
                source=self.source,
                source_tender_id="TED-2026-000002",
                title="Cloud Migration Services",
                buyer="City Council",
                published_at=now,
                deadline_at=now,
                notice_url="https://example.com/ted/000002",
                documents=[],
            ),
        ]

        return items[:limit]

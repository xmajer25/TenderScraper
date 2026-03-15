from __future__ import annotations

import logging

from tenderscraper.connectors.base import BaseConnector, TenderDocument, TenderNotice
from tenderscraper.scraping.sources.tender_arena import TenderArenaScraper

logger = logging.getLogger(__name__)


class TenderArenaConnector(BaseConnector):
    source = "tender_arena"

    def fetch(self, *, query: str | None = None, limit: int = 10):
        scraper = TenderArenaScraper()

        try:
            urls = scraper.fetch_tender_urls(limit=limit, headless=True, timeout_ms=30_000)
        except Exception as exc:
            logger.warning("TenderArena listing fetch failed: %s", exc)
            return []

        tenders: list[TenderNotice] = []

        for url in urls:
            try:
                detail = scraper.fetch_detail(url=url, headless=True, include_docs=True)
            except Exception as exc:
                logger.warning("TenderArena detail fetch failed for %s: %s", url, exc)
                continue

            source_id = url.rstrip("/").split("/")[-1]
            title = detail.title or "Unknown title"

            docs: list[TenderDocument] = []
            for d in detail.docs:
                if d.filename:
                    docs.append(
                        TenderDocument(
                            url=url,  # provenance; real download URL will come later
                            filename=d.filename,
                            mime_type=None,
                        )
                    )

            t = TenderNotice(
                source=self.source,
                source_tender_id=source_id,
                title=title,
                buyer=detail.buyer_name,
                buyer_ico=detail.buyer_ico,
                description=detail.description,
                submission_deadline_at=detail.submission_deadline_at,
                bids_opening_at=detail.bids_opening_at,
                notice_url=url,
                documents=docs,
            )

            tenders.append(t)

        return tenders

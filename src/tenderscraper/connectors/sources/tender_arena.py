from __future__ import annotations

import logging

from tenderscraper.connectors.base import BaseConnector, TenderDocument, TenderNotice
from tenderscraper.scraping.sources.tender_arena import TenderArenaScraper

logger = logging.getLogger(__name__)


class TenderArenaConnector(BaseConnector):
    source = "tender_arena"
    OVERFETCH_MULTIPLIER = 3
    MAX_LISTING_SCAN = 120

    def fetch(self, *, query: str | None = None, limit: int = 10):
        tenders: list[TenderNotice] = []
        listing_limit = min(max(limit * self.OVERFETCH_MULTIPLIER, limit), self.MAX_LISTING_SCAN)

        with TenderArenaScraper(timeout_ms=30_000, request_pause_s=0.6) as scraper:
            try:
                items = scraper.fetch_listing(limit=listing_limit)
            except Exception as exc:
                logger.warning("TenderArena listing fetch failed: %s", exc)
                return []

            for item in items:
                if len(tenders) >= limit:
                    break

                detail_payload: dict = {}
                try:
                    ai_payload = scraper.fetch_ai_summary(tender_id=item.tender_id, timeout_ms=30_000)
                except Exception as exc:
                    try:
                        detail_payload = scraper.fetch_detail(tender_id=item.tender_id, timeout_ms=30_000)
                        ai_payload = None
                    except Exception as fallback_exc:
                        logger.warning(
                            "TenderArena summary/detail fetch failed for %s: %s | fallback: %s",
                            item.notice_url,
                            exc,
                            fallback_exc,
                        )
                        continue

                detail = scraper.build_detail(
                    listing_item=item,
                    detail_payload=detail_payload,
                    ai_payload=ai_payload,
                    profile_payload=None,
                )
                title = detail.title or "Unknown title"

                docs: list[TenderDocument] = []
                for d in detail.docs:
                    if d.filename:
                        docs.append(
                            TenderDocument(
                                url=d.download_url or item.notice_url,
                                filename=d.filename,
                                mime_type=None,
                            )
                        )

                t = TenderNotice(
                    source=self.source,
                    source_tender_id=item.source_tender_id,
                    title=title,
                    buyer=detail.buyer_name,
                    buyer_ico=detail.buyer_ico,
                    description=detail.description,
                    submission_deadline_at=detail.submission_deadline_at,
                    bids_opening_at=detail.bids_opening_at,
                    notice_url=item.notice_url,
                    documents=docs,
                )

                tenders.append(t)

        return tenders

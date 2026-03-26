from __future__ import annotations

import logging

from tenderscraper.connectors.base import BaseConnector, TenderDocument, TenderNotice
from tenderscraper.scraping.files import guess_mime_type
from tenderscraper.scraping.sources.tender_arena import TenderArenaScraper

logger = logging.getLogger(__name__)


class TenderArenaConnector(BaseConnector):
    source = "tender_arena"
    OVERFETCH_MULTIPLIER = 3

    def fetch(self, *, query: str | None = None, limit: int | None = 10):
        tenders: list[TenderNotice] = []
        listing_limit = (
            None
            if limit is None
            else max(limit * self.OVERFETCH_MULTIPLIER, limit)
        )

        with TenderArenaScraper(timeout_ms=30_000, request_pause_s=1.0) as scraper:
            try:
                items = scraper.fetch_listing(limit=listing_limit)
            except Exception as exc:
                logger.warning("TenderArena listing fetch failed: %s", exc)
                return []

            for item in items:
                if limit is not None and len(tenders) >= limit:
                    break

                detail_payload: dict = {}
                ai_payload: dict | None = None
                try:
                    detail_payload = scraper.fetch_detail(
                        tender_id=item.tender_id,
                        timeout_ms=30_000,
                        max_attempts=2,
                    )
                except Exception as exc:
                    message = str(exc)
                    if "429" not in message:
                        logger.warning("TenderArena detail fetch failed for %s: %s", item.notice_url, exc)
                    tenders.append(self._build_listing_only_notice(item))
                    continue

                if not scraper.detail_has_description(detail_payload) or not scraper.detail_has_docs(
                    detail_payload
                ):
                    try:
                        ai_payload = scraper.fetch_ai_summary(
                            tender_id=item.tender_id,
                            timeout_ms=30_000,
                            max_attempts=1,
                        )
                    except Exception:
                        ai_payload = None

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
                                mime_type=guess_mime_type(d.filename),
                            )
                        )

                t = TenderNotice(
                    source=self.source,
                    source_tender_id=item.source_tender_id,
                    title=title,
                    date=None,
                    price=None,
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

    def _build_listing_only_notice(self, item) -> TenderNotice:
        return TenderNotice(
            source=self.source,
            source_tender_id=item.source_tender_id,
            title=item.title or "Unknown title",
            date=None,
            price=None,
            buyer=item.buyer_name,
            buyer_ico=None,
            description=None,
            submission_deadline_at=item.submission_deadline_at,
            bids_opening_at=None,
            notice_url=item.notice_url,
            documents=[],
        )

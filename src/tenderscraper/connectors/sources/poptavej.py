from __future__ import annotations

from tenderscraper.connectors.base import BaseConnector, TenderDocument, TenderNotice
from tenderscraper.scraping.sources.poptavej import PoptavejScraper


class PoptavejConnector(BaseConnector):
    source = "poptavej"

    def fetch(self, *, query: str | None = None, limit: int = 10):
        """
        Public-only scraping:
        - Listing gives us: id, title, url, and (sometimes) ukončení as absolute date.
        - Detail gives us: description + attachment filenames (no download URLs public).
        """
        scraper = PoptavejScraper()

        items = scraper.fetch_listing(limit=limit, headless=True, timeout_ms=30_000)
        tenders: list[TenderNotice] = []

        for it in items:
            detail = scraper.fetch_detail(notice_url=it.notice_url, headless=True, timeout_ms=30_000)

            docs: list[TenderDocument] = []
            for fn in detail.attachment_filenames:
                docs.append(
                    TenderDocument(
                        url=it.notice_url,  # provenance only; downloads are behind registration
                        filename=fn,
                        mime_type=None,
                    )
                )

            t = TenderNotice(
                source=self.source,
                source_tender_id=it.source_tender_id,
                title=detail.title or it.title or "Unknown title",
                buyer=None,  # public pages don’t expose zadavatel reliably
                buyer_ico=None,
                description=detail.description_text or None,
                submission_deadline_at=it.closing_at,  # best-effort (only if absolute date was present)
                bids_opening_at=None,
                notice_url=it.notice_url,
                documents=docs,
            )
            tenders.append(t)

        return tenders

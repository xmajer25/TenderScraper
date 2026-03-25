from __future__ import annotations

from tenderscraper.config import settings
from tenderscraper.connectors.base import BaseConnector, TenderDocument, TenderNotice
from tenderscraper.scraping.auth.poptavej_auth import ensure_storage_state
from tenderscraper.scraping.files import guess_mime_type
from tenderscraper.scraping.sources.poptavej import PoptavejScraper


class PoptavejConnector(BaseConnector):
    source = "poptavej"

    def fetch(self, *, query: str | None = None, limit: int | None = 10):
        scraper = PoptavejScraper()
        storage_state_path = None

        if settings.poptavej_username and settings.poptavej_password:
            storage_state_path = ensure_storage_state(
                headless=True,
                timeout_ms=30_000,
                force_relogin=False,
            )

        items = scraper.fetch_listing(limit=limit, headless=True, timeout_ms=30_000)
        tenders: list[TenderNotice] = []

        for item in items:
            detail = scraper.fetch_detail(
                notice_url=item.notice_url,
                storage_state_path=storage_state_path,
                headless=True,
                timeout_ms=30_000,
            )

            documents = [
                TenderDocument(
                    url=item.notice_url,
                    filename=filename,
                    mime_type=guess_mime_type(filename),
                )
                for filename in detail.attachment_filenames
            ]

            tenders.append(
                TenderNotice(
                    source=self.source,
                    source_tender_id=item.source_tender_id,
                    title=detail.title or item.title or "Unknown title",
                    buyer=detail.buyer_name,
                    buyer_ico=detail.buyer_ico,
                    description=detail.description_text or None,
                    submission_deadline_at=detail.submission_deadline_at or item.closing_at,
                    bids_opening_at=None,
                    notice_url=item.notice_url,
                    documents=documents,
                )
            )

        return tenders

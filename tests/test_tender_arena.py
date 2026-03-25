from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from tenderscraper.connectors.sources import tender_arena as tender_arena_connector


def test_tender_arena_uses_listing_fallback_when_detail_is_rate_limited(monkeypatch) -> None:
    item = SimpleNamespace(
        tender_id=123,
        source_tender_id="123",
        buyer_id="Z0001",
        buyer_name="Buyer One",
        title="Tender One",
        submission_deadline_at=datetime(2026, 3, 25, tzinfo=timezone.utc),
        notice_url="https://tenderarena.cz/dodavatel/zakazka/123",
    )

    class FakeScraper:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            pass

        def fetch_listing(self, *, limit):
            assert limit == 3
            return [item]

        def fetch_detail(self, *, tender_id: int, timeout_ms: int = 30_000, max_attempts: int = 4):
            assert tender_id == 123
            assert max_attempts == 2
            raise RuntimeError("Transient response 429 for detail request")

        def detail_has_description(self, detail_payload: dict) -> bool:
            return False

        def detail_has_docs(self, detail_payload: dict) -> bool:
            return False

        def fetch_ai_summary(
            self,
            *,
            tender_id: int,
            timeout_ms: int = 30_000,
            max_attempts: int = 4,
        ):
            raise AssertionError("AI summary should not run after detail fallback")

        def build_detail(self, **kwargs):
            raise AssertionError("build_detail should not run for listing-only fallback")

    monkeypatch.setattr(tender_arena_connector, "TenderArenaScraper", FakeScraper)

    connector = tender_arena_connector.TenderArenaConnector()
    tenders = connector.fetch(limit=1)

    assert len(tenders) == 1
    assert tenders[0].source_tender_id == "123"
    assert tenders[0].title == "Tender One"
    assert tenders[0].buyer == "Buyer One"
    assert tenders[0].documents == []


def test_tender_arena_uses_ai_only_when_detail_is_incomplete(monkeypatch) -> None:
    item = SimpleNamespace(
        tender_id=456,
        source_tender_id="456",
        buyer_id="Z0002",
        buyer_name="Buyer Two",
        title="Tender Two",
        submission_deadline_at=datetime(2026, 3, 26, tzinfo=timezone.utc),
        notice_url="https://tenderarena.cz/dodavatel/zakazka/456",
    )

    class FakeScraper:
        def __init__(self, *args, **kwargs) -> None:
            self.ai_calls = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            pass

        def fetch_listing(self, *, limit):
            assert limit == 3
            return [item]

        def fetch_detail(self, *, tender_id: int, timeout_ms: int = 30_000, max_attempts: int = 4):
            assert tender_id == 456
            assert max_attempts == 2
            return {"nazev": "Tender Two"}

        def detail_has_description(self, detail_payload: dict) -> bool:
            return bool(detail_payload.get("strucnyPopis"))

        def detail_has_docs(self, detail_payload: dict) -> bool:
            return bool(detail_payload.get("dokumenty"))

        def fetch_ai_summary(
            self,
            *,
            tender_id: int,
            timeout_ms: int = 30_000,
            max_attempts: int = 4,
        ):
            assert tender_id == 456
            assert max_attempts == 1
            self.ai_calls += 1
            return {
                "manazerskeShrnutiZadavaciDokumentace": {"predmetZakazky": "AI-generated summary"},
                "dokumenty": [{"id": 99, "nazev": "specifikace.pdf"}],
            }

        def build_detail(
            self,
            *,
            listing_item,
            detail_payload: dict,
            ai_payload: dict | None,
            profile_payload: dict | None,
        ):
            assert profile_payload is None
            assert ai_payload is not None
            return SimpleNamespace(
                buyer_name=listing_item.buyer_name,
                buyer_ico="12345678",
                title=detail_payload.get("nazev") or listing_item.title,
                description=ai_payload["manazerskeShrnutiZadavaciDokumentace"]["predmetZakazky"],
                submission_deadline_at=listing_item.submission_deadline_at,
                bids_opening_at=None,
                docs=[
                    SimpleNamespace(
                        filename="specifikace.pdf",
                        download_url="https://api.tenderarena.cz/ta/profil/stahovani-dokumentu/dokument/99",
                    )
                ],
            )

    fake_scraper = FakeScraper()
    monkeypatch.setattr(tender_arena_connector, "TenderArenaScraper", lambda *args, **kwargs: fake_scraper)

    connector = tender_arena_connector.TenderArenaConnector()
    tenders = connector.fetch(limit=1)

    assert len(tenders) == 1
    assert fake_scraper.ai_calls == 1
    assert tenders[0].description == "AI-generated summary"
    assert tenders[0].documents[0].filename == "specifikace.pdf"

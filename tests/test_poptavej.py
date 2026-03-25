from __future__ import annotations

from tenderscraper.scraping.auth import poptavej_auth
from tenderscraper.scraping.auth.poptavej_auth import PoptavejAuthConfig
from tenderscraper.scraping.sources.poptavej import PoptavejScraper


def test_extract_buyer_fields_from_contact_pairs() -> None:
    buyer, ico = PoptavejScraper._extract_buyer_fields_from_pairs(
        [
            ("N\u00e1zev:", "Buyer Corp"),
            ("I\u010c:", "70889988"),
            ("Jm\u00e9no:", "Mgr. Marek Stowasser"),
        ]
    )

    assert buyer == "Buyer Corp"
    assert ico == "70889988"


def test_auth_config_reads_credentials_from_settings(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("POPTAVEJ_USERNAME", raising=False)
    monkeypatch.delenv("POPTAVEJ_PASSWORD", raising=False)
    monkeypatch.setenv("POPTAVEJ_STORAGE_STATE", str(tmp_path / "poptavej_state.json"))
    monkeypatch.setattr(poptavej_auth.settings, "poptavej_username", "user")
    monkeypatch.setattr(poptavej_auth.settings, "poptavej_password", "secret")

    config = PoptavejAuthConfig.from_env()

    assert config.username == "user"
    assert config.password == "secret"
    assert config.storage_state_path == tmp_path / "poptavej_state.json"


def test_parse_absolute_date_accepts_suffix_text() -> None:
    scraper = PoptavejScraper()

    parsed = scraper._parse_absolute_date("19.3.2026 - 1 den do ukončení")

    assert parsed is not None
    assert parsed.year == 2026
    assert parsed.month == 3
    assert parsed.day == 19


def test_extract_submission_deadline_from_detail_text() -> None:
    raw = PoptavejScraper._extract_submission_deadline_from_text(
        "Datum pro podání nabídky:\n19.3.2026 - 1 den do ukončení\nDatum zveřejnění:\n18.3.2026"
    )

    assert raw == "19.3.2026 - 1 den do ukončení"

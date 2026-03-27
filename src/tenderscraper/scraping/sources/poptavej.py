from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

from tenderscraper.scraping.overlays import dismiss_common_overlays


@dataclass(frozen=True)
class ScrapedPoptavejListingItem:
    source_tender_id: str
    title: str
    notice_url: str
    posted_at: datetime | None
    posted_at_raw: str | None
    closing_at: datetime | None
    closing_raw: str | None
    procurement_type: str | None
    value_text: str | None
    category: str | None
    region: str | None


@dataclass(frozen=True)
class ScrapedPoptavejDetail:
    source_tender_id: str
    notice_url: str
    title: str | None
    original_url: str | None
    buyer_name: str | None
    buyer_ico: str | None
    winner_name: str | None
    winner_ic: str | None
    description_html: str | None
    description_text: str | None
    submission_deadline_at: datetime | None
    submission_deadline_raw: str | None
    attachment_filenames: list[str]


class PoptavejScraper:
    BASE = "https://www.poptavej.cz"
    START_URL_IT = (
        "https://www.poptavej.cz/verejne-zakazky?filters%5Bkategorie%5D%5B0%5D=16"
    )

    ROW_SELECTOR = "div.procurement-list div.row.procurement"
    NEXT_SELECTOR = "li.page-item a.page-link.next"

    _DATE_SEL = "div.col.date"
    _TITLE_LINK_SEL = "div.col.nazev a[href]"
    _HODNOTA_SEL = "div.col.nazev div.hodnota"
    _VALUE_SEL = "div.col.cena"
    _CATEGORY_SEL = "a.col.category"
    _REGION_SEL = "a.col.location"
    _CLOSING_SEL = "div.col.ukonceni"

    _DETAIL_TITLE_SEL = "div.main-text h1"
    _DETAIL_DESC_SEL = "div.main-text p.popis"
    _DETAIL_ATTACH_PUBLIC_SEL = "div.main-text a.prilohy span"
    _DETAIL_ATTACH_AUTH_SEL = "div.main-text a[target='_blank'][href*='/data/procurement/file/']"
    _DETAIL_CONTACT_ROW_SEL = "div.contact-area .contact .row"
    _DETAIL_WINNER_ROW_SEL = "div.winner-area .contact .row"
    _DETAIL_CONTACT_TITLE_SEL = ".title"
    _DETAIL_CONTACT_VALUE_SEL = ".value"
    _DETAIL_DEADLINE_RE = re.compile(
        r"Datum\s+pro\s+pod[aá]n[ií]\s+nab[ií]dky\s*:?\s*([^\n\r]+)",
        re.IGNORECASE,
    )

    _ID_RE = re.compile(r"/verejna-zakazka/(VZ[0-9]+)/", re.IGNORECASE)
    _ABS_DATE_RE = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\.(\d{4})(?:\s*-\s*.*)?\s*$")
    _TODAY_RE = re.compile(r"^\s*Dnes\s+(\d{1,2}):(\d{2})\s*$", re.IGNORECASE)
    _YDAY_RE = re.compile(r"^\s*V[cC]era\s+(\d{1,2}):(\d{2})\s*$", re.IGNORECASE)

    def fetch_listing(
        self,
        *,
        limit: int | None = 10,
        start_url: str | None = None,
        headless: bool = True,
        timeout_ms: int = 30_000,
    ) -> list[ScrapedPoptavejListingItem]:
        from playwright.sync_api import sync_playwright

        url = start_url or self.START_URL_IT
        out: list[ScrapedPoptavejListingItem] = []
        seen_ids: set[str] = set()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            page = browser.new_page()

            try:
                while url and (limit is None or len(out) < limit):
                    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    page.wait_for_selector(self.ROW_SELECTOR, timeout=timeout_ms)
                    dismiss_common_overlays(page)

                    rows = page.locator(self.ROW_SELECTOR)
                    row_count = rows.count()

                    for i in range(row_count):
                        if limit is not None and len(out) >= limit:
                            break

                        row = rows.nth(i)
                        link = row.locator(self._TITLE_LINK_SEL).first
                        if link.count() == 0:
                            continue

                        href = (link.get_attribute("href") or "").strip()
                        title = (link.inner_text() or "").strip()
                        if not href or not title:
                            continue

                        notice_url = href if href.startswith("http") else urljoin(self.BASE, href)
                        match = self._ID_RE.search(notice_url + "/")
                        if not match:
                            continue

                        source_tender_id = match.group(1)
                        if source_tender_id in seen_ids:
                            continue
                        seen_ids.add(source_tender_id)

                        date_raw = self._safe_text(row.locator(self._DATE_SEL).first)
                        closing_raw = self._safe_text(row.locator(self._CLOSING_SEL).first)

                        out.append(
                            ScrapedPoptavejListingItem(
                                source_tender_id=source_tender_id,
                                title=title,
                                notice_url=notice_url,
                                posted_at=self._parse_posted_at(date_raw),
                                posted_at_raw=date_raw,
                                closing_at=self._parse_absolute_date(closing_raw),
                                closing_raw=closing_raw,
                                procurement_type=self._safe_text(row.locator(self._HODNOTA_SEL).first),
                                value_text=self._safe_text(row.locator(self._VALUE_SEL).first),
                                category=self._safe_text(row.locator(self._CATEGORY_SEL).first),
                                region=self._safe_text(row.locator(self._REGION_SEL).first),
                            )
                        )

                    next_loc = page.locator(self.NEXT_SELECTOR).first
                    if next_loc.count() == 0:
                        break

                    next_href = (next_loc.get_attribute("href") or "").strip()
                    if not next_href:
                        break
                    if next_href.startswith("http"):
                        url = next_href
                    else:
                        url = urljoin(self.BASE, next_href)
            finally:
                browser.close()

        return out

    def fetch_tender_urls(
        self,
        *,
        limit: int | None = 10,
        start_url: str | None = None,
        headless: bool = True,
        timeout_ms: int = 30_000,
    ) -> list[str]:
        items = self.fetch_listing(
            limit=limit,
            start_url=start_url,
            headless=headless,
            timeout_ms=timeout_ms,
        )
        return [item.notice_url for item in items]

    def fetch_detail(
        self,
        *,
        notice_url: str,
        storage_state_path: Path | str | None = None,
        headless: bool = True,
        timeout_ms: int = 30_000,
    ) -> ScrapedPoptavejDetail:
        from playwright.sync_api import sync_playwright

        match = self._ID_RE.search(notice_url.rstrip("/") + "/")
        source_tender_id = match.group(1) if match else notice_url.rstrip("/").split("/")[-2]

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context_kwargs = {}
            if storage_state_path:
                context_kwargs["storage_state"] = str(storage_state_path)
            context = browser.new_context(**context_kwargs)
            page = context.new_page()

            try:
                page.goto(notice_url, wait_until="domcontentloaded", timeout=timeout_ms)
                dismiss_common_overlays(page)

                title = None
                title_loc = page.locator(self._DETAIL_TITLE_SEL).first
                if title_loc.count() > 0:
                    title = (title_loc.inner_text() or "").strip() or None

                desc_html = None
                desc_text = None
                desc_loc = page.locator(self._DETAIL_DESC_SEL).first
                if desc_loc.count() > 0:
                    try:
                        desc_html = (desc_loc.inner_html() or "").strip() or None
                    except Exception:
                        desc_html = None
                    try:
                        desc_text = (desc_loc.inner_text() or "").strip() or None
                    except Exception:
                        desc_text = None

                contact_rows: list[tuple[str | None, str | None]] = []
                rows = page.locator(self._DETAIL_CONTACT_ROW_SEL)
                for i in range(rows.count()):
                    row = rows.nth(i)
                    contact_rows.append(
                        (
                            self._safe_text(row.locator(self._DETAIL_CONTACT_TITLE_SEL).first),
                            self._safe_text(row.locator(self._DETAIL_CONTACT_VALUE_SEL).first),
                        )
                    )

                original_url = self._extract_original_url(page)
                buyer_name, buyer_ico = self._extract_name_and_id_from_pairs(contact_rows)
                winner_name, winner_ic = self._extract_winner_fields(page)
                submission_deadline_raw = self._extract_submission_deadline_raw(page)
                submission_deadline_at = self._parse_absolute_date(submission_deadline_raw)

                return ScrapedPoptavejDetail(
                    source_tender_id=source_tender_id,
                    notice_url=notice_url,
                    title=title,
                    original_url=original_url,
                    buyer_name=buyer_name,
                    buyer_ico=buyer_ico,
                    winner_name=winner_name,
                    winner_ic=winner_ic,
                    description_html=desc_html,
                    description_text=desc_text,
                    submission_deadline_at=submission_deadline_at,
                    submission_deadline_raw=submission_deadline_raw,
                    attachment_filenames=self._collect_attachment_filenames(page),
                )
            finally:
                try:
                    context.close()
                except Exception:
                    pass
                browser.close()

    def _collect_attachment_filenames(self, page) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()

        for selector in (self._DETAIL_ATTACH_AUTH_SEL, self._DETAIL_ATTACH_PUBLIC_SEL):
            locators = page.locator(selector)
            for i in range(locators.count()):
                text = self._safe_text(locators.nth(i))
                if text and text not in seen:
                    seen.add(text)
                    out.append(text)

        return out

    @classmethod
    def _extract_submission_deadline_raw(cls, page) -> str | None:
        body = page.locator("body").first
        text = cls._safe_text(body)
        if not text:
            return None
        return cls._extract_submission_deadline_from_text(text)

    @classmethod
    def _extract_submission_deadline_from_text(cls, text: str | None) -> str | None:
        if not text:
            return None
        match = cls._DETAIL_DEADLINE_RE.search(text)
        if not match:
            return None
        return match.group(1).strip() or None

    @classmethod
    def _extract_original_url(cls, page) -> str | None:
        rows = page.locator(cls._DETAIL_CONTACT_ROW_SEL)
        for i in range(rows.count()):
            row = rows.nth(i)
            label = cls._normalize_label(cls._safe_text(row.locator(cls._DETAIL_CONTACT_TITLE_SEL).first))
            if label != "url odkaz":
                continue

            link = row.locator("a[href]").first
            if link.count() > 0:
                href = (link.get_attribute("href") or "").strip()
                if href:
                    return href

            value = cls._safe_text(row.locator(cls._DETAIL_CONTACT_VALUE_SEL).first)
            if value and value.startswith(("http://", "https://")):
                return value

        return None

    @classmethod
    def _extract_name_and_id_from_pairs(
        cls, pairs: list[tuple[str | None, str | None]]
    ) -> tuple[str | None, str | None]:
        name: str | None = None
        ico: str | None = None

        for raw_label, raw_value in pairs:
            label = cls._normalize_label(raw_label)
            value = (raw_value or "").strip() or None
            if not value:
                continue

            if label == "nazev" and name is None:
                name = value
            elif label in {"ic", "ico"} and ico is None:
                ico = value

        return name, ico

    @classmethod
    def _extract_winner_fields(cls, page) -> tuple[str | None, str | None]:
        winner_rows: list[tuple[str | None, str | None]] = []
        rows = page.locator(cls._DETAIL_WINNER_ROW_SEL)
        for i in range(rows.count()):
            row = rows.nth(i)
            winner_rows.append(
                (
                    cls._safe_text(row.locator(cls._DETAIL_CONTACT_TITLE_SEL).first),
                    cls._safe_text(row.locator(cls._DETAIL_CONTACT_VALUE_SEL).first),
                )
            )

        return cls._extract_name_and_id_from_pairs(winner_rows)

    @staticmethod
    def _normalize_label(value: str | None) -> str:
        if not value:
            return ""

        normalized = unicodedata.normalize("NFKD", value)
        ascii_only = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        ascii_only = ascii_only.replace(":", " ")
        return re.sub(r"\s+", " ", ascii_only).strip().lower()

    @staticmethod
    def _safe_text(locator) -> str | None:
        try:
            if locator.count() == 0:
                return None
            text = locator.inner_text()
            text = text.strip() if text else None
            return text or None
        except Exception:
            return None

    def _parse_posted_at(self, raw: str | None) -> datetime | None:
        if not raw:
            return None

        text = self._normalize_label(raw)

        match = self._TODAY_RE.match(text)
        if match:
            hh, mm = int(match.group(1)), int(match.group(2))
            now = datetime.now()
            return now.replace(hour=hh, minute=mm, second=0, microsecond=0)

        match = self._YDAY_RE.match(text)
        if match:
            hh, mm = int(match.group(1)), int(match.group(2))
            now = datetime.now()
            dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            return dt - timedelta(days=1)

        return self._parse_absolute_date(raw)

    def _parse_absolute_date(self, raw: str | None) -> datetime | None:
        if not raw:
            return None

        match = self._ABS_DATE_RE.match(raw.strip())
        if not match:
            return None

        day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
        try:
            return datetime(year, month, day, 0, 0, 0)
        except ValueError:
            return None

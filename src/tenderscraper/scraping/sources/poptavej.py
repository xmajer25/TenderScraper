from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

from tenderscraper.scraping.overlays import dismiss_common_overlays


# ---------------------------
# Models
# ---------------------------

@dataclass(frozen=True)
class ScrapedPoptavejListingItem:
    source_tender_id: str
    title: str
    notice_url: str

    # from listing
    posted_at: Optional[datetime]          # naive local datetime (machine local)
    posted_at_raw: Optional[str]

    closing_at: Optional[datetime]         # parsed only if absolute date exists in listing
    closing_raw: Optional[str]

    procurement_type: Optional[str]        # text under title ("Veřejná zakázka malého rozsahu", ...)
    value_text: Optional[str]              # "45 064 000 Kč" / "neurčeno"
    category: Optional[str]                # "Informační technologie"
    region: Optional[str]                  # "Praha", "Ústecký", ...


@dataclass(frozen=True)
class ScrapedPoptavejDetail:
    source_tender_id: str
    notice_url: str

    title: Optional[str]
    description_html: Optional[str]        # innerHTML of p.popis
    description_text: Optional[str]        # innerText of p.popis

    # Public-only: just filenames listed under "Přílohy:"
    attachment_filenames: List[str]


# ---------------------------
# Scraper
# ---------------------------

class PoptavejScraper:
    BASE = "https://www.poptavej.cz"
    START_URL_IT = (
        "https://www.poptavej.cz/verejne-zakazky?filters%5Bkategorie%5D%5B0%5D=16"
    )

    ROW_SELECTOR = "div.procurement-list div.row.procurement"
    NEXT_SELECTOR = "li.page-item a.page-link.next"

    # Row sub-selectors
    _DATE_SEL = "div.col.date"
    _TITLE_LINK_SEL = "div.col.nazev a[href]"
    _HODNOTA_SEL = "div.col.nazev div.hodnota"
    _VALUE_SEL = "div.col.cena"
    _CATEGORY_SEL = "a.col.category"
    _REGION_SEL = "a.col.location"
    _CLOSING_SEL = "div.col.ukonceni"

    # Detail sub-selectors
    _DETAIL_TITLE_SEL = "div.main-text h1"
    _DETAIL_DESC_SEL = "div.main-text p.popis"
    _DETAIL_ATTACH_SEL = "div.main-text a.prilohy span"

    _ID_RE = re.compile(r"/verejna-zakazka/(VZ[0-9]+)/", re.IGNORECASE)
    _ABS_DATE_RE = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\s*$")
    _TODAY_RE = re.compile(r"^\s*Dnes\s+(\d{1,2}):(\d{2})\s*$", re.IGNORECASE)
    _YDAY_RE = re.compile(r"^\s*Včera\s+(\d{1,2}):(\d{2})\s*$", re.IGNORECASE)

    def fetch_listing(
        self,
        *,
        limit: int = 10,
        start_url: str | None = None,
        headless: bool = True,
        timeout_ms: int = 30_000,
    ) -> List[ScrapedPoptavejListingItem]:
        url = start_url or self.START_URL_IT
        out: List[ScrapedPoptavejListingItem] = []
        seen_ids: set[str] = set()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            page = browser.new_page()

            try:
                while url and len(out) < limit:
                    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    page.wait_for_selector(self.ROW_SELECTOR, timeout=timeout_ms)
                    dismiss_common_overlays(page)

                    rows = page.locator(self.ROW_SELECTOR)
                    row_count = rows.count()

                    for i in range(row_count):
                        if len(out) >= limit:
                            break

                        row = rows.nth(i)

                        # Title + href
                        a = row.locator(self._TITLE_LINK_SEL).first
                        if a.count() == 0:
                            continue

                        href = (a.get_attribute("href") or "").strip()
                        title = (a.inner_text() or "").strip()
                        if not href or not title:
                            continue

                        notice_url = href if href.startswith("http") else urljoin(self.BASE, href)

                        # Extract ID from URL
                        m = self._ID_RE.search(notice_url + "/")
                        if not m:
                            continue
                        source_tender_id = m.group(1)

                        # Dedup
                        if source_tender_id in seen_ids:
                            continue
                        seen_ids.add(source_tender_id)

                        # Date text (posted)
                        date_raw = self._safe_text(row.locator(self._DATE_SEL).first)
                        posted_at = self._parse_posted_at(date_raw)

                        # Procurement type (text under title)
                        procurement_type = self._safe_text(row.locator(self._HODNOTA_SEL).first)

                        # Value
                        value_text = self._safe_text(row.locator(self._VALUE_SEL).first)

                        # Category + region
                        category = self._safe_text(row.locator(self._CATEGORY_SEL).first)
                        region = self._safe_text(row.locator(self._REGION_SEL).first)

                        # Closing (can be "X dní do ukončení" or "19.1.2026")
                        closing_raw = self._safe_text(row.locator(self._CLOSING_SEL).first)
                        closing_at = self._parse_absolute_date(closing_raw)

                        out.append(
                            ScrapedPoptavejListingItem(
                                source_tender_id=source_tender_id,
                                title=title,
                                notice_url=notice_url,
                                posted_at=posted_at,
                                posted_at_raw=date_raw,
                                closing_at=closing_at,
                                closing_raw=closing_raw,
                                procurement_type=procurement_type,
                                value_text=value_text,
                                category=category,
                                region=region,
                            )
                        )

                    # Pagination: do NOT click, just follow href if present
                    next_loc = page.locator(self.NEXT_SELECTOR).first
                    if next_loc.count() == 0:
                        break

                    next_href = (next_loc.get_attribute("href") or "").strip()
                    if not next_href:
                        break
                    url = next_href if next_href.startswith("http") else urljoin(self.BASE, next_href)

            finally:
                browser.close()

        return out

    def fetch_tender_urls(
        self,
        *,
        limit: int = 10,
        start_url: str | None = None,
        headless: bool = True,
        timeout_ms: int = 30_000,
    ) -> List[str]:
        items = self.fetch_listing(
            limit=limit, start_url=start_url, headless=headless, timeout_ms=timeout_ms
        )
        return [i.notice_url for i in items]

    def fetch_detail(
        self,
        *,
        notice_url: str,
        headless: bool = True,
        timeout_ms: int = 30_000,
    ) -> ScrapedPoptavejDetail:
        # ID from URL
        m = self._ID_RE.search(notice_url.rstrip("/") + "/")
        source_tender_id = m.group(1) if m else notice_url.rstrip("/").split("/")[-2]

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            page = browser.new_page()

            try:
                page.goto(notice_url, wait_until="domcontentloaded", timeout=timeout_ms)
                dismiss_common_overlays(page)

                # Title
                title = None
                tloc = page.locator(self._DETAIL_TITLE_SEL).first
                if tloc.count() > 0:
                    title = (tloc.inner_text() or "").strip() or None

                # Description
                desc_html = None
                desc_text = None
                dloc = page.locator(self._DETAIL_DESC_SEL).first
                if dloc.count() > 0:
                    try:
                        desc_html = (dloc.inner_html() or "").strip() or None
                    except Exception:
                        desc_html = None
                    try:
                        desc_text = (dloc.inner_text() or "").strip() or None
                    except Exception:
                        desc_text = None

                # Attachments list (public-visible filenames)
                attachment_filenames: List[str] = []
                spans = page.locator(self._DETAIL_ATTACH_SEL)
                for i in range(spans.count()):
                    s = (spans.nth(i).inner_text() or "").strip()
                    if s:
                        attachment_filenames.append(s)

                return ScrapedPoptavejDetail(
                    source_tender_id=source_tender_id,
                    notice_url=notice_url,
                    title=title,
                    description_html=desc_html,
                    description_text=desc_text,
                    attachment_filenames=attachment_filenames,
                )
            finally:
                browser.close()

    @staticmethod
    def _safe_text(locator) -> Optional[str]:
        try:
            if locator.count() == 0:
                return None
            t = locator.inner_text()
            t = t.strip() if t else None
            return t or None
        except Exception:
            return None

    def _parse_posted_at(self, raw: Optional[str]) -> Optional[datetime]:
        """
        Listing can contain:
          - "Dnes 11:01"
          - "Včera 23:10"
          - "8.2.2026"
        Returns naive datetime in *machine local time*.
        """
        if not raw:
            return None
        s = raw.strip()

        m = self._TODAY_RE.match(s)
        if m:
            hh, mm = int(m.group(1)), int(m.group(2))
            now = datetime.now()
            return now.replace(hour=hh, minute=mm, second=0, microsecond=0)

        m = self._YDAY_RE.match(s)
        if m:
            hh, mm = int(m.group(1)), int(m.group(2))
            now = datetime.now()
            dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            return dt - timedelta(days=1)

        return self._parse_absolute_date(s)

    def _parse_absolute_date(self, raw: Optional[str]) -> Optional[datetime]:
        """
        Accepts "19.1.2026" / "8.2.2026" (with/without spaces).
        Returns naive datetime at 00:00.
        """
        if not raw:
            return None
        s = raw.strip()
        m = self._ABS_DATE_RE.match(s)
        if not m:
            return None
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(y, mo, d, 0, 0, 0)
        except ValueError:
            return None

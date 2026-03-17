from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from playwright.sync_api import TimeoutError as PWTimeoutError
from playwright.sync_api import sync_playwright

from tenderscraper.scraping.datetime_cz import parse_cz_datetime
from tenderscraper.scraping.label_value import get_value_by_label
from tenderscraper.scraping.overlays import dismiss_common_overlays


@dataclass(frozen=True)
class ScrapedDoc:
    display_name: Optional[str]
    category: Optional[str]
    published_at: Optional[str]
    filename: Optional[str]
    size: Optional[str]


@dataclass(frozen=True)
class ScrapedTenderDetail:
    buyer_name: Optional[str]
    buyer_ico: Optional[str]
    title: Optional[str]
    description: Optional[str]
    submission_deadline_at: Optional[datetime]
    bids_opening_at: Optional[datetime]
    docs: List[ScrapedDoc]


class TenderArenaScraper:
    BASE = "https://tenderarena.cz"
    START_URL = "https://tenderarena.cz/dodavatel"

    ROWS_XPATH = "//*[@id='zakazky']//app-seznam//section/a"
    ROWS_XPATH_FALLBACK = "//*[@id='zakazky']//section//a[@href]"
    NEXT_XPATH = "//*[@id='zakazky']//a[.//p[contains(normalize-space(), 'Dal')]]"

    DOC_ROW_XPATH = "//*[@id='seznam-dokumentu']//app-dokument/section"
    DOC_MODAL_XPATH = "//*[@id='detail-dokumentu-modalni-panel']"

    _DETAIL_BUTTON_RE = re.compile(r"zobrazit|detail", re.IGNORECASE)
    _CLOSE_BUTTON_RE = re.compile(r"zavrit|close", re.IGNORECASE)

    def _listing_locator(self, page):
        primary = page.locator(f"xpath={self.ROWS_XPATH}")
        try:
            if primary.count() > 0:
                return primary
        except Exception:
            pass
        return page.locator(f"xpath={self.ROWS_XPATH_FALLBACK}")

    def _button_by_name(self, scope, pattern: re.Pattern[str]):
        buttons = scope.locator("button")
        for i in range(buttons.count()):
            button = buttons.nth(i)
            try:
                title = (button.get_attribute("title") or "").strip()
            except Exception:
                title = ""
            if title and pattern.search(title):
                return button
            try:
                text = (button.inner_text() or "").strip()
            except Exception:
                text = ""
            if text and pattern.search(text):
                return button
        return None

    def _wait_for_listing_ready(self, page, *, timeout_ms: int) -> bool:
        deadline = datetime.now().timestamp() + (timeout_ms / 1000)
        while datetime.now().timestamp() < deadline:
            dismiss_common_overlays(page)
            try:
                page.wait_for_load_state("networkidle", timeout=2_000)
            except Exception:
                pass

            anchors = self._listing_locator(page)
            try:
                if anchors.count() > 0:
                    return True
            except Exception:
                pass

            page.wait_for_timeout(500)
        return False

    def fetch_tender_urls(self, *, limit: int, headless: bool, timeout_ms: int) -> List[str]:
        urls: List[str] = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            page = browser.new_page()

            try:
                page.goto(self.START_URL, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_timeout(1_000)
                if not self._wait_for_listing_ready(page, timeout_ms=timeout_ms):
                    return []

                while len(urls) < limit:
                    anchors = self._listing_locator(page)

                    for i in range(anchors.count()):
                        if len(urls) >= limit:
                            break
                        anchor = anchors.nth(i)
                        href = anchor.get_attribute("href") or ""
                        full = href if href.startswith("http") else f"{self.BASE}{href}"
                        if full and full not in urls:
                            urls.append(full)

                    if len(urls) >= limit:
                        break

                    next_btn = page.locator(f"xpath={self.NEXT_XPATH}").first
                    if next_btn.count() == 0:
                        break

                    before_first = None
                    try:
                        if anchors.count() > 0:
                            before_first = anchors.nth(0).get_attribute("href")
                    except Exception:
                        before_first = None

                    dismiss_common_overlays(page)
                    try:
                        next_btn.scroll_into_view_if_needed(timeout=2_000)
                    except Exception:
                        pass

                    clicked = False
                    try:
                        next_btn.click(timeout=5_000)
                        clicked = True
                    except Exception:
                        dismiss_common_overlays(page)
                        try:
                            next_btn.click(timeout=5_000, force=True)
                            clicked = True
                        except Exception:
                            clicked = False

                    if not clicked:
                        break

                    changed = False
                    for _ in range(32):
                        page.wait_for_timeout(250)
                        try:
                            now_anchors = self._listing_locator(page)
                            if now_anchors.count() == 0:
                                continue
                            now_first = now_anchors.nth(0).get_attribute("href")
                            if before_first is None:
                                changed = True
                                break
                            if now_first and now_first != before_first:
                                changed = True
                                break
                        except Exception:
                            continue

                    if not changed:
                        break

                    if not self._wait_for_listing_ready(page, timeout_ms=timeout_ms):
                        break

            except PWTimeoutError:
                return []
            finally:
                browser.close()

        return urls

    def fetch_detail(
        self,
        *,
        url: str,
        headless: bool = True,
        timeout_ms: int = 30_000,
        include_docs: bool = True,
    ) -> ScrapedTenderDetail:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            page = browser.new_page()

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_timeout(300)

                buyer_name = get_value_by_label(page, "uredni nazev zadavatele")
                buyer_ico = get_value_by_label(page, "ico zadavatele")
                title = get_value_by_label(page, "nazev zakazky")
                description = get_value_by_label(page, "strucny popis")

                deadline_raw = get_value_by_label(page, "lhuta pro podani nabidek")
                opening_raw = get_value_by_label(page, "datum otevirani nabidek")

                submission_deadline_at = parse_cz_datetime(deadline_raw or "")
                bids_opening_at = parse_cz_datetime(opening_raw or "")

                docs: List[ScrapedDoc] = []
                if include_docs:
                    docs = self._extract_docs(page, timeout_ms=timeout_ms)

                return ScrapedTenderDetail(
                    buyer_name=buyer_name,
                    buyer_ico=buyer_ico,
                    title=title,
                    description=description,
                    submission_deadline_at=submission_deadline_at,
                    bids_opening_at=bids_opening_at,
                    docs=docs,
                )
            finally:
                browser.close()

    def _extract_docs(self, page, *, timeout_ms: int):
        out: List[ScrapedDoc] = []
        doc_sections = page.locator(f"xpath={self.DOC_ROW_XPATH}")

        if doc_sections.count() == 0:
            return out

        for i in range(doc_sections.count()):
            try:
                sec = doc_sections.nth(i)

                info_btn = self._button_by_name(sec, self._DETAIL_BUTTON_RE)
                if info_btn is None:
                    fallback = sec.locator("xpath=.//button[1]").first
                    if fallback.count() == 0:
                        continue
                    info_btn = fallback

                dismiss_common_overlays(page)

                try:
                    info_btn.click(trial=True, timeout=2_000)
                    info_btn.click(timeout=5_000)
                except Exception:
                    dismiss_common_overlays(page)
                    info_btn.click(timeout=5_000, force=True)

                page.wait_for_selector(f"xpath={self.DOC_MODAL_XPATH}", timeout=timeout_ms)

                display_name = get_value_by_label(page, "nazev dokumentu")
                category = get_value_by_label(page, "kategorie dokumentu")
                published_at = get_value_by_label(page, "datum uverejneni")
                filename = get_value_by_label(page, "nazev souboru")
                size = get_value_by_label(page, "velikost")

                out.append(
                    ScrapedDoc(
                        display_name=display_name,
                        category=category,
                        published_at=published_at,
                        filename=filename,
                        size=size,
                    )
                )

                modal = page.locator(f"xpath={self.DOC_MODAL_XPATH}").first
                close_btn = self._button_by_name(modal, self._CLOSE_BUTTON_RE)
                if close_btn is not None and close_btn.count() > 0:
                    close_btn.click(timeout=2_000)
                else:
                    page.keyboard.press("Escape")

                page.wait_for_timeout(150)

                overlay = page.locator(".modal-overlay").first
                try:
                    overlay.wait_for(state="hidden", timeout=3_000)
                except Exception:
                    try:
                        overlay.click(force=True, timeout=1_000)
                    except Exception:
                        pass

                try:
                    modal.wait_for(state="hidden", timeout=3_000)
                except Exception:
                    pass

            except Exception:
                dismiss_common_overlays(page)
                continue

        return out

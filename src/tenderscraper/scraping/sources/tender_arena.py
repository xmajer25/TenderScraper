from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

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
    NEXT_XPATH = "//*[@id='zakazky']//a[.//p[normalize-space()='Další']]"

    DOC_ROW_XPATH = "//*[@id='seznam-dokumentu']//app-dokument/section"
    DOC_INFO_BTN_REL_XPATH = ".//button[1]"
    DOC_DOWNLOAD_BTN_REL_XPATH = ".//button[2]"

    DOC_MODAL_XPATH = "//*[@id='detail-dokumentu-modalni-panel']"

    def fetch_tender_urls(self, *, limit: int, headless: bool, timeout_ms: int) -> List[str]:
        urls: List[str] = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            page = browser.new_page()

            try:
                page.goto(self.START_URL, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_selector(f"xpath={self.ROWS_XPATH}", timeout=timeout_ms)

                while len(urls) < limit:
                    anchors = page.locator(f"xpath={self.ROWS_XPATH}")

                    # collect current page urls
                    for i in range(anchors.count()):
                        if len(urls) >= limit:
                            break
                        a = anchors.nth(i)
                        href = a.get_attribute("href") or ""
                        full = href if href.startswith("http") else f"{self.BASE}{href}"
                        if full and full not in urls:
                            urls.append(full)

                    if len(urls) >= limit:
                        break

                    next_btn = page.locator(f"xpath={self.NEXT_XPATH}").first
                    if next_btn.count() == 0:
                        break

                    # Snapshot first row href to detect page change
                    before_first = None
                    try:
                        if anchors.count() > 0:
                            before_first = anchors.nth(0).get_attribute("href")
                    except Exception:
                        before_first = None

                    dismiss_common_overlays(page)
                    try:
                        next_btn.scroll_into_view_if_needed(timeout=2000)
                    except Exception:
                        pass

                    # Try click; if intercepted, force click
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

                    # Wait up to 8s for the list to actually change; otherwise stop to avoid hanging forever
                    changed = False
                    for _ in range(32):  # 32 * 250ms = 8s
                        page.wait_for_timeout(250)
                        try:
                            now_anchors = page.locator(f"xpath={self.ROWS_XPATH}")
                            if now_anchors.count() == 0:
                                continue
                            now_first = now_anchors.nth(0).get_attribute("href")
                            if before_first is None:
                                # no baseline -> accept that rows exist
                                changed = True
                                break
                            if now_first and now_first != before_first:
                                changed = True
                                break
                        except Exception:
                            continue

                    if not changed:
                        break

                    page.wait_for_selector(f"xpath={self.ROWS_XPATH}", timeout=timeout_ms)

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

                buyer_name = get_value_by_label(page, "Úřední název zadavatele")
                buyer_ico = get_value_by_label(page, "IČO zadavatele")
                title = get_value_by_label(page, "Název zakázky")
                description = get_value_by_label(page, "Stručný popis (předmět zakázky)")

                deadline_raw = get_value_by_label(page, "Lhůta pro podání nabídek")
                opening_raw = get_value_by_label(page, "Datum otevírání nabídek")

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

                info_btn = sec.locator("button[title='Zobrazit detail']").first
                if info_btn.count() == 0:
                    info_btn = sec.locator("xpath=.//button[1]").first
                if info_btn.count() == 0:
                    continue

                dismiss_common_overlays(page)

                try:
                    info_btn.click(trial=True, timeout=2000)
                    info_btn.click(timeout=5000)
                except Exception:
                    dismiss_common_overlays(page)
                    info_btn.click(timeout=5000, force=True)

                page.wait_for_selector("xpath=//*[@id='detail-dokumentu-modalni-panel']", timeout=timeout_ms)

                display_name = get_value_by_label(page, "Název dokumentu")
                category = get_value_by_label(page, "Kategorie dokumentu")
                published_at = get_value_by_label(page, "Datum uveřejnění")
                filename = get_value_by_label(page, "Název souboru")
                size = get_value_by_label(page, "Velikost")

                out.append(
                    ScrapedDoc(
                        display_name=display_name,
                        category=category,
                        published_at=published_at,
                        filename=filename,
                        size=size,
                    )
                )

                modal = page.locator("xpath=//*[@id='detail-dokumentu-modalni-panel']").first

                close_btn = modal.get_by_role("button", name="Zavřít")
                if close_btn.count() == 0:
                    close_btn = modal.get_by_role("button", name="Close")

                if close_btn.count() > 0:
                    close_btn.first.click(timeout=2000)
                else:
                    page.keyboard.press("Escape")

                page.wait_for_timeout(150)

                overlay = page.locator(".modal-overlay").first
                try:
                    overlay.wait_for(state="hidden", timeout=3000)
                except Exception:
                    try:
                        overlay.click(force=True, timeout=1000)
                    except Exception:
                        pass

                try:
                    modal.wait_for(state="hidden", timeout=3000)
                except Exception:
                    pass

            except Exception:
                dismiss_common_overlays(page)
                continue

        return out

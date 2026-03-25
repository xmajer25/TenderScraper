from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import random
import time
from json import JSONDecodeError
from typing import Any, Optional
from urllib.parse import urljoin

from playwright.sync_api import Browser, BrowserContext, Error as PWError, Page, Playwright, sync_playwright

from tenderscraper.scraping.overlays import dismiss_common_overlays


@dataclass(frozen=True)
class ScrapedDoc:
    document_id: int | None
    download_url: Optional[str]
    display_name: Optional[str]
    category: Optional[str]
    published_at: Optional[str]
    filename: Optional[str]
    size: Optional[str]


@dataclass(frozen=True)
class ScrapedTenderListingItem:
    tender_id: int
    source_tender_id: str
    buyer_id: Optional[str]
    buyer_name: Optional[str]
    title: Optional[str]
    submission_deadline_at: Optional[datetime]
    notice_url: str


@dataclass(frozen=True)
class ScrapedTenderDetail:
    buyer_name: Optional[str]
    buyer_ico: Optional[str]
    title: Optional[str]
    description: Optional[str]
    submission_deadline_at: Optional[datetime]
    bids_opening_at: Optional[datetime]
    docs: list[ScrapedDoc]


class TenderArenaScraper:
    BASE = "https://tenderarena.cz"
    START_URL = "https://tenderarena.cz/dodavatel"
    API_BASE = "https://api.tenderarena.cz/ta/profil"
    LIST_URL = f"{API_BASE}/seznam-zakazek/noveUverejneneZakazky"
    DETAIL_URL = f"{API_BASE}/detail-zakazky"
    PROFILE_URL = f"{API_BASE}/detail-profilu"
    AI_SUMMARY_URL = f"{API_BASE}/ai/manazerske-shrnuti/nacist"
    DOWNLOAD_URL = f"{API_BASE}/stahovani-dokumentu/dokument"
    TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
    MAX_REQUESTS_PER_PAGE = 60

    def __init__(
        self,
        *,
        timeout_ms: int = 30_000,
        request_pause_s: float = 0.6,
        headless: bool = True,
    ) -> None:
        self.timeout_ms = timeout_ms
        self.request_pause_s = request_pause_s
        self.headless = headless
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._last_request_at = 0.0
        self._request_count = 0
        self._cooldown_until = 0.0

    def __enter__(self) -> "TenderArenaScraper":
        self._ensure_page()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        try:
            if self._page is not None:
                self._page.close()
        finally:
            self._page = None
            try:
                if self._context is not None:
                    self._context.close()
            finally:
                self._context = None
                try:
                    if self._browser is not None:
                        self._browser.close()
                finally:
                    self._browser = None
                    if self._playwright is not None:
                        self._playwright.stop()
                        self._playwright = None

    def _launch_browser(self) -> Browser:
        if self._playwright is None:
            raise RuntimeError("Playwright is not initialized")

        base_launch_kwargs = {
            "args": [
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        }
        candidates = [
            {"headless": True, **base_launch_kwargs},
            {"channel": "msedge", "headless": True, **base_launch_kwargs},
            {"headless": False, **base_launch_kwargs},
            {"channel": "msedge", "headless": False, **base_launch_kwargs},
        ]

        last_error: Exception | None = None
        for candidate in candidates:
            try:
                return self._playwright.chromium.launch(**candidate)
            except Exception as exc:
                last_error = exc

        if last_error is not None:
            raise last_error
        raise RuntimeError("Failed to launch Playwright browser")

    def _create_page(self) -> Page:
        self._playwright = sync_playwright().start()
        last_error: Exception | None = None

        for _ in range(4):
            try:
                self._browser = self._launch_browser()
                self._context = self._browser.new_context(ignore_https_errors=True)
                self._page = self._context.new_page()
                break
            except PWError as exc:
                last_error = exc
                try:
                    if self._page is not None:
                        self._page.close()
                except Exception:
                    pass
                self._page = None
                try:
                    if self._context is not None:
                        self._context.close()
                except Exception:
                    pass
                self._context = None
                try:
                    if self._browser is not None:
                        self._browser.close()
                except Exception:
                    pass
                self._browser = None

        if self._page is None:
            self.close()
            if last_error is not None:
                raise last_error
            raise RuntimeError("Failed to create Playwright page for TenderArena")

        self._page.goto(self.START_URL, wait_until="domcontentloaded", timeout=self.timeout_ms)
        self._page.wait_for_timeout(1_000)
        dismiss_common_overlays(self._page)
        self._request_count = 0
        return self._page

    def _ensure_page(self) -> Page:
        if self._page is not None:
            return self._page
        return self._create_page()

    def _recreate_page(self) -> Page:
        try:
            if self._page is not None:
                self._page.close()
        except Exception:
            pass
        self._page = None

        try:
            if self._context is not None:
                self._context.close()
        except Exception:
            pass
        self._context = None

        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:
            pass
        self._browser = None

        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

        return self._create_page()

    def _pace(self) -> None:
        now = time.monotonic()
        if now < self._cooldown_until:
            time.sleep(self._cooldown_until - now)
            now = time.monotonic()

        elapsed = now - self._last_request_at
        sleep_s = self.request_pause_s - elapsed
        if sleep_s > 0:
            time.sleep(sleep_s + random.uniform(0.05, 0.25))

    def _set_cooldown(self, delay_s: float) -> None:
        self._cooldown_until = max(self._cooldown_until, time.monotonic() + delay_s)

    @staticmethod
    def _retry_delay_for_status(status: int, headers: dict[str, str], current_delay_s: float) -> float:
        retry_after = (headers.get("retry-after") or "").strip()
        if retry_after.isdigit():
            return max(float(retry_after), current_delay_s)
        if status == 429:
            return max(current_delay_s, 10.0)
        if status in {500, 502, 503, 504}:
            return max(current_delay_s, 3.0)
        return current_delay_s

    def _fetch_json_via_browser(
        self,
        url: str,
        *,
        max_attempts: int = 4,
    ) -> dict[str, Any]:
        page = self._ensure_page()
        delay_s = 1.5
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                if self._page is None or self._request_count >= self.MAX_REQUESTS_PER_PAGE:
                    page = self._recreate_page() if self._page is not None else self._ensure_page()
                self._pace()
                dismiss_common_overlays(page)
                if self._context is None:
                    raise RuntimeError("TenderArena browser context is not available")
                response = self._context.request.get(
                    url,
                    headers={
                        "accept": "application/json",
                        "accept-language": "en-US,en;q=0.9",
                        "content-type": "application/json",
                        "origin": self.BASE,
                        "referer": f"{self.BASE}/",
                    },
                    fail_on_status_code=False,
                    timeout=self.timeout_ms,
                )
                self._last_request_at = time.monotonic()
                self._request_count += 1

                status = response.status
                text = response.text()
                if status in self.TRANSIENT_STATUS_CODES:
                    delay_s = self._retry_delay_for_status(status, response.headers, delay_s)
                    self._set_cooldown(delay_s)
                    raise RuntimeError(f"Transient response {status} for {url}")
                if status < 200 or status >= 300:
                    raise RuntimeError(f"Unexpected response {status} for {url}")
                text = (text or "").strip()
                if not text:
                    return {}
                try:
                    return json.loads(text)
                except JSONDecodeError as exc:
                    raise RuntimeError(f"Invalid JSON response for {url}: {exc}") from exc
            except Exception as exc:
                last_error = exc
                try:
                    message = str(exc)
                except Exception:
                    message = ""
                if (
                    "Failed to fetch" in message
                    or "Target page" in message
                    or "Execution context was destroyed" in message
                    or "Transient response 429" in message
                    or "Invalid JSON response" in message
                ):
                    try:
                        page = self._recreate_page()
                    except Exception:
                        pass

            if attempt == max_attempts:
                break

            time.sleep(delay_s + random.uniform(0.2, 0.6))
            delay_s = min(delay_s * 2, 30.0)

        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Failed to fetch TenderArena payload from {url}")

    @staticmethod
    def _parse_epoch_ms(value: Any) -> Optional[datetime]:
        if value in (None, ""):
            return None
        try:
            return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            return None

    @staticmethod
    def _first_non_empty(payload: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            value = payload.get(key)
            if value not in (None, "", [], {}):
                return value
        return None

    @staticmethod
    def _clean_text(value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @classmethod
    def _notice_url(cls, tender_id: int) -> str:
        return urljoin(cls.BASE, f"/dodavatel/zakazka/{tender_id}")

    def fetch_listing(
        self,
        *,
        limit: int | None,
    ) -> list[ScrapedTenderListingItem]:
        payload = self._fetch_json_via_browser(
            f"{self.LIST_URL}?t={int(datetime.now(tz=timezone.utc).timestamp() * 1000)}"
        )

        items: list[ScrapedTenderListingItem] = []
        rows = payload.get("polozky") or []
        if limit is not None:
            rows = rows[:limit]

        for raw in rows:
            tender_id = raw.get("id")
            if tender_id is None:
                continue
            try:
                tender_id_int = int(tender_id)
            except (TypeError, ValueError):
                continue

            items.append(
                ScrapedTenderListingItem(
                    tender_id=tender_id_int,
                    source_tender_id=str(tender_id_int),
                    buyer_id=self._clean_text(raw.get("idZadavatele")),
                    buyer_name=self._clean_text(raw.get("uredniNazevZadavatele")),
                    title=self._clean_text(raw.get("nazev")),
                    submission_deadline_at=self._parse_epoch_ms(raw.get("lhutaProPodaniNabidek")),
                    notice_url=self._notice_url(tender_id_int),
                )
            )
        return items

    def fetch_profile(self, *, buyer_id: str, timeout_ms: int = 30_000) -> dict[str, Any]:
        return self._fetch_json_via_browser(f"{self.PROFILE_URL}/{buyer_id}")

    def fetch_detail(
        self,
        *,
        tender_id: int,
        timeout_ms: int = 30_000,
        max_attempts: int = 4,
    ) -> dict[str, Any]:
        return self._fetch_json_via_browser(
            f"{self.DETAIL_URL}/{tender_id}",
            max_attempts=max_attempts,
        )

    def fetch_ai_summary(
        self,
        *,
        tender_id: int,
        timeout_ms: int = 30_000,
        max_attempts: int = 4,
    ) -> dict[str, Any]:
        return self._fetch_json_via_browser(
            f"{self.AI_SUMMARY_URL}/{tender_id}",
            max_attempts=max_attempts,
        )

    def detail_has_description(self, detail_payload: dict[str, Any]) -> bool:
        return bool(
            self._clean_text(
                self._first_non_empty(
                    detail_payload,
                    "strucnyPopis",
                    "popis",
                    "predmetZakazky",
                )
            )
        )

    def detail_docs(self, detail_payload: dict[str, Any]) -> list[dict[str, Any]]:
        docs = self._first_non_empty(
            detail_payload,
            "dokumenty",
            "seznamDokumentu",
            "dokumentace",
        )
        return list(docs or [])

    def detail_has_docs(self, detail_payload: dict[str, Any]) -> bool:
        return bool(self.detail_docs(detail_payload))

    def build_detail(
        self,
        *,
        listing_item: ScrapedTenderListingItem,
        detail_payload: dict[str, Any],
        ai_payload: dict[str, Any] | None,
        profile_payload: dict[str, Any] | None,
    ) -> ScrapedTenderDetail:
        ai_summary = (ai_payload or {}).get("manazerskeShrnutiZadavaciDokumentace") or {}
        ai_docs = (ai_payload or {}).get("dokumenty") or []
        detail_docs = self.detail_docs(detail_payload)
        profile_ident = (profile_payload or {}).get("identifikacniUdaje") or {}

        description = self._clean_text(
            self._first_non_empty(
                detail_payload,
                "strucnyPopis",
                "popis",
                "predmetZakazky",
            )
        ) or self._clean_text(ai_summary.get("predmetZakazky"))

        docs_source = ai_docs or detail_docs
        docs: list[ScrapedDoc] = []
        for raw in docs_source:
            docs.append(
                ScrapedDoc(
                    document_id=int(raw["id"]) if raw.get("id") is not None else None,
                    download_url=(
                        f"{self.DOWNLOAD_URL}/{int(raw['id'])}"
                        if raw.get("id") is not None
                        else None
                    ),
                    display_name=self._clean_text(
                        self._first_non_empty(raw, "nazev", "displayName", "popis")
                    ),
                    category=self._clean_text(self._first_non_empty(raw, "typ", "kategorie")),
                    published_at=self._clean_text(
                        self._first_non_empty(raw, "datumUverejneni", "publishedAt")
                    ),
                    filename=self._clean_text(
                        self._first_non_empty(raw, "nazev", "nazevSouboru", "fileName")
                    ),
                    size=self._clean_text(self._first_non_empty(raw, "velikost", "size")),
                )
            )

        return ScrapedTenderDetail(
            buyer_name=self._clean_text(
                self._first_non_empty(
                    detail_payload,
                    "uredniNazevZadavatele",
                    "nazevZadavatele",
                )
            )
            or listing_item.buyer_name
            or self._clean_text((profile_payload or {}).get("uredniNazev")),
            buyer_ico=self._clean_text(
                self._first_non_empty(
                    detail_payload,
                    "icoZadavatele",
                    "icZadavatele",
                )
            )
            or self._clean_text(profile_ident.get("ic")),
            title=self._clean_text(detail_payload.get("nazev")) or listing_item.title,
            description=description,
            submission_deadline_at=self._parse_epoch_ms(
                self._first_non_empty(
                    detail_payload,
                    "lhutaProPodaniNabidek",
                    "datumKonceLhuty",
                )
            )
            or listing_item.submission_deadline_at,
            bids_opening_at=self._parse_epoch_ms(
                self._first_non_empty(
                    detail_payload,
                    "datumOteviraniNabidek",
                    "datumOtevreniNabidek",
                )
            ),
            docs=docs,
        )

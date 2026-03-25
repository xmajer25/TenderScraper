from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from tenderscraper.config import settings

if TYPE_CHECKING:
    from playwright.sync_api import Page


@dataclass(frozen=True)
class PoptavejAuthConfig:
    """Auth config loaded from settings with optional env overrides.

    Uses `POPTAVEJ_USERNAME`, `POPTAVEJ_PASSWORD`, and optional
    `POPTAVEJ_STORAGE_STATE`.
    """

    username: str
    password: str
    storage_state_path: Path
    base_url: str = "https://www.poptavej.cz"
    start_url: str = "https://www.poptavej.cz/verejne-zakazky"

    @staticmethod
    def from_env() -> PoptavejAuthConfig:
        user = (settings.poptavej_username or os.getenv("POPTAVEJ_USERNAME") or "").strip()
        pwd = (settings.poptavej_password or os.getenv("POPTAVEJ_PASSWORD") or "").strip()
        if not user or not pwd:
            raise ValueError(
                "Missing POPTAVEJ_USERNAME / POPTAVEJ_PASSWORD in environment (.env)."
            )

        state = (os.getenv("POPTAVEJ_STORAGE_STATE") or "").strip()
        if state:
            state_path = Path(state)
        else:
            state_path = settings.default_poptavej_state_path

        return PoptavejAuthConfig(username=user, password=pwd, storage_state_path=state_path)


LOGIN_TRIGGER_SEL = "a[data-target='#modal_login']"
LOGIN_MODAL_SEL = "#modal_login"
LOGIN_INPUT_SEL = "#frm-logInForm-login"
PASSWORD_INPUT_SEL = "#frm-logInForm-heslo"
SUBMIT_BTN_SEL = "#frm-logInForm button[type='submit']"
ACCOUNT_LINK_SEL = "div.content a[href='/dodavatel/zaslane-poptavky']"


def ensure_storage_state(
    *,
    headless: bool = True,
    timeout_ms: int = 30_000,
    force_relogin: bool = False,
) -> Path:
    """Ensure poptavej storage state exists and is still valid.

    If missing or invalid, perform login and rewrite it.
    """
    from playwright.sync_api import sync_playwright

    cfg = PoptavejAuthConfig.from_env()
    cfg.storage_state_path.parent.mkdir(parents=True, exist_ok=True)

    if force_relogin or not cfg.storage_state_path.exists():
        login_and_save_state(headless=headless, timeout_ms=timeout_ms)
        return cfg.storage_state_path

    needs_relogin = False

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(storage_state=str(cfg.storage_state_path))
        page = context.new_page()
        try:
            page.goto(cfg.start_url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(300)
            if not _is_logged_in(page):
                needs_relogin = True
        finally:
            try:
                context.close()
            except Exception:
                pass
            browser.close()

    if needs_relogin:
        login_and_save_state(headless=headless, timeout_ms=timeout_ms)

    return cfg.storage_state_path


def login_and_save_state(*, headless: bool = True, timeout_ms: int = 30_000) -> Path:
    """Perform modal login and save Playwright storage state to disk."""
    from playwright.sync_api import TimeoutError as PWTimeoutError
    from playwright.sync_api import sync_playwright

    cfg = PoptavejAuthConfig.from_env()
    cfg.storage_state_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()

        try:
            page.goto(cfg.base_url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(300)

            _open_login_modal(page, timeout_ms=timeout_ms)
            page.locator(LOGIN_INPUT_SEL).fill(cfg.username, timeout=timeout_ms)
            page.locator(PASSWORD_INPUT_SEL).fill(cfg.password, timeout=timeout_ms)

            try:
                with page.expect_navigation(timeout=10_000):
                    page.locator(SUBMIT_BTN_SEL).click(timeout=timeout_ms)
            except PWTimeoutError:
                page.locator(SUBMIT_BTN_SEL).click(timeout=timeout_ms)

            _wait_modal_closed(page, timeout_ms=timeout_ms)

            if not _is_logged_in(page):
                raise RuntimeError(
                    "Login did not appear to succeed (no logged-in indicator found). "
                    "Either credentials are wrong, login is blocked, or selectors changed."
                )

            context.storage_state(path=str(cfg.storage_state_path))
            return cfg.storage_state_path
        finally:
            try:
                context.close()
            except Exception:
                pass
            browser.close()


def _open_login_modal(page: Page, *, timeout_ms: int) -> None:
    trigger = page.locator(LOGIN_TRIGGER_SEL).first
    trigger.wait_for(state="visible", timeout=timeout_ms)
    trigger.click(timeout=timeout_ms)
    page.locator(LOGIN_MODAL_SEL).wait_for(state="visible", timeout=timeout_ms)
    page.locator(LOGIN_INPUT_SEL).wait_for(state="visible", timeout=timeout_ms)
    page.locator(PASSWORD_INPUT_SEL).wait_for(state="visible", timeout=timeout_ms)


def _wait_modal_closed(page: Page, *, timeout_ms: int) -> None:
    modal = page.locator(LOGIN_MODAL_SEL).first
    try:
        modal.wait_for(state="hidden", timeout=timeout_ms)
        return
    except Exception:
        pass

    try:
        modal.wait_for(state="detached", timeout=timeout_ms)
        return
    except Exception:
        pass

    page.wait_for_timeout(500)


def _is_logged_in(page: Page) -> bool:
    """Return True when the page shows logged-in account state."""
    try:
        if page.locator(ACCOUNT_LINK_SEL).count() > 0:
            return True
    except Exception:
        pass

    try:
        trigger = page.locator(LOGIN_TRIGGER_SEL).first
        if trigger.count() == 0:
            return True
        if not trigger.is_visible():
            return True
    except Exception:
        pass

    return False

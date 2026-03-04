from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page, TimeoutError as PWTimeoutError, sync_playwright


@dataclass(frozen=True)
class PoptavejAuthConfig:
    """
    Auth config lives in env:
      - POPTAVEJ_USERNAME
      - POPTAVEJ_PASSWORD
      - Optional: POPTAVEJ_STORAGE_STATE (defaults to data/auth/poptavej_state.json)

    Keep credentials OUT of code. Keep storage state OUT of git.
    """

    username: str
    password: str
    storage_state_path: Path

    base_url: str = "https://www.poptavej.cz"
    start_url: str = "https://www.poptavej.cz/verejne-zakazky"

    @staticmethod
    def from_env() -> "PoptavejAuthConfig":
        user = (os.getenv("POPTAVEJ_USERNAME") or "").strip()
        pwd = (os.getenv("POPTAVEJ_PASSWORD") or "").strip()
        if not user or not pwd:
            raise ValueError(
                "Missing POPTAVEJ_USERNAME / POPTAVEJ_PASSWORD in environment (.env)."
            )

        state = (os.getenv("POPTAVEJ_STORAGE_STATE") or "").strip()
        if state:
            state_path = Path(state)
        else:
            state_path = Path("data") / "auth" / "poptavej_state.json"

        return PoptavejAuthConfig(username=user, password=pwd, storage_state_path=state_path)


# --- Selectors (keep them centralized; changes go here) ---
LOGIN_TRIGGER_SEL = "a[data-target='#modal_login']"
LOGIN_MODAL_SEL = "#modal_login"
LOGIN_INPUT_SEL = "#frm-logInForm-login"
PASSWORD_INPUT_SEL = "#frm-logInForm-heslo"
SUBMIT_BTN_SEL = "#frm-logInForm button[type='submit']"

# best-effort "logged-in" signals (we don't want brittle single selector)
ACCOUNT_LINK_SEL = "div.content a[href='/dodavatel/zaslane-poptavky']" 

def ensure_storage_state(
    *,
    headless: bool = True,
    timeout_ms: int = 30_000,
    force_relogin: bool = False,
) -> Path:
    """
    Ensure poptavej storage_state exists and is valid enough.
    If missing/invalid or force_relogin=True, performs login and rewrites it.

    Returns: path to storage state file.
    """
    cfg = PoptavejAuthConfig.from_env()
    cfg.storage_state_path.parent.mkdir(parents=True, exist_ok=True)

    if force_relogin or not cfg.storage_state_path.exists():
        login_and_save_state(headless=headless, timeout_ms=timeout_ms)
        return cfg.storage_state_path

    # Validate existing state by opening a page with that state and checking "logged-in" signals.
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(storage_state=str(cfg.storage_state_path))
        page = context.new_page()
        try:
            page.goto(cfg.start_url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(300)
            if not _is_logged_in(page):
                # state exists but isn't authenticated anymore
                browser.close()
                login_and_save_state(headless=headless, timeout_ms=timeout_ms)
                return cfg.storage_state_path
        finally:
            try:
                context.close()
            except Exception:
                pass
            browser.close()

    return cfg.storage_state_path


def login_and_save_state(*, headless: bool = True, timeout_ms: int = 30_000) -> Path:
    """
    Perform modal login and save Playwright storage state to disk.
    """
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

            # Fill credentials
            page.locator(LOGIN_INPUT_SEL).fill(cfg.username, timeout=timeout_ms)
            page.locator(PASSWORD_INPUT_SEL).fill(cfg.password, timeout=timeout_ms)

            # Submit — may cause ajax update OR navigation; handle both
            try:
                with page.expect_navigation(timeout=10_000):
                    page.locator(SUBMIT_BTN_SEL).click(timeout=timeout_ms)
            except PWTimeoutError:
                # No navigation: likely AJAX login. Just click and wait for modal to change.
                page.locator(SUBMIT_BTN_SEL).click(timeout=timeout_ms)

            # Wait for modal to close/stop blocking
            _wait_modal_closed(page, timeout_ms=timeout_ms)

            # Final verification (best-effort)
            if not _is_logged_in(page):
                # Don’t silently “succeed” with a broken auth state.
                raise RuntimeError(
                    "Login did not appear to succeed (no logged-in indicator found). "
                    "Either credentials are wrong, login is blocked, or selectors changed."
                )

            # Save state
            context.storage_state(path=str(cfg.storage_state_path))
            return cfg.storage_state_path

        finally:
            try:
                context.close()
            except Exception:
                pass
            browser.close()


def _open_login_modal(page: Page, *, timeout_ms: int) -> None:
    # Trigger is <a data-target="#modal_login">Přihlásit</a>
    trigger = page.locator(LOGIN_TRIGGER_SEL).first
    trigger.wait_for(state="visible", timeout=timeout_ms)
    trigger.click(timeout=timeout_ms)

    # Wait for modal + form inputs
    page.locator(LOGIN_MODAL_SEL).wait_for(state="visible", timeout=timeout_ms)
    page.locator(LOGIN_INPUT_SEL).wait_for(state="visible", timeout=timeout_ms)
    page.locator(PASSWORD_INPUT_SEL).wait_for(state="visible", timeout=timeout_ms)


def _wait_modal_closed(page: Page, *, timeout_ms: int) -> None:
    # Nette modals vary: sometimes hidden, sometimes detached. Accept both.
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

    # Worst case: modal stays but should stop blocking the page (avoid infinite wait)
    page.wait_for_timeout(500)


def _is_logged_in(page: Page) -> bool:
    """
    Best-effort detection. You didn’t provide a definitive post-login element,
    so we check several common options.
    """
    try:
        if page.locator(ACCOUNT_LINK_SEL).count() > 0:
            return True
    except Exception:
        pass

    # Another heuristic: the login trigger should disappear or become non-visible
    try:
        trig = page.locator(LOGIN_TRIGGER_SEL).first
        if trig.count() == 0:
            return True
        if not trig.is_visible():
            return True
    except Exception:
        pass

    return False

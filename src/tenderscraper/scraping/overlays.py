from __future__ import annotations

from playwright.sync_api import Page


def dismiss_common_overlays(page: Page) -> None:
    """
    Best-effort dismissal of common overlays (cookie banners, modal backdrops).
    Safe to call frequently.
    """
    # 1) Try closing cookie banners by common button texts (CZ/EN)
    candidates = [
        "Souhlasím", "Přijmout", "Přijmout vše", "Rozumím",
        "Accept", "Accept all", "I agree", "OK",
        "Zavřít", "Close",
    ]
    for text in candidates:
        btn = page.get_by_role("button", name=text)
        if btn.count() > 0:
            try:
                btn.first.click(timeout=500)
                page.wait_for_timeout(150)
            except Exception:
                pass

    # 2) Press Escape to close any open modal
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(100)
    except Exception:
        pass

    # 3) If a modal overlay/backdrop exists, click it (often closes modals)
    # TenderArena uses modal overlay divs; yours is 'modal-overlay'
    overlay = page.locator(".modal-overlay")
    if overlay.count() > 0:
        try:
            overlay.first.click(timeout=500, force=True)
            page.wait_for_timeout(150)
        except Exception:
            pass

def wait_overlay_gone(page: Page, timeout_ms: int = 5_000) -> None:
    overlay = page.locator(".modal-overlay")
    if overlay.count() > 0:
        try:
            overlay.first.wait_for(state="detached", timeout=timeout_ms)
        except Exception:
            # if it doesn't go away, we'll force click later
            pass
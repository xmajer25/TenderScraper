from __future__ import annotations

from playwright.sync_api import Page


def get_value_by_label(page: Page, label_text: str) -> str | None:
    """
    Finds a label with exact text and returns the nearby value text.
    Works with common 'label + value' blocks.
    """
    label = page.locator(
        f"xpath=//label[normalize-space()='{label_text}']"
    ).first

    if label.count() == 0:
        return None

    # Go up to the nearest output block and take text excluding the label.
    block = label.locator("xpath=ancestor::app-formular-output[1]")
    if block.count() == 0:
        # fallback: go to parent div
        block = label.locator("xpath=ancestor::div[1]")

    text = block.inner_text().strip()
    # remove the label itself from the text
    text = text.replace(label_text, "").strip()
    # value might still contain other labels; keep it simple for now
    return " ".join(text.split()) or None

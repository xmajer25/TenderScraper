from __future__ import annotations

import unicodedata

from playwright.sync_api import Page


def _normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.casefold().replace("\xa0", " ").split())


def _find_label(page: Page, label_text: str):
    target = _normalize_text(label_text)
    labels = page.locator("label")

    exact_match = None
    fuzzy_match = None
    for index in range(labels.count()):
        candidate = labels.nth(index)
        try:
            text = candidate.inner_text()
        except Exception:
            continue

        normalized = _normalize_text(text)
        if normalized == target:
            exact_match = candidate
            break
        if target in normalized and fuzzy_match is None:
            fuzzy_match = candidate

    return exact_match or fuzzy_match


def get_value_by_label(page: Page, label_text: str) -> str | None:
    """
    Finds a label by normalized text and returns the nearby value text.
    Matching ignores accents, case, repeated whitespace, and minor text drift.
    """
    label = _find_label(page, label_text)
    if label is None:
        return None

    block = label.locator("xpath=ancestor::app-formular-output[1]")
    if block.count() == 0:
        block = label.locator("xpath=ancestor::div[1]")

    text = block.inner_text().strip()
    label_text_live = label.inner_text().strip()
    text = text.replace(label_text_live, "").strip()
    return " ".join(text.split()) or None

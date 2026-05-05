from __future__ import annotations

import re
from typing import Any

_FRAMING_BOILERPLATE_SUBSTRINGS = (
    "copyright",
    "all rights reserved",
    "©",
    "subscribe",
    "sign up",
    "advertisement",
    "click here",
    "read more",
    "privacy policy",
    "terms of service",
)


def clean_text(text: str | None) -> str:
    if not text:
        return ""
    text = re.sub(r"\[\+?\d+\s*chars?\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\(\+?\d+\s*chars?\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"http\S+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def strip_chars_length_markers(text: str | None) -> str:
    """Backward-compatible alias: full API-safe cleanup including markers, HTML, and URLs."""
    return clean_text(text)


def _sentence_has_boilerplate(sentence: str) -> bool:
    low = sentence.lower()
    for needle in _FRAMING_BOILERPLATE_SUBSTRINGS:
        if needle == "©":
            if "©" in sentence:
                return True
        elif needle in low:
            return True
    return False


def _filter_framing_sentences(sentences: list[str]) -> list[str]:
    min_words = 6
    out: list[str] = []
    for raw in sentences:
        s = raw.strip()
        if len(s.split()) < min_words:
            continue
        if _sentence_has_boilerplate(s):
            continue
        out.append(s)
    return out


def get_framing_summary(articles: list[Any], topic: str, source: str) -> str:
    """
    Up to three sentences (each ≥6 words, no boilerplate) from the highest–relevance article.
    Returns "" if fewer than two usable sentences — callers should omit framing in the UI.
    """
    _ = topic
    _ = source

    if not articles:
        return ""

    sorted_articles = sorted(articles, key=lambda a: -int(getattr(a, "relevance_score", None) or 0))
    top = sorted_articles[0]
    raw_content = getattr(top, "content", None) or ""
    raw_title = getattr(top, "title", None) or ""

    body = clean_text(raw_content)
    body = re.sub(r"^\s*Lead:\s*", "", body, flags=re.IGNORECASE).strip()
    if not body:
        body = clean_text(raw_title)
        body = re.sub(r"^\s*Lead:\s*", "", body, flags=re.IGNORECASE).strip()

    if not body:
        return ""

    sentences = re.split(r"(?<=[.!?])\s+", body)
    good = _filter_framing_sentences(sentences)
    if len(good) < 2:
        return ""
    picked = good[:3]
    out = " ".join(picked).strip()
    return out

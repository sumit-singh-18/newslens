from __future__ import annotations

import re
from typing import Any


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


def get_framing_summary(articles: list[Any], topic: str, source: str) -> str:
    """
    First up to three sentences (6+ words each) from the highest–relevance_score article body.
    topic and source are accepted for API stability; filtering is the caller's responsibility.
    """
    _ = topic  # reserved for logging / future use
    _ = source

    fallback = "Coverage snapshot unavailable."

    if not articles:
        return fallback

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
        return fallback

    sentences = re.split(r"(?<=[.!?])\s+", body)
    good = [s.strip() for s in sentences if len(s.split()) >= 6]
    picked = good[:3]
    if picked:
        out = " ".join(picked).strip()
        return out if out else fallback

    head = body[:300].strip()
    return head if head else fallback

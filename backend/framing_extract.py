from __future__ import annotations

import re
from typing import Any

# Tokens that look capitalized but are not treated as named entities for the spam triple-check.
_JUNK_PROPER_LIKE = frozenset(
    {
        "copyright",
        "subscribe",
        "advertisement",
        "privacy",
        "terms",
        "read",
        "more",
        "click",
        "here",
        "all",
    }
)

_STOP_FIRST_WORD = frozenset(
    {
        "the",
        "a",
        "an",
        "but",
        "and",
        "or",
        "in",
        "on",
        "at",
        "to",
        "for",
        "as",
        "if",
        "it",
        "of",
        "by",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "not",
        "no",
        "we",
        "you",
        "they",
        "he",
        "she",
        "this",
        "that",
        "these",
        "those",
        "there",
        "then",
        "than",
        "with",
        "from",
        "into",
        "over",
        "also",
        "about",
        "after",
        "before",
    }
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


def _has_spam_triggers(s: str) -> bool:
    low = s.lower()
    if "©" in s:
        return True
    if "rights reserved" in low:
        return True
    if "subscribe" in low:
        return True
    if "sign up" in low:
        return True
    return False


def _looks_like_named_entity_present(s: str) -> bool:
    """Heuristic: capitalized tokens that look like proper nouns / acronyms (excluding boilerplate words)."""
    words = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", s)
    for i, w in enumerate(words):
        lw = w.lower()
        if lw in _JUNK_PROPER_LIKE:
            continue
        if i == 0 and lw in _STOP_FIRST_WORD:
            continue
        if len(w) >= 2 and w.isupper():
            return True
        if len(w) >= 3 and w[0].isupper() and w[1:].islower():
            return True
    return False


def _should_reject_sentence(s: str) -> bool:
    """
    Reject only if ALL three hold:
    - no named-entity-like tokens (heuristic),
    - under 8 words,
    - contains © / rights reserved / subscribe / sign up.
    """
    if len(s.split()) >= 8:
        return False
    if not _has_spam_triggers(s):
        return False
    if _looks_like_named_entity_present(s):
        return False
    return True


def _first_two_filtered_content_sentences(body: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", body)
    kept: list[str] = []
    for sent in sentences:
        s = sent.strip()
        if not s:
            continue
        if _should_reject_sentence(s):
            continue
        kept.append(s)
        if len(kept) >= 2:
            break
    return " ".join(kept).strip()


def get_framing_summary(articles: list[Any], topic: str, source: str) -> str:
    """
    1) Prefer NewsAPI-style description (stored on Article) — clean, rarely truncated.
    2) Else first two content sentences after spam triple-filter.
    3) Else empty string (UI hides framing).
    """
    _ = topic
    _ = source

    if not articles:
        return ""

    sorted_articles = sorted(articles, key=lambda a: -int(getattr(a, "relevance_score", None) or 0))
    top = sorted_articles[0]

    desc = clean_text(getattr(top, "description", None) or "")
    if desc and not _should_reject_sentence(desc):
        return desc.strip()

    raw_content = getattr(top, "content", None) or ""
    body = clean_text(raw_content)
    body = re.sub(r"^\s*Lead:\s*", "", body, flags=re.IGNORECASE).strip()
    if not body:
        return ""

    out = _first_two_filtered_content_sentences(body)
    return out

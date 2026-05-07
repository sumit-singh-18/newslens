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

_TITLE_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?")


def _topic_tokens_min_len4(topic: str) -> set[str]:
    """Topic-side tokens (4+ chars), excluding common stopwords so titles cannot match on e.g. 'with' alone."""
    stop = _STOP_FIRST_WORD
    out: set[str] = set()
    for m in _TITLE_WORD_RE.finditer(topic or ""):
        w = m.group(0).lower()
        if len(w) < 4 or w in stop:
            continue
        out.add(w)
    return out


def _title_tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in _TITLE_WORD_RE.finditer(text or "")}


def _title_shares_topic_word(title: str, topic_tokens: set[str]) -> bool:
    if not topic_tokens:
        return False
    return bool(_title_tokens(title) & topic_tokens)


def _framing_article_sort_key(a: Any) -> tuple[int, float]:
    rel = int(getattr(a, "relevance_score", None) or 0)
    pub = getattr(a, "published_at", None)
    if pub is None:
        ts = float("-inf")
    else:
        try:
            ts = float(pub.timestamp())
        except (OSError, ValueError, AttributeError):
            ts = float("-inf")
    return (-rel, -ts)


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

    Only articles whose title shares at least one token (4+ chars) with the topic are eligible.
    Eligible rows are sorted by relevance_score DESC, published_at DESC.
    """
    _ = source

    if not articles:
        return ""

    topic_tokens = _topic_tokens_min_len4(topic)
    if not topic_tokens:
        return ""

    eligible = [a for a in articles if _title_shares_topic_word(getattr(a, "title", None) or "", topic_tokens)]
    if not eligible:
        return ""

    top = sorted(eligible, key=_framing_article_sort_key)[0]

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

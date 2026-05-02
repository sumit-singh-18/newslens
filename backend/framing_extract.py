from __future__ import annotations

import re
from typing import Any


def first_n_words(text: str, n: int = 100) -> str:
    words = (text or "").split()
    if len(words) <= n:
        return " ".join(words).strip()
    return " ".join(words[:n]).strip()


def split_sentences(text: str, min_len: int = 25) -> list[str]:
    raw = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    out = [s.strip() for s in raw if len(s.strip()) >= min_len]
    return out


def _sentiment_charge(raw_sentiment: dict[str, float]) -> float:
    """Higher = stronger positive/negative vs neutral (\"sentiment-charged\")."""
    lower = {str(k).lower(): float(v) for k, v in raw_sentiment.items()}
    neu = lower.get("neutral", 0.0)
    return 1.0 - neu


def extractive_framing_summary(nlp: Any, corpus: str, k: int = 2) -> str:
    """
    Pick the k sentences with the highest sentiment charge (1 - P(neutral))
    from the corpus using the same sentiment model as the rest of the pipeline.
    """
    corpus = (corpus or "").strip()
    if not corpus:
        return ""
    sents = split_sentences(corpus)
    if not sents:
        return corpus[:800]
    if len(sents) <= k:
        return " ".join(sents)
    analyses = nlp.analyze_batch(sents)
    scored: list[tuple[float, str]] = []
    for sent, an in zip(sents, analyses):
        rs = an.get("raw_scores") or {}
        sent_raw = rs.get("sentiment") if isinstance(rs, dict) else {}
        if not isinstance(sent_raw, dict):
            sent_raw = {}
        ch = _sentiment_charge(sent_raw)
        scored.append((ch, sent))
    scored.sort(key=lambda item: -item[0])
    return " ".join(s for _, s in scored[:k])


def build_outlet_corpus_snippets(article_rows: list[dict[str, Any]]) -> str:
    """title + first 100 words per article, concatenated."""
    parts: list[str] = []
    for row in article_rows:
        title = (row.get("title") or "").strip()
        body = (row.get("content") or "").strip()
        snippet = first_n_words(body, 100)
        if title and snippet:
            parts.append(f"{title}. {snippet}")
        elif title:
            parts.append(title)
        elif snippet:
            parts.append(snippet)
    return " ".join(parts).strip()

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Lower than previous 25 so short leads become eligible for scoring.
SENTENCE_MIN_LEN = 10


def first_n_words(text: str, n: int = 100) -> str:
    words = (text or "").split()
    if len(words) <= n:
        return " ".join(words).strip()
    return " ".join(words[:n]).strip()


def split_sentences(text: str, min_len: int | None = None) -> list[str]:
    ml = SENTENCE_MIN_LEN if min_len is None else min_len
    raw = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    out = [s.strip() for s in raw if len(s.strip()) >= ml]
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
        logger.info("[NewsLens] framing extractive: empty corpus")
        return ""
    sents = split_sentences(corpus)
    logger.info(
        "[NewsLens] framing extractive: extracted %d sentences (min_len=%d), corpus_chars=%d",
        len(sents),
        SENTENCE_MIN_LEN,
        len(corpus),
    )
    if not sents:
        logger.info("[NewsLens] framing extractive: no sentences met min_len; using corpus head")
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
    for rank, (ch, sent) in enumerate(scored[: min(8, len(scored))], start=1):
        logger.info(
            "[NewsLens] framing sentence rank %d: charge=%.5f chars=%d preview=%s",
            rank,
            ch,
            len(sent),
            (sent[:120] + "…") if len(sent) > 120 else sent,
        )

    if all(t[0] < 1e-5 for t in scored):
        logger.info(
            "[NewsLens] framing extractive: all sentence charges ~0 (neutral); using first %d sentences fallback",
            k,
        )
        return " ".join(sents[:k])

    top_k = [s for _, s in scored[:k]]
    joined = " ".join(top_k).strip()
    if not joined:
        logger.info("[NewsLens] framing extractive: top-k join empty after scoring; using first %d sentences", k)
        return " ".join(sents[:k])
    return joined


def build_outlet_corpus_snippets(article_rows: list[dict[str, Any]]) -> str:
    """title + first 100 words per article, concatenated (highest relevance first)."""
    rows = sorted(article_rows, key=lambda r: -int(r.get("relevance_score") or 0))
    parts: list[str] = []
    for row in rows:
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


def fallback_framing_best_article(
    rows: list[dict[str, Any]],
    nlp: Any,
    n_sentences: int = 2,
) -> str:
    """First n sentences from the best sentiment-charged piece; rows ordered by relevance_score first."""
    if not rows:
        return ""
    rows_sorted = sorted(rows, key=lambda r: -int(r.get("relevance_score") or 0))
    top_title = (rows_sorted[0].get("title") or "").strip()
    stitched: list[str] = []
    for r in rows_sorted:
        title = (r.get("title") or "").strip()
        body = (r.get("content") or "").strip()
        if title and body:
            stitched.append(f"{title}. {body}")
        elif body:
            stitched.append(body)
        elif title:
            stitched.append(title)
    if not stitched:
        return top_title
    analyses = nlp.analyze_batch(stitched)
    best_i = 0
    best_charge = -1.0
    for i, an in enumerate(analyses):
        rs = (an.get("raw_scores") or {}).get("sentiment") if isinstance(an.get("raw_scores"), dict) else {}
        if not isinstance(rs, dict):
            rs = {}
        ch = _sentiment_charge(rs)
        if ch > best_charge:
            best_charge = ch
            best_i = i
    best_row = rows_sorted[best_i]
    title = (best_row.get("title") or "").strip()
    body = (best_row.get("content") or "").strip()
    blob = f"{title}. {body}" if title and body else (body or title)
    sents = split_sentences(blob)
    if len(sents) >= n_sentences:
        return " ".join(sents[:n_sentences])
    if sents:
        return " ".join(sents)
    if top_title:
        return top_title
    return (blob or "")[:1200]

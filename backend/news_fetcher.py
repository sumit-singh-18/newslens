from __future__ import annotations

import logging
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .database import Article, ArticleScore, TopicOutletFraming
from .framing_extract import (
    build_outlet_corpus_snippets,
    extractive_framing_summary,
    fallback_framing_best_article,
)
from .nlp_pipeline import NLPPipeline

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

logger = logging.getLogger(__name__)

NEWSAPI_EVERYTHING = "https://newsapi.org/v2/everything"

# Credibility-ranked allowlist — NewsAPI publisher IDs only (see https://newsapi.org/sources).
# Tier 1: wires · Tier 2: broadcast · Tier 3: major papers · Tier 4: cable/finance.
CREDIBLE_SOURCES_RANKED: tuple[str, ...] = (
    "associated-press",
    "reuters",
    "bbc-news",
    "nbc-news",
    "abc-news",
    "cbs-news",
    "npr",
    "the-washington-post",
    "the-wall-street-journal",
    "the-guardian-uk",
    "the-new-york-times",
    "cnn",
    "fox-news",
    "msnbc",
    "bloomberg",
)

SOURCE_DISPLAY_NAMES: dict[str, str] = {
    "associated-press": "Associated Press",
    "reuters": "Reuters",
    "bbc-news": "BBC News",
    "nbc-news": "NBC News",
    "abc-news": "ABC News",
    "cbs-news": "CBS News",
    "npr": "NPR",
    "the-washington-post": "Washington Post",
    "the-wall-street-journal": "Wall Street Journal",
    "the-guardian-uk": "The Guardian",
    "the-new-york-times": "New York Times",
    "cnn": "CNN",
    "fox-news": "Fox News",
    "msnbc": "MSNBC",
    "bloomberg": "Bloomberg",
}

ALLOWED_SOURCE_IDS: frozenset[str] = frozenset(CREDIBLE_SOURCES_RANKED)
ALLOWED_OUTLET_DISPLAY_NAMES: frozenset[str] = frozenset(SOURCE_DISPLAY_NAMES.values())
OUTLET_DISPLAY_RANK: dict[str, int] = {
    SOURCE_DISPLAY_NAMES[sid]: i for i, sid in enumerate(CREDIBLE_SOURCES_RANKED)
}

NEWSAPI_CREDIBLE_SOURCES_PARAM = ",".join(CREDIBLE_SOURCES_RANKED)

TOP_OUTLET_SLOTS = 5
MIN_ARTICLES_PER_SOURCE = 1
MIN_CREDIBLE_OUTLETS = 3
NEWSAPI_PAGE_SIZE = 100
COVERAGE_SHORTFALL_MESSAGE = "Not enough credible coverage found for this topic yet"


class NewsFetcherError(Exception):
    pass


def clean_text(text: str | None) -> str:
    if not text:
        return ""

    cleaned = re.sub(r"<[^>]+>", " ", text)
    cleaned = re.sub(r"https?://\S+|www\.\S+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _parse_newsapi_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _log_source_article_counts(stage: str, by_source: dict[str, list[Any]]) -> None:
    counts = {src: len(items) for src, items in sorted(by_source.items(), key=lambda x: x[0].lower())}
    logger.info(
        "[NewsLens] %s — %d outlets, articles per source: %s",
        stage,
        len(counts),
        counts,
    )


def _qualifying_source_names(by_source: dict[str, list[Any]], min_art: int) -> list[str]:
    eligible = [s for s, items in by_source.items() if len(items) >= min_art]
    eligible.sort(key=lambda s: (-len(by_source[s]), OUTLET_DISPLAY_RANK.get(s, 999)))
    return eligible


def _ingest_articles_into_buckets(
    incoming_articles: list[dict[str, Any]],
    by_source: dict[str, list[dict[str, Any]]],
    seen_urls: set[str],
    *,
    allowed_source_ids: frozenset[str],
    source_id_to_display: dict[str, str],
) -> int:
    added = 0
    for item in incoming_articles:
        src_obj = item.get("source") or {}
        source_id = (src_obj.get("id") or "").strip()
        if not source_id or source_id not in allowed_source_ids:
            continue
        source_name = source_id_to_display.get(source_id) or (src_obj.get("name") or "").strip()
        if not source_name:
            continue
        url = (item.get("url") or "").strip()
        title = clean_text(item.get("title"))
        content = clean_text(item.get("content")) or clean_text(item.get("description"))

        if not url or url in seen_urls:
            continue
        if not title or not content:
            continue

        seen_urls.add(url)
        by_source[source_name].append(
            {
                "source": source_name,
                "url": url,
                "title": title,
                "content": content,
                "published_at_raw": item.get("publishedAt"),
            }
        )
        added += 1
    return added


def _get_recent_cached_articles(topic: str, db: Session) -> list[Article]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    statement = (
        select(Article)
        .where(Article.topic == topic, Article.fetched_at >= cutoff)
        .order_by(Article.fetched_at.desc())
    )
    return list(db.scalars(statement).all())


def compute_selected_outlets_from_db(topic: str, db: Session) -> list[str]:
    """Top approved sources by article count for this topic (>= min articles), max TOP_OUTLET_SLOTS."""
    rows = db.execute(
        select(Article.source, func.count(Article.id))
        .where(Article.topic == topic)
        .group_by(Article.source)
    ).all()
    eligible = [
        (s, int(c))
        for s, c in rows
        if c >= MIN_ARTICLES_PER_SOURCE and s in ALLOWED_OUTLET_DISPLAY_NAMES
    ]
    eligible.sort(key=lambda x: (-x[1], OUTLET_DISPLAY_RANK.get(x[0], 999)))
    return [s for s, _ in eligible[:TOP_OUTLET_SLOTS]]


def _invalidate_topic_articles(topic: str, db: Session) -> None:
    db.execute(delete(TopicOutletFraming).where(TopicOutletFraming.topic == topic))
    db.execute(delete(Article).where(Article.topic == topic))
    db.commit()


async def fetch_and_store_articles(topic: str, db: Session) -> dict[str, Any]:
    topic = topic.strip()
    if not topic:
        raise NewsFetcherError("Topic must not be empty.")

    cached_articles = _get_recent_cached_articles(topic, db)
    if cached_articles:
        selected = compute_selected_outlets_from_db(topic, db)
        if selected:
            return {
                "cached": True,
                "count": len(cached_articles),
                "saved_urls": [],
                "selected_outlets": selected,
            }
        _invalidate_topic_articles(topic, db)

    news_api_key = os.getenv("NEWSAPI_KEY")
    if not news_api_key or "your_newsapi_key_here" in news_api_key:
        raise NewsFetcherError("Missing valid NEWSAPI_KEY in environment.")

    headers = {"X-Api-Key": news_api_key}
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_urls: set[str] = set()

    params_everything = {
        "q": topic,
        "sources": NEWSAPI_CREDIBLE_SOURCES_PARAM,
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": NEWSAPI_PAGE_SIZE,
    }

    try:
        async with httpx.AsyncClient(timeout=40.0) as client:
            r1 = await client.get(NEWSAPI_EVERYTHING, params=params_everything, headers=headers)
            r1.raise_for_status()
            payload1 = r1.json()
            if payload1.get("status") != "ok":
                raise NewsFetcherError(f"NewsAPI returned error: {payload1.get('message', 'unknown error')}")
            n1 = _ingest_articles_into_buckets(
                payload1.get("articles") or [],
                by_source,
                seen_urls,
                allowed_source_ids=ALLOWED_SOURCE_IDS,
                source_id_to_display=SOURCE_DISPLAY_NAMES,
            )
            _log_source_article_counts(f"everything(credible allowlist) ingested={n1}", by_source)

    except httpx.HTTPError as exc:
        raise NewsFetcherError(f"NewsAPI request failed: {exc}") from exc

    eligible_sources = _qualifying_source_names(by_source, MIN_ARTICLES_PER_SOURCE)
    if len(eligible_sources) < MIN_CREDIBLE_OUTLETS:
        return {
            "cached": False,
            "count": 0,
            "saved_urls": [],
            "selected_outlets": [],
            "coverage_message": COVERAGE_SHORTFALL_MESSAGE,
        }

    selected_sources = eligible_sources[:TOP_OUTLET_SLOTS]

    flat_raw: list[dict[str, Any]] = []
    for src in selected_sources:
        flat_raw.extend(by_source[src])

    if not flat_raw:
        return {
            "cached": False,
            "count": 0,
            "saved_urls": [],
            "selected_outlets": [],
            "coverage_message": COVERAGE_SHORTFALL_MESSAGE,
        }

    urls_flat = [r["url"] for r in flat_raw]
    blocked_rows = db.execute(select(Article.url).where(Article.url.in_(urls_flat))).all()
    blocked = {str(r[0]) for r in blocked_rows if r[0]}
    if blocked:
        logger.info(
            "[NewsLens] dropping %d articles whose URLs already exist for another topic",
            len(blocked),
        )
        flat_raw = [r for r in flat_raw if r["url"] not in blocked]

    if not flat_raw:
        return {
            "cached": False,
            "count": 0,
            "saved_urls": [],
            "selected_outlets": [],
            "coverage_message": COVERAGE_SHORTFALL_MESSAGE,
        }

    flat_by_src: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in flat_raw:
        flat_by_src[r["source"]].append(r)
    reranked = sorted(
        flat_by_src.keys(),
        key=lambda s: (-len(flat_by_src[s]), OUTLET_DISPLAY_RANK.get(s, 999)),
    )
    if len(reranked) < MIN_CREDIBLE_OUTLETS:
        return {
            "cached": False,
            "count": 0,
            "saved_urls": [],
            "selected_outlets": [],
            "coverage_message": COVERAGE_SHORTFALL_MESSAGE,
        }

    selected_sources = reranked[:TOP_OUTLET_SLOTS]
    flat_raw = []
    for src in selected_sources:
        flat_raw.extend(flat_by_src[src])

    texts = [row["content"] for row in flat_raw]
    nlp = NLPPipeline.get_instance()
    analyses = nlp.analyze_batch(texts)
    if len(flat_raw) != len(analyses):
        raise NewsFetcherError("NLP analysis count does not match article count.")

    now_utc = datetime.now(timezone.utc)
    snapshot_date = now_utc.date()
    saved_urls: list[str] = []

    db.execute(delete(TopicOutletFraming).where(TopicOutletFraming.topic == topic))
    db.execute(delete(Article).where(Article.topic == topic))
    db.flush()

    for raw_row, analysis in zip(flat_raw, analyses):
        article = Article(
            topic=topic,
            source=raw_row["source"],
            url=raw_row["url"],
            title=raw_row["title"],
            content=raw_row["content"],
            published_at=_parse_newsapi_datetime(raw_row.get("published_at_raw")),
            fetched_at=now_utc,
            snapshot_date=snapshot_date,
        )
        db.add(article)
        db.flush()
        db.add(
            ArticleScore(
                article_id=article.id,
                sentiment_label=analysis["sentiment_label"],
                sentiment_score=analysis["sentiment_score"],
                bias_label=analysis["bias_label"],
                bias_score=analysis["bias_score"],
                raw_scores=analysis["raw_scores"],
            )
        )
        saved_urls.append(raw_row["url"])

    for src in selected_sources:
        rows_for_src = [r for r in flat_raw if r["source"] == src]
        corpus = build_outlet_corpus_snippets(rows_for_src)
        framing_text = extractive_framing_summary(nlp, corpus, k=2)
        if not framing_text.strip():
            framing_text = fallback_framing_best_article(rows_for_src, nlp, n_sentences=2)
        if not framing_text.strip() and corpus.strip():
            framing_text = corpus[:1200]
        if not framing_text.strip() and rows_for_src:
            framing_text = (rows_for_src[0].get("title") or "").strip() or corpus[:800]
        db.add(
            TopicOutletFraming(
                topic=topic,
                source=src,
                framing_summary=framing_text.strip() or "Coverage snapshot unavailable.",
                updated_at=now_utc,
            )
        )

    db.commit()
    return {
        "cached": False,
        "count": len(saved_urls),
        "saved_urls": saved_urls,
        "selected_outlets": selected_sources,
    }

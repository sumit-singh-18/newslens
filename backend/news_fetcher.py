from __future__ import annotations

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
from .framing_extract import build_outlet_corpus_snippets, extractive_framing_summary
from .nlp_pipeline import NLPPipeline

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")


NEWSAPI_ENDPOINT = "https://newsapi.org/v2/everything"

# Broader pool (15+). Slugs must match NewsAPI publisher IDs (see https://newsapi.org/sources).
# Pool per product brief (15+). IDs must match NewsAPI `sources` (invalid IDs fail the whole request).
NEWSAPI_BROAD_SOURCES = (
    "bbc-news,reuters,cnn,fox-news,al-jazeera-english,"
    "the-guardian-uk,nbc-news,abc-news,associated-press,bloomberg,"
    "the-washington-post,msnbc,politico,the-wall-street-journal,npr"
)

TOP_OUTLET_SLOTS = 5
MIN_ARTICLES_PER_SOURCE = 2
NEWSAPI_PAGE_SIZE = 100


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


def _get_recent_cached_articles(topic: str, db: Session) -> list[Article]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    statement = (
        select(Article)
        .where(Article.topic == topic, Article.fetched_at >= cutoff)
        .order_by(Article.fetched_at.desc())
    )
    return list(db.scalars(statement).all())


def compute_selected_outlets_from_db(topic: str, db: Session) -> list[str]:
    """Top sources by article count for this topic (>= min articles), max TOP_OUTLET_SLOTS."""
    rows = db.execute(
        select(Article.source, func.count(Article.id))
        .where(Article.topic == topic)
        .group_by(Article.source)
    ).all()
    eligible = [(s, int(c)) for s, c in rows if c >= MIN_ARTICLES_PER_SOURCE]
    eligible.sort(key=lambda x: -x[1])
    return [s for s, _ in eligible[:TOP_OUTLET_SLOTS]]


async def fetch_and_store_articles(topic: str, db: Session) -> dict[str, Any]:
    topic = topic.strip()
    if not topic:
        raise NewsFetcherError("Topic must not be empty.")

    cached_articles = _get_recent_cached_articles(topic, db)
    if cached_articles:
        selected = compute_selected_outlets_from_db(topic, db)
        return {
            "cached": True,
            "count": len(cached_articles),
            "saved_urls": [],
            "selected_outlets": selected,
        }

    news_api_key = os.getenv("NEWSAPI_KEY")
    if not news_api_key or "your_newsapi_key_here" in news_api_key:
        raise NewsFetcherError("Missing valid NEWSAPI_KEY in environment.")

    params = {
        "q": topic,
        "sources": NEWSAPI_BROAD_SOURCES,
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": NEWSAPI_PAGE_SIZE,
    }
    headers = {"X-Api-Key": news_api_key}

    try:
        async with httpx.AsyncClient(timeout=40.0) as client:
            response = await client.get(NEWSAPI_ENDPOINT, params=params, headers=headers)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        raise NewsFetcherError(f"NewsAPI request failed: {exc}") from exc

    if payload.get("status") != "ok":
        raise NewsFetcherError(f"NewsAPI returned error: {payload.get('message', 'unknown error')}")

    incoming_articles = payload.get("articles", [])
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_urls: set[str] = set()

    for item in incoming_articles:
        source_name = ((item.get("source") or {}).get("name") or "").strip()
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

    eligible_sources = [s for s, items in by_source.items() if len(items) >= MIN_ARTICLES_PER_SOURCE]
    eligible_sources.sort(key=lambda s: len(by_source[s]), reverse=True)
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
        }

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
            framing_text = corpus[:1200] if corpus else "No extractive framing available."
        db.add(
            TopicOutletFraming(
                topic=topic,
                source=src,
                framing_summary=framing_text,
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

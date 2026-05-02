from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import Article
from .nlp_pipeline import NLPPipeline

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")


NEWSAPI_ENDPOINT = "https://newsapi.org/v2/everything"
ALLOWED_OUTLETS = ["BBC News", "Reuters", "Fox News", "CNN", "Al Jazeera English"]


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


async def fetch_and_store_articles(topic: str, db: Session, page_size: int = 25) -> dict[str, Any]:
    topic = topic.strip()
    if not topic:
        raise NewsFetcherError("Topic must not be empty.")

    cached_articles = _get_recent_cached_articles(topic, db)
    if cached_articles:
        return {"cached": True, "count": len(cached_articles), "saved_urls": []}

    news_api_key = os.getenv("NEWSAPI_KEY")
    if not news_api_key or "your_newsapi_key_here" in news_api_key:
        raise NewsFetcherError("Missing valid NEWSAPI_KEY in environment.")

    params = {
        "q": topic,
        "sources": "bbc-news,reuters,fox-news,cnn,al-jazeera-english",
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": page_size,
    }
    headers = {"X-Api-Key": news_api_key}

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(NEWSAPI_ENDPOINT, params=params, headers=headers)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        raise NewsFetcherError(f"NewsAPI request failed: {exc}") from exc

    if payload.get("status") != "ok":
        raise NewsFetcherError(f"NewsAPI returned error: {payload.get('message', 'unknown error')}")

    incoming_articles = payload.get("articles", [])
    seen_urls: set[str] = set()
    saved_urls: list[str] = []
    now_utc = datetime.now(timezone.utc)
    snapshot_date = now_utc.date()

    for item in incoming_articles:
        source_name = (item.get("source") or {}).get("name", "")
        if source_name not in ALLOWED_OUTLETS:
            continue

        url = (item.get("url") or "").strip()
        title = clean_text(item.get("title"))
        content = clean_text(item.get("content")) or clean_text(item.get("description"))

        if not url or url in seen_urls:
            continue
        seen_urls.add(url)

        existing = db.scalar(select(Article).where(Article.url == url))
        if existing:
            continue

        if not title or not content:
            continue

        article = Article(
            topic=topic,
            source=source_name,
            url=url,
            title=title,
            content=content,
            published_at=_parse_newsapi_datetime(item.get("publishedAt")),
            fetched_at=now_utc,
            snapshot_date=snapshot_date,
        )
        db.add(article)
        saved_urls.append(url)

    db.commit()
    if saved_urls:
        # Ensure every persisted article is scored before any consumer reads the DB.
        NLPPipeline.get_instance().score_topic_articles(topic, db)
    return {"cached": False, "count": len(saved_urls), "saved_urls": saved_urls}

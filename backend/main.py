from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, select, text
from sqlalchemy.orm import Session

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

from .database import Article, ArticleScore, create_tables, get_db
from .llm_analyzer import LLMAnalyzer
from .news_fetcher import ALLOWED_OUTLETS, NewsFetcherError, fetch_and_store_articles
from .nlp_pipeline import NLPPipeline

_nlp_pipeline: NLPPipeline | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _nlp_pipeline
    create_tables()
    _nlp_pipeline = NLPPipeline.get_instance()
    yield


app = FastAPI(title="NewsLens Backend", lifespan=lifespan)
router = APIRouter()


class HealthResponse(BaseModel):
    success: bool
    data: dict
    error: Optional[str]


class ScoresResponse(BaseModel):
    success: bool
    data: dict
    error: Optional[str]


class AnalyzeResponse(BaseModel):
    success: bool
    data: dict
    error: Optional[str]


def get_nlp_pipeline() -> NLPPipeline:
    if _nlp_pipeline is None:
        return NLPPipeline.get_instance()
    return _nlp_pipeline


def _build_outlet_scores(topic: str, db: Session) -> dict:
    rows = db.execute(
        select(
            Article.id,
            Article.source,
            ArticleScore.sentiment_score,
            ArticleScore.sentiment_label,
            ArticleScore.bias_score,
            ArticleScore.bias_label,
            ArticleScore.created_at,
        )
        .join(ArticleScore, ArticleScore.article_id == Article.id)
        .where(Article.topic == topic)
        .order_by(Article.id.asc(), desc(ArticleScore.created_at))
    ).all()

    latest_by_article: dict[int, dict] = {}
    for row in rows:
        if row.id in latest_by_article:
            continue
        latest_by_article[row.id] = {
            "source": row.source,
            "sentiment_score": row.sentiment_score,
            "sentiment_label": row.sentiment_label,
            "bias_score": row.bias_score,
            "bias_label": row.bias_label,
        }

    outlet_scores: dict[str, dict] = {}
    for item in latest_by_article.values():
        source = item["source"]
        outlet_scores.setdefault(
            source,
            {
                "source": source,
                "article_count": 0,
                "avg_sentiment_score": 0.0,
                "avg_bias_score": 0.0,
                "sentiment_labels": {},
                "bias_labels": {},
            },
        )
        outlet_scores[source]["article_count"] += 1
        outlet_scores[source]["avg_sentiment_score"] += float(item["sentiment_score"] or 0.0)
        outlet_scores[source]["avg_bias_score"] += float(item["bias_score"] or 0.0)
        sentiment_label = item["sentiment_label"] or "Unknown"
        bias_label = item["bias_label"] or "Unknown"
        outlet_scores[source]["sentiment_labels"][sentiment_label] = (
            outlet_scores[source]["sentiment_labels"].get(sentiment_label, 0) + 1
        )
        outlet_scores[source]["bias_labels"][bias_label] = (
            outlet_scores[source]["bias_labels"].get(bias_label, 0) + 1
        )

    outlets = []
    for source in sorted(ALLOWED_OUTLETS):
        if source not in outlet_scores:
            outlets.append(
                {
                    "source": source,
                    "article_count": 0,
                    "avg_sentiment_score": None,
                    "avg_bias_score": None,
                    "sentiment_labels": {},
                    "bias_labels": {},
                    "dominant_sentiment_label": None,
                    "dominant_bias_label": None,
                }
            )
            continue

        stats = outlet_scores[source]
        article_count = stats["article_count"]
        stats["avg_sentiment_score"] = round(stats["avg_sentiment_score"] / article_count, 4)
        stats["avg_bias_score"] = round(stats["avg_bias_score"] / article_count, 4)
        stats["dominant_sentiment_label"] = max(
            stats["sentiment_labels"], key=lambda label: stats["sentiment_labels"][label]
        )
        stats["dominant_bias_label"] = max(stats["bias_labels"], key=lambda label: stats["bias_labels"][label])
        outlets.append(stats)

    return {
        "topic": topic,
        "article_count": len(latest_by_article),
        "outlet_count": len(outlets),
        "outlets": outlets,
    }


def _build_headline_map(topic: str, db: Session) -> dict[str, str | None]:
    rows = db.execute(
        select(Article.source, Article.title)
        .where(Article.topic == topic)
        .order_by(Article.published_at.desc().nullslast(), Article.fetched_at.desc())
    ).all()

    headlines: dict[str, str | None] = {source: None for source in ALLOWED_OUTLETS}
    for row in rows:
        if headlines.get(row.source):
            continue
        headlines[row.source] = row.title
    return headlines


def _build_bias_timeline(topic: str, db: Session, days: int = 7) -> list[dict]:
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=days - 1)
    rows = db.execute(
        select(
            Article.id,
            Article.source,
            Article.snapshot_date,
            ArticleScore.bias_score,
            ArticleScore.created_at,
        )
        .join(ArticleScore, ArticleScore.article_id == Article.id)
        .where(Article.topic == topic, Article.snapshot_date >= start_date, Article.snapshot_date <= end_date)
        .order_by(Article.id.asc(), desc(ArticleScore.created_at))
    ).all()

    latest_by_article: dict[int, dict] = {}
    for row in rows:
        if row.id in latest_by_article:
            continue
        latest_by_article[row.id] = {
            "source": row.source,
            "snapshot_date": row.snapshot_date,
            "bias_score": row.bias_score,
        }

    day_cursor = start_date
    buckets = {}
    while day_cursor <= end_date:
        key = day_cursor.isoformat()
        buckets[key] = {"date": key}
        for source in ALLOWED_OUTLETS:
            buckets[key][source] = None
        day_cursor += timedelta(days=1)

    grouped: dict[tuple[str, str], list[float]] = {}
    for item in latest_by_article.values():
        if item["bias_score"] is None:
            continue
        key = (item["snapshot_date"].isoformat(), item["source"])
        grouped.setdefault(key, []).append(float(item["bias_score"]))

    for (snapshot_date, source), scores in grouped.items():
        if snapshot_date not in buckets:
            continue
        buckets[snapshot_date][source] = round(sum(scores) / len(scores), 4)

    return [buckets[key] for key in sorted(buckets.keys())]


@router.get("/health", response_model=HealthResponse)
def health_check(db: Session = Depends(get_db)) -> HealthResponse:
    db.execute(text("SELECT 1"))
    return {
        "success": True,
        "data": {
            "service": "newslens-backend",
            "status": "ok",
            "debug": os.getenv("DEBUG", "False"),
        },
        "error": None,
    }


@router.get("/scores", response_model=ScoresResponse)
def get_scores(
    topic: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
    nlp_pipeline: NLPPipeline = Depends(get_nlp_pipeline),
) -> ScoresResponse:
    normalized_topic = topic.strip()
    if not normalized_topic:
        raise HTTPException(status_code=400, detail="Topic must not be empty.")

    score_result = nlp_pipeline.score_topic_articles(normalized_topic, db)
    if score_result["article_count"] == 0:
        return {"success": True, "data": {"topic": normalized_topic, "outlets": []}, "error": None}

    return {
        "success": True,
        "data": _build_outlet_scores(normalized_topic, db),
        "error": None,
    }


@router.get("/analyze", response_model=AnalyzeResponse)
async def analyze_topic(
    topic: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
    nlp_pipeline: NLPPipeline = Depends(get_nlp_pipeline),
) -> AnalyzeResponse:
    normalized_topic = topic.strip()
    if not normalized_topic:
        raise HTTPException(status_code=400, detail="Topic must not be empty.")

    fetch_meta: dict = {"cached": True, "count": 0, "saved_urls": []}
    try:
        # Always run through fetcher so its 24-hour cache policy decides freshness.
        fetch_meta = await fetch_and_store_articles(normalized_topic, db)
    except NewsFetcherError as exc:
        fetch_meta = {"cached": True, "count": 0, "saved_urls": [], "warning": str(exc)}

    score_result = nlp_pipeline.score_topic_articles(normalized_topic, db)
    score_data = _build_outlet_scores(normalized_topic, db)

    llm_result = LLMAnalyzer().generate_missing_angle(normalized_topic, db)
    llm_data = llm_result.get("data", {})
    headlines = _build_headline_map(normalized_topic, db)
    timeline = _build_bias_timeline(normalized_topic, db, days=7)

    outlet_missing_angles = llm_data.get("outlet_missing_angles", {})
    outlets_with_missing_angle = []
    for outlet in score_data["outlets"]:
        source = outlet["source"]
        merged_outlet = dict(outlet)
        merged_outlet["missing_angle"] = outlet_missing_angles.get(source)
        merged_outlet["headline"] = headlines.get(source)
        outlets_with_missing_angle.append(merged_outlet)

    reasoning = (
        f"Confidence: {llm_data.get('confidence') or 'unknown'}. "
        f"Computed from multi-outlet article framing and sentiment/bias patterns for topic '{normalized_topic}'."
    )
    if llm_data.get("from_cache"):
        reasoning += " Reused same-day cached analysis."
    if llm_data.get("error"):
        reasoning = llm_data.get("error_message") or "Missing-angle reasoning unavailable."

    return {
        "success": True,
        "data": {
            "topic": normalized_topic,
            "fetch": fetch_meta,
            "scoring": {
                "article_count": score_result["article_count"],
                "scored_count": score_result["scored_count"],
            },
            "missing_angle": {
                "value": llm_data.get("missing_angle"),
                "reasoning": reasoning,
                "confidence": llm_data.get("confidence"),
                "from_cache": llm_data.get("from_cache", False),
                "error": llm_data.get("error", False),
                "error_message": llm_data.get("error_message"),
            },
            "outlet_count": score_data["outlet_count"],
            "outlets": outlets_with_missing_angle,
            "timeline": timeline,
        },
        "error": None,
    }


app.include_router(router)

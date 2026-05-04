from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import desc, exists, func, select, text
from sqlalchemy.orm import Session

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

# Browser dev servers (Vite/static on 5173). Append comma-separated URLs via CORS_ORIGINS for staging/production.
_DEV_CORS_ORIGINS = [
    "http://127.0.0.1:5173",
    "http://localhost:5173",
]
_extra_cors = [
    o.strip()
    for o in (os.getenv("CORS_ORIGINS") or "").split(",")
    if o.strip()
]
_CORS_ALLOW_ORIGINS = list(dict.fromkeys(_DEV_CORS_ORIGINS + _extra_cors))

from .bias_utils import bias_distribution_from_outlets, bias_label_from_axis, extrem_bias_outlets
from .database import Article, ArticleScore, TopicOutletFraming, create_tables, get_db, normalize_topic
from .llm_analyzer import LLMAnalyzer
from .news_fetcher import (
    NewsFetcherError,
    compute_selected_outlets_from_db,
    detect_source_categories_for_query,
    fetch_and_store_articles,
)

DEFAULT_TOPIC_TREND_DAYS = 7
DEFAULT_OUTLET_SERIES_DAYS = 14
from .nlp_pipeline import NLPPipeline

_nlp_pipeline: NLPPipeline | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _nlp_pipeline
    create_tables()
    _nlp_pipeline = NLPPipeline.get_instance()
    yield


app = FastAPI(title="NewsLens Backend", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ALLOW_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)
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


def _coverage_confidence_status(article_count: int) -> str:
    """Fetch quality signal from stored article count for this topic."""
    if article_count >= 10:
        return "high"
    if article_count >= 5:
        return "developing"
    return "insufficient"


def _load_framing_map(topic: str, db: Session) -> dict[str, str]:
    rows = db.execute(
        select(TopicOutletFraming.source, TopicOutletFraming.framing_summary).where(
            TopicOutletFraming.topic == topic
        )
    ).all()
    return {str(r[0]): str(r[1]) for r in rows}


def _topic_has_unscored_articles(topic: str, db: Session) -> bool:
    """True if any article for this topic has no row in article_scores."""
    row = db.scalar(
        select(Article.id)
        .where(Article.topic == topic)
        .where(~exists(select(1).select_from(ArticleScore).where(ArticleScore.article_id == Article.id)))
        .limit(1)
    )
    return row is not None


def _build_outlet_scores(
    topic: str,
    db: Session,
    ordered_sources: list[str],
    framing_by_source: dict[str, str],
) -> dict:
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
    for source in ordered_sources:
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
                    "framing_summary": framing_by_source.get(source),
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
        stats["dominant_bias_label"] = bias_label_from_axis(float(stats["avg_bias_score"]))
        stats["framing_summary"] = framing_by_source.get(source)
        outlets.append(stats)

    return {
        "topic": topic,
        "article_count": len(latest_by_article),
        "outlet_count": len(outlets),
        "outlets": outlets,
    }


def _build_headline_map(topic: str, db: Session, sources: list[str]) -> dict[str, str | None]:
    rows = db.execute(
        select(Article.source, Article.title)
        .where(Article.topic == topic)
        .order_by(Article.published_at.desc().nullslast(), Article.fetched_at.desc())
    ).all()

    headlines: dict[str, str | None] = {source: None for source in sources}
    for row in rows:
        if row.source not in headlines:
            continue
        if headlines.get(row.source):
            continue
        headlines[row.source] = row.title
    return headlines


def _build_bias_timeline(topic: str, db: Session, outlet_names: list[str], days: int = 7) -> list[dict]:
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
        for source in outlet_names:
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


def _outlet_historical_profile(outlet: str, db: Session) -> dict:
    """Latest score per article for this source (all topics), then averages + daily bias series."""
    rows = db.execute(
        select(
            Article.id,
            Article.snapshot_date,
            ArticleScore.bias_score,
            ArticleScore.sentiment_score,
            ArticleScore.created_at,
        )
        .join(ArticleScore, ArticleScore.article_id == Article.id)
        .where(Article.source == outlet)
        .order_by(Article.id.asc(), desc(ArticleScore.created_at))
    ).all()

    latest_by_article: dict[int, dict] = {}
    for row in rows:
        if row.id in latest_by_article:
            continue
        latest_by_article[row.id] = {
            "snapshot_date": row.snapshot_date,
            "bias_score": row.bias_score,
            "sentiment_score": row.sentiment_score,
        }

    bias_vals: list[float] = []
    sent_vals: list[float] = []
    for item in latest_by_article.values():
        if item["bias_score"] is not None:
            bias_vals.append(float(item["bias_score"]))
        if item["sentiment_score"] is not None:
            sent_vals.append(float(item["sentiment_score"]))

    n = len(latest_by_article)
    avg_bias = round(sum(bias_vals) / len(bias_vals), 4) if bias_vals else None
    avg_sent = round(sum(sent_vals) / len(sent_vals), 4) if sent_vals else None

    end_date = datetime.now(timezone.utc).date()
    start_series = end_date - timedelta(days=DEFAULT_OUTLET_SERIES_DAYS - 1)
    by_day: dict[str, list[float]] = {}
    for item in latest_by_article.values():
        sd = item["snapshot_date"]
        if sd is None or sd < start_series or sd > end_date:
            continue
        if item["bias_score"] is None:
            continue
        key = sd.isoformat()
        by_day.setdefault(key, []).append(float(item["bias_score"]))

    series = []
    day_cursor = start_series
    while day_cursor <= end_date:
        key = day_cursor.isoformat()
        vals = by_day.get(key)
        series.append(
            {
                "date": key,
                "avg_bias": round(sum(vals) / len(vals), 4) if vals else None,
            }
        )
        day_cursor += timedelta(days=1)

    return {
        "outlet": outlet,
        "article_count": n,
        "avg_bias_score": avg_bias,
        "avg_sentiment_score": avg_sent,
        "series": series,
    }


def _topic_volume_trend(topic: str, db: Session, days: int, outlet_names: list[str]) -> list[dict]:
    """Article counts per calendar day (UTC) and source from fetched_at."""
    normalized = normalize_topic(topic)
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=max(1, days) - 1)
    start_dt = datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc)
    end_dt = datetime(end_date.year, end_date.month, end_date.day, 23, 59, 59, 999999, tzinfo=timezone.utc)

    rows = db.execute(
        select(Article.fetched_at, Article.source).where(
            Article.topic == normalized,
            Article.fetched_at >= start_dt,
            Article.fetched_at <= end_dt,
        )
    ).all()

    counts: dict[tuple[str, str], int] = {}
    for fetched_at, source in rows:
        if not fetched_at:
            continue
        day_key = fetched_at.astimezone(timezone.utc).date().isoformat()
        key = (day_key, source)
        counts[key] = counts.get(key, 0) + 1

    day_cursor = start_date
    bucket_keys: list[str] = []
    while day_cursor <= end_date:
        bucket_keys.append(day_cursor.isoformat())
        day_cursor += timedelta(days=1)

    result = []
    for day_key in bucket_keys:
        row = {"date": day_key}
        for src in outlet_names:
            row[src] = counts.get((day_key, src), 0)
        result.append(row)

    return result


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
    normalized_topic = normalize_topic(topic)
    if not normalized_topic:
        raise HTTPException(status_code=400, detail="Topic must not be empty.")

    if _topic_has_unscored_articles(normalized_topic, db):
        score_result = nlp_pipeline.score_topic_articles(normalized_topic, db)
    else:
        n_art = db.scalar(select(func.count(Article.id)).where(Article.topic == normalized_topic)) or 0
        score_result = {
            "topic": normalized_topic,
            "article_count": int(n_art),
            "scored_count": int(n_art),
        }
    if score_result["article_count"] == 0:
        return {"success": True, "data": {"topic": normalized_topic, "outlets": []}, "error": None}

    selected = compute_selected_outlets_from_db(normalized_topic, db)
    framing = _load_framing_map(normalized_topic, db)
    return {
        "success": True,
        "data": _build_outlet_scores(normalized_topic, db, selected, framing),
        "error": None,
    }


@router.get("/analyze", response_model=AnalyzeResponse)
async def analyze_topic(
    topic: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
    nlp_pipeline: NLPPipeline = Depends(get_nlp_pipeline),
) -> AnalyzeResponse:
    normalized_topic = normalize_topic(topic)
    if not normalized_topic:
        raise HTTPException(status_code=400, detail="Topic must not be empty.")

    fetch_meta: dict = {
        "cached": True,
        "count": 0,
        "saved_urls": [],
        "selected_outlets": [],
        "source_pool": detect_source_categories_for_query(normalized_topic),
        "query_used": normalized_topic,
    }
    try:
        # Always run through fetcher so its 24-hour cache policy decides freshness.
        fetch_meta = await fetch_and_store_articles(normalized_topic, db)
    except NewsFetcherError as exc:
        fetch_meta = {
            "cached": True,
            "count": 0,
            "saved_urls": [],
            "selected_outlets": [],
            "warning": str(exc),
            "source_pool": detect_source_categories_for_query(normalized_topic),
            "query_used": normalized_topic,
        }

    selected: list[str] = list(fetch_meta.get("selected_outlets") or [])
    if not selected:
        selected = compute_selected_outlets_from_db(normalized_topic, db)

    if _topic_has_unscored_articles(normalized_topic, db):
        score_result = nlp_pipeline.score_topic_articles(normalized_topic, db)
    else:
        n_art = db.scalar(select(func.count(Article.id)).where(Article.topic == normalized_topic)) or 0
        score_result = {
            "topic": normalized_topic,
            "article_count": int(n_art),
            "scored_count": int(n_art),
        }

    framing_map = _load_framing_map(normalized_topic, db)
    score_data = _build_outlet_scores(normalized_topic, db, selected, framing_map)

    llm_result = LLMAnalyzer().generate_missing_angle(normalized_topic, db, selected)
    llm_data = llm_result.get("data", {})
    headlines = _build_headline_map(normalized_topic, db, selected)
    timeline = _build_bias_timeline(normalized_topic, db, selected, days=7)

    outlet_missing_angles = llm_data.get("outlet_missing_angles", {})
    outlets_with_missing_angle = []
    for outlet in score_data["outlets"]:
        source = outlet["source"]
        merged_outlet = dict(outlet)
        merged_outlet["missing_angle"] = outlet_missing_angles.get(source)
        merged_outlet["headline"] = headlines.get(source)
        outlets_with_missing_angle.append(merged_outlet)

    bias_distribution = bias_distribution_from_outlets(score_data["outlets"])
    most_left_outlet, most_right_outlet = extrem_bias_outlets(score_data["outlets"])

    source_pool = list(fetch_meta.get("source_pool") or detect_source_categories_for_query(normalized_topic))
    coverage_status = _coverage_confidence_status(int(score_result["article_count"]))

    reasoning = (
        f"Confidence: {llm_data.get('confidence') or 'unknown'}. "
        f"Computed from multi-outlet article framing and sentiment/bias patterns for topic '{normalized_topic}'."
    )
    if llm_data.get("from_cache"):
        reasoning += " Reused same-day cached analysis."
    if llm_data.get("analysis_status") == "quota_limited":
        reasoning = (
            "Gemini Pro and Gemini Flash both hit API quota limits for this request. "
            "Missing-angle synthesis is temporarily unavailable."
        )
    elif llm_data.get("error"):
        reasoning = llm_data.get("error_message") or "Missing-angle reasoning unavailable."

    return {
        "success": True,
        "data": {
            "topic": normalized_topic,
            "status": coverage_status,
            "analysis_status": llm_data.get("analysis_status"),
            "source_pool": source_pool,
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
            "bias_distribution": bias_distribution,
            "most_left_outlet": most_left_outlet,
            "most_right_outlet": most_right_outlet,
            "selected_outlets": selected,
            "timeline": timeline,
        },
        "error": None,
    }


@router.get("/outlet-profile", response_model=AnalyzeResponse)
def get_outlet_profile(
    outlet: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
) -> AnalyzeResponse:
    normalized = outlet.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="Outlet name must not be empty.")
    return {
        "success": True,
        "data": _outlet_historical_profile(normalized, db),
        "error": None,
    }


@router.get("/topic-trend", response_model=AnalyzeResponse)
def get_topic_trend(
    topic: str = Query(..., min_length=1),
    days: int = Query(DEFAULT_TOPIC_TREND_DAYS, ge=1, le=90),
    db: Session = Depends(get_db),
) -> AnalyzeResponse:
    normalized = normalize_topic(topic)
    if not normalized:
        raise HTTPException(status_code=400, detail="Topic must not be empty.")
    outlets_for_topic = compute_selected_outlets_from_db(normalized, db)
    return {
        "success": True,
        "data": {
            "topic": normalized,
            "days": days,
            "series": _topic_volume_trend(normalized, db, days, outlets_for_topic),
        },
        "error": None,
    }


app.include_router(router)

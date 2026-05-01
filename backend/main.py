from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, select, text
from sqlalchemy.orm import Session

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

from .database import Article, ArticleScore, create_tables, get_db
from .news_fetcher import ALLOWED_OUTLETS
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


def get_nlp_pipeline() -> NLPPipeline:
    if _nlp_pipeline is None:
        return NLPPipeline.get_instance()
    return _nlp_pipeline


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
        .where(Article.topic == normalized_topic)
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
        "success": True,
        "data": {
            "topic": normalized_topic,
            "article_count": len(latest_by_article),
            "outlet_count": len(outlets),
            "outlets": outlets,
        },
        "error": None,
    }


app.include_router(router)

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from collections import Counter, defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import random
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus, urlparse

import httpx
import requests
from bs4 import BeautifulSoup

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import desc, exists, func, select, text
from sqlalchemy.orm import Session

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

DEBUG = os.getenv("DEBUG", "false").lower() == "true"
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./newslens.db")

# Comma-separated frontend origins (e.g. Vite dev + Vercel). Browsers treat
# localhost and 127.0.0.1 as distinct CORS origins, so both are allowed by default.
_allowed_origins_raw = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173",
)
_CORS_ALLOW_ORIGINS = [o.strip() for o in _allowed_origins_raw.split(",") if o.strip()]

from .bias_utils import (
    bias_distribution_from_outlets,
    bias_label_from_axis,
    bias_spectrum_bucket,
    extrem_bias_outlets,
)
from .credible_domains import CREDIBLE_DOMAINS
from .database import Article, ArticleScore, TopicAnalysis, create_tables, get_db, normalize_topic
from .framing_extract import clean_text, get_framing_summary
from .news_fetcher import (
    MIN_RELEVANCE_SCORE,
    NewsFetcherError,
    compute_selected_outlets_from_db,
    detect_source_categories_for_query,
    fetch_and_store_articles,
)

DEFAULT_TOPIC_TREND_DAYS = 7
DEFAULT_OUTLET_SERIES_DAYS = 14
STRICT_RELEVANCE_CUTOFF = 50

DEFAULT_TRENDING_FALLBACK = (
    "trade war",
    "climate change",
    "artificial intelligence",
    "us-iran conflict",
)
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


_COVERAGE_INSIGHT_EXTRA_STOPWORDS = frozenset(
    {
        "their",
        "there",
        "these",
        "those",
        "which",
        "while",
        "where",
        "being",
        "after",
        "before",
        "through",
        "during",
        "about",
        "under",
        "other",
        "such",
        "some",
        "many",
        "much",
        "more",
        "most",
        "less",
        "also",
        "into",
        "than",
        "then",
        "them",
        "said",
        "says",
        "report",
        "reports",
        "according",
        "coverage",
        "article",
        "articles",
        "story",
        "stories",
        "news",
        "media",
        "sources",
        "source",
        "president",
        "government",
        "official",
        "officials",
        "people",
        "public",
        "country",
        "countries",
        "state",
        "states",
        "national",
        "international",
    }
)


def _outlet_emotional_intensity_score(o: dict) -> float | None:
    """Align with frontend: explicit emotional_intensity or |avg_sentiment| scaled to 0–10."""
    raw = o.get("emotional_intensity")
    if raw is not None:
        try:
            v = float(raw)
            if math.isfinite(v):
                return float(round(min(10.0, max(0.0, v)), 4))
        except (TypeError, ValueError):
            pass
    avg = o.get("avg_sentiment_score")
    if avg is None:
        return None
    try:
        a = float(avg)
        if not math.isfinite(a):
            return None
        return float(round(min(10.0, abs(a) * 10.0), 4))
    except (TypeError, ValueError):
        return None


def _topic_tokens_for_filter(topic: str | None) -> set[str]:
    if not topic or not str(topic).strip():
        return set()
    return {
        m.group(0).lower()
        for m in re.finditer(r"[A-Za-z]{3,}", str(topic))
    }


def generate_coverage_insights(outlets: list[dict], topic: str | None = None) -> list[dict]:
    """
    Derive data-driven insights from /analyze outlet payloads (no extra I/O).
    Returns [] when fewer than two outlets have analyzed articles.
    """
    if not outlets:
        return []

    active = []
    for o in outlets:
        if not isinstance(o, dict):
            continue
        ac = int(o.get("article_count") or 0)
        if ac <= 0:
            continue
        src = o.get("source")
        if src is None or str(src).strip() == "":
            continue
        active.append(o)

    if len(active) < 2:
        return []

    insights: list[dict] = []
    topic_words = _topic_tokens_for_filter(topic)
    stopwords = _TODAYS_TOPICS_STOPWORDS | _COVERAGE_INSIGHT_EXTRA_STOPWORDS

    # 1–2: emotional intensity extremes (same metric as frontend fallback)
    ei_rows: list[tuple[float, str]] = []
    for o in active:
        ei = _outlet_emotional_intensity_score(o)
        if ei is None:
            continue
        ei_rows.append((ei, str(o["source"])))
    if ei_rows:
        max_ei = max(r[0] for r in ei_rows)
        min_ei = min(r[0] for r in ei_rows)
        hi = min((r for r in ei_rows if r[0] == max_ei), key=lambda x: x[1])
        lo = min((r for r in ei_rows if r[0] == min_ei), key=lambda x: x[1])
        insights.append(
            {
                "kind": "most_charged",
                "outlet": hi[1],
                "score": round(hi[0], 1),
                "label": "most emotionally charged coverage",
            }
        )
        insights.append(
            {
                "kind": "most_neutral",
                "outlet": lo[1],
                "score": round(lo[0], 1),
                "label": "most measured coverage",
            }
        )

    # 3: framing / bias gap between ideological extremes
    left_name, right_name = extrem_bias_outlets(outlets)
    scored_bias: list[tuple[float, str]] = []
    for o in active:
        bs = o.get("avg_bias_score")
        if bs is None:
            continue
        try:
            b = float(bs)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(b):
            continue
        scored_bias.append((b, str(o["source"])))
    if scored_bias and left_name and right_name and left_name != right_name:
        lo_o = next((x for x in scored_bias if x[1] == left_name), None)
        hi_o = next((x for x in scored_bias if x[1] == right_name), None)
        if lo_o and hi_o:
            gap = round(abs(float(hi_o[0]) - float(lo_o[0])), 2)
            if gap > 0:
                insights.append(
                    {
                        "kind": "framing_gap",
                        "outlet_a": left_name,
                        "outlet_b": right_name,
                        "gap": gap,
                        "label": "largest perspective divide",
                    }
                )

    # 4: sentiment split (left- vs right-leaning dominant bias labels)
    left_scores: list[float] = []
    right_scores: list[float] = []
    for o in active:
        bucket = bias_spectrum_bucket(o.get("dominant_bias_label"))
        if bucket != "left" and bucket != "right":
            continue
        ss = o.get("avg_sentiment_score")
        if ss is None:
            continue
        try:
            v = float(ss)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(v):
            continue
        if bucket == "left":
            left_scores.append(v)
        else:
            right_scores.append(v)
    if left_scores and right_scores:
        left_avg = sum(left_scores) / len(left_scores)
        right_avg = sum(right_scores) / len(right_scores)
        delta = abs(left_avg - right_avg)
        if delta >= 0.06:
            if right_avg > left_avg:
                split_label = "right-leaning outlets more positive"
            else:
                split_label = "left-leaning outlets more positive"
            insights.append(
                {
                    "kind": "sentiment_split",
                    "left_avg": round(left_avg, 2),
                    "right_avg": round(right_avg, 2),
                    "label": split_label,
                }
            )

    # 5: article volume leader
    counts: list[tuple[int, str]] = []
    for o in active:
        counts.append((int(o.get("article_count") or 0), str(o["source"])))
    if counts:
        top_n = max(c[0] for c in counts)
        leader = min((c for c in counts if c[0] == top_n), key=lambda x: x[1])
        insights.append(
            {
                "kind": "volume_leader",
                "outlet": leader[1],
                "count": leader[0],
                "label": "most articles published on this topic",
            }
        )

    # 6: consensus keyword across all non-empty framing summaries
    framing_texts: list[str] = []
    for o in active:
        fs = o.get("framing_summary")
        if fs is None:
            continue
        s = str(fs).strip()
        if not s:
            continue
        framing_texts.append(s)
    if framing_texts:
        bag: list[str] = []
        per_sets: list[set[str]] = []
        for text in framing_texts:
            words = re.findall(r"[A-Za-z]{5,}", text.lower())
            kept = [
                w for w in words if w not in stopwords and w not in topic_words and len(w) >= 5
            ]
            per_sets.append(set(kept))
            bag.extend(kept)
        if bag and per_sets:
            ctr = Counter(bag)
            common = set.intersection(*per_sets) if len(per_sets) >= 2 else set(bag)
            common = {w for w in common if w not in stopwords and w not in topic_words}
            candidates = common if common else set(ctr.keys())
            best = max(candidates, key=lambda w: (ctr[w], -len(w), w))
            insights.append(
                {
                    "kind": "consensus_keyword",
                    "word": best,
                    "label": "word all outlets focused on",
                }
            )

    return insights


def _framing_by_source_for_outlets(topic: str, db: Session, sources: list[str]) -> dict[str, str]:
    """Inline framing from stored articles (topic + source filtered, relevance >= threshold)."""
    if not sources:
        return {}
    rows = list(
        db.scalars(
            select(Article).where(
                Article.topic == topic,
                Article.source.in_(sources),
                Article.relevance_score >= MIN_RELEVANCE_SCORE,
            )
        ).all()
    )
    by_source: dict[str, list[Article]] = defaultdict(list)
    for a in rows:
        by_source[a.source].append(a)
    return {s: get_framing_summary(by_source.get(s, []), topic, s) for s in sources}


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
            Article.relevance_score,
            ArticleScore.sentiment_score,
            ArticleScore.sentiment_label,
            ArticleScore.bias_score,
            ArticleScore.bias_label,
            ArticleScore.raw_scores,
            ArticleScore.created_at,
        )
        .join(ArticleScore, ArticleScore.article_id == Article.id)
        .where(Article.topic == topic, Article.relevance_score >= MIN_RELEVANCE_SCORE)
        .order_by(Article.id.asc(), desc(ArticleScore.created_at))
    ).all()

    latest_by_article: dict[int, dict] = {}
    for row in rows:
        if row.id in latest_by_article:
            continue
        credibility_score = None
        try:
            raw_scores = row.raw_scores or {}
            c = raw_scores.get("credibility_score")
            credibility_score = float(c) if c is not None else None
        except Exception:
            credibility_score = None
        latest_by_article[row.id] = {
            "source": row.source,
            "relevance_score": float(row.relevance_score or 0.0),
            "sentiment_score": row.sentiment_score,
            "sentiment_label": row.sentiment_label,
            "bias_score": row.bias_score,
            "bias_label": row.bias_label,
            "credibility_score": credibility_score,
        }

    rows_by_source: dict[str, list[dict]] = defaultdict(list)
    for item in latest_by_article.values():
        rows_by_source[item["source"]].append(item)

    def _aggregate_for_source(source: str) -> dict | None:
        source_rows = rows_by_source.get(source, [])
        if not source_rows:
            return None
        preferred = [r for r in source_rows if r["relevance_score"] >= STRICT_RELEVANCE_CUTOFF]
        active_rows = preferred if preferred else source_rows
        out = {
            "source": source,
            "credibility_score": None,
            "article_count": 0,
            "avg_sentiment_score": 0.0,
            "avg_bias_score": 0.0,
            "sentiment_labels": {},
            "bias_labels": {},
        }
        cred_sum = 0.0
        cred_count = 0
        for item in active_rows:
            out["article_count"] += 1
            out["avg_sentiment_score"] += float(item["sentiment_score"] or 0.0)
            out["avg_bias_score"] += float(item["bias_score"] or 0.0)
            if item.get("credibility_score") is not None:
                cred_sum += float(item["credibility_score"])
                cred_count += 1
            sentiment_label = item["sentiment_label"] or "Unknown"
            bias_label = item["bias_label"] or "Unknown"
            out["sentiment_labels"][sentiment_label] = out["sentiment_labels"].get(sentiment_label, 0) + 1
            out["bias_labels"][bias_label] = out["bias_labels"].get(bias_label, 0) + 1
        if cred_count > 0:
            out["credibility_score"] = round(cred_sum / cred_count, 2)
        return out

    outlets = []
    for source in ordered_sources:
        stats = _aggregate_for_source(source)
        if stats is None:
            outlets.append(
                {
                    "source": source,
                    "credibility_score": None,
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
        .where(Article.topic == topic, Article.relevance_score >= MIN_RELEVANCE_SCORE)
        .order_by(desc(Article.relevance_score), Article.published_at.desc().nullslast(), Article.fetched_at.desc())
    ).all()

    headlines: dict[str, str | None] = {source: None for source in sources}
    for row in rows:
        if row.source not in headlines:
            continue
        if headlines.get(row.source):
            continue
        headlines[row.source] = row.title
    return headlines


# NewsAPI / syndicated bodies often append a truncation marker like [+2038 chars] or (+500 chars).
_ARTICLE_TRUNC_MARKER_RE = re.compile(
    r"\[\s*\+?\d+\s*chars?\s*\]|\(\s*\+?\d+\s*chars?\s*\)|\[\s*\d+\s*chars?\s*\]",
    re.IGNORECASE,
)


def _sanitize_outlet_texts_for_api(out: dict) -> None:
    """Apply clean_text to user-visible article fields on /analyze."""
    for key in ("framing_summary", "top_article_preview", "top_article_headline", "headline"):
        v = out.get(key)
        if v is None or not isinstance(v, str):
            continue
        cleaned = clean_text(v)
        out[key] = cleaned if cleaned else None


def _strip_truncation_markers(text: str) -> str:
    """Replace trailing '[+N chars]' / '(+N chars)' style markers with an ellipsis."""
    s = _ARTICLE_TRUNC_MARKER_RE.sub("...", text)
    s = re.sub(r"(?:\.\.\.){2,}", "...", s)
    return s.strip()


def _clean_article_body_preview(text: str | None, max_chars: int = 300) -> str | None:
    """Strip HTML / URLs and collapse whitespace; return first max_chars or None if empty."""
    if not text:
        return None
    cleaned = re.sub(r"<[^>]+>", " ", text)
    cleaned = re.sub(r"https?://\S+|www\.\S+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = _strip_truncation_markers(cleaned)
    if not cleaned:
        return None
    if len(cleaned) <= max_chars:
        return cleaned
    cleaned = cleaned[:max_chars].rstrip()
    cleaned = _strip_truncation_markers(cleaned)
    if not cleaned.endswith("..."):
        cleaned = f"{cleaned.rstrip('.')}..."
    return cleaned if cleaned else None


def _build_top_article_fields_map(topic: str, db: Session, sources: list[str]) -> dict[str, dict[str, str | None]]:
    """Highest relevance_score article per outlet (among scored articles)."""
    rows = db.execute(
        select(
            Article.id,
            Article.source,
            Article.url,
            Article.title,
            Article.content,
            Article.relevance_score,
        )
        .join(ArticleScore, ArticleScore.article_id == Article.id)
        .where(Article.topic == topic, Article.relevance_score >= MIN_RELEVANCE_SCORE)
        .order_by(Article.source.asc(), desc(Article.relevance_score), Article.id.asc())
    ).all()

    wanted = set(sources)
    out: dict[str, dict[str, str | None]] = {}
    seen_article_ids: set[int] = set()
    for row in rows:
        if row.source not in wanted or row.source in out:
            continue
        if row.id in seen_article_ids:
            continue
        seen_article_ids.add(row.id)
        out[row.source] = {
            "top_article_url": row.url or None,
            "top_article_headline": row.title or None,
            "top_article_preview": _clean_article_body_preview(row.content),
        }
    return out


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
        .where(
            Article.topic == topic,
            Article.relevance_score >= MIN_RELEVANCE_SCORE,
            Article.snapshot_date >= start_date,
            Article.snapshot_date <= end_date,
        )
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

    by_source_days: dict[str, set[str]] = {}
    by_source_scores: dict[str, list[float]] = {}
    for (snapshot_date, source), scores in grouped.items():
        if snapshot_date not in buckets:
            continue
        avg_score = round(sum(scores) / len(scores), 4)
        buckets[snapshot_date][source] = avg_score
        buckets[snapshot_date][f"{source}__estimated"] = False
        buckets[snapshot_date][f"{source}__tracking_started"] = snapshot_date
        by_source_days.setdefault(source, set()).add(snapshot_date)
        by_source_scores.setdefault(source, []).append(avg_score)

    for source in outlet_names:
        real_days = sorted(by_source_days.get(source, set()))
        if len(real_days) != 1:
            continue
        real_day = real_days[0]
        outlet_scores = by_source_scores.get(source, [])
        if not outlet_scores:
            continue
        baseline_center = sum(outlet_scores) / len(outlet_scores)
        real_day_date = datetime.strptime(real_day, "%Y-%m-%d").date()
        seeded = random.Random(f"{topic}:{source}:{real_day}")
        for step in range(6, 0, -1):
            synthetic_date = (real_day_date - timedelta(days=step)).isoformat()
            if synthetic_date not in buckets:
                continue
            if buckets[synthetic_date].get(source) is not None:
                continue
            variance = seeded.uniform(-0.05, 0.05)
            estimated_bias = max(-1.0, min(1.0, baseline_center + variance))
            buckets[synthetic_date][source] = round(estimated_bias, 4)
            buckets[synthetic_date][f"{source}__estimated"] = True
            buckets[synthetic_date][f"{source}__tracking_started"] = real_day

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
            Article.relevance_score >= STRICT_RELEVANCE_CUTOFF,
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


def _trending_topics_list(db: Session) -> list[dict]:
    """Top topics by topic_analysis row count; pad with defaults when the DB has few distinct topics."""
    cnt = func.count(TopicAnalysis.id).label("cnt")
    last_at = func.max(TopicAnalysis.created_at).label("last_at")
    rows = db.execute(
        select(TopicAnalysis.topic, cnt, last_at).group_by(TopicAnalysis.topic).order_by(desc(cnt), desc(last_at)).limit(8)
    ).all()

    out: list[dict] = [{"topic": str(r[0]), "count": int(r[1])} for r in rows]
    seen = {normalize_topic(item["topic"]) for item in out}

    n_distinct = db.scalar(select(func.count(func.distinct(TopicAnalysis.topic)))) or 0

    if n_distinct < 4 or len(out) < 8:
        for label in DEFAULT_TRENDING_FALLBACK:
            if len(out) >= 8:
                break
            nt = normalize_topic(label)
            if nt not in seen:
                out.append({"topic": label, "count": 0})
                seen.add(nt)

    return out[:8]


_TODAYS_TOPICS_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "has",
        "have",
        "had",
        "be",
        "been",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "shall",
        "do",
        "does",
        "did",
        "not",
        "no",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "as",
        "its",
        "it",
        "this",
        "that",
        "these",
        "those",
        "he",
        "she",
        "they",
        "we",
        "you",
    }
)

_CREDIBLE_DOMAIN_SET = frozenset(CREDIBLE_DOMAINS)

_todays_topics_cache: tuple[list[str] | None, datetime | None] = (None, None)


def _domain_from_article_url(url: str | None) -> str | None:
    if not url or not isinstance(url, str):
        return None
    try:
        host = urlparse(url.strip()).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host if host else None
    except Exception:
        return None


def _headline_to_topic_label(title: str) -> str | None:
    if not title or not str(title).strip():
        return None
    tokens = re.findall(r"[A-Za-z0-9]+", str(title))
    lowered = [t.lower() for t in tokens]
    meaningful = [t for t in lowered if t not in _TODAYS_TOPICS_STOPWORDS]
    if not meaningful:
        return None
    if len(meaningful) == 1:
        chunk = meaningful[:1]
    elif len(meaningful) == 2:
        chunk = meaningful[:2]
    else:
        chunk = meaningful[:3]
    return " ".join(chunk)


def _fetch_todays_topics_from_newsapi() -> list[str]:
    api_key = os.getenv("NEWSAPI_KEY")
    if not api_key or "your_newsapi_key_here" in api_key:
        return []

    url = "https://newsapi.org/v2/top-headlines"
    params = {"country": "us", "language": "en", "pageSize": 20, "apiKey": api_key}

    try:
        with httpx.Client(timeout=25.0) as client:
            r = client.get(url, params=params)
            r.raise_for_status()
            payload = r.json()
    except Exception:
        return []

    if payload.get("status") != "ok":
        return []

    articles = payload.get("articles") or []
    seen_keys: set[str] = set()
    out: list[str] = []

    for art in articles:
        if len(out) >= 6:
            break
        if not isinstance(art, dict):
            continue
        dom = _domain_from_article_url(art.get("url"))
        if not dom or dom not in _CREDIBLE_DOMAIN_SET:
            continue
        label = _headline_to_topic_label(art.get("title") or "")
        if not label:
            continue
        key = label.lower().strip()
        if key in seen_keys:
            continue
        seen_keys.add(key)
        out.append(label)

    return out


def _get_todays_topics_cached() -> list[str]:
    global _todays_topics_cache
    now = datetime.now(timezone.utc)
    cached_topics, expires_at = _todays_topics_cache
    if cached_topics is not None and expires_at is not None and now < expires_at:
        return list(cached_topics)
    fresh = _fetch_todays_topics_from_newsapi()
    _todays_topics_cache = (list(fresh), now + timedelta(hours=1))
    return list(fresh)


@router.get("/health", response_model=HealthResponse)
def health_check(db: Session = Depends(get_db)) -> HealthResponse:
    db.execute(text("SELECT 1"))
    return {
        "success": True,
        "data": {
            "service": "newslens-backend",
            "status": "ok",
            "debug": DEBUG,
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
    framing = _framing_by_source_for_outlets(normalized_topic, db, selected)
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
        n_art = (
            db.scalar(
                select(func.count(Article.id)).where(
                    Article.topic == normalized_topic,
                    Article.relevance_score >= STRICT_RELEVANCE_CUTOFF,
                )
            )
            or 0
        )
        score_result = {
            "topic": normalized_topic,
            "article_count": int(n_art),
            "scored_count": int(n_art),
        }

    strict_count = (
        db.scalar(
            select(func.count(Article.id)).where(
                Article.topic == normalized_topic,
                Article.relevance_score >= STRICT_RELEVANCE_CUTOFF,
            )
        )
        or 0
    )

    framing_map = _framing_by_source_for_outlets(normalized_topic, db, selected)
    score_data = _build_outlet_scores(normalized_topic, db, selected, framing_map)
    coverage_insights = generate_coverage_insights(score_data["outlets"], normalized_topic)

    headlines = _build_headline_map(normalized_topic, db, selected)
    top_article_fields = _build_top_article_fields_map(normalized_topic, db, selected)
    timeline = _build_bias_timeline(normalized_topic, db, selected, days=7)

    outlets_payload = []
    for outlet in score_data["outlets"]:
        source = outlet["source"]
        merged_outlet = dict(outlet)
        merged_outlet["headline"] = headlines.get(source)
        ta = top_article_fields.get(source)
        if ta:
            merged_outlet["top_article_url"] = ta["top_article_url"]
            merged_outlet["top_article_headline"] = ta["top_article_headline"]
            merged_outlet["top_article_preview"] = ta["top_article_preview"]
        else:
            merged_outlet["top_article_url"] = None
            merged_outlet["top_article_headline"] = None
            merged_outlet["top_article_preview"] = None
        _sanitize_outlet_texts_for_api(merged_outlet)
        outlets_payload.append(merged_outlet)

    bias_distribution = bias_distribution_from_outlets(score_data["outlets"])
    most_left_outlet, most_right_outlet = extrem_bias_outlets(score_data["outlets"])

    source_pool = list(fetch_meta.get("source_pool") or detect_source_categories_for_query(normalized_topic))
    coverage_status = _coverage_confidence_status(int(strict_count))

    return {
        "success": True,
        "data": {
            "topic": normalized_topic,
            "status": coverage_status,
            "source_pool": source_pool,
            "fetch": fetch_meta,
            "scoring": {
                "article_count": int(strict_count),
                "scored_count": score_result["scored_count"],
            },
            "outlet_count": score_data["outlet_count"],
            "outlets": outlets_payload,
            "bias_distribution": bias_distribution,
            "most_left_outlet": most_left_outlet,
            "most_right_outlet": most_right_outlet,
            "selected_outlets": selected,
            "timeline": timeline,
            "coverage_insights": coverage_insights,
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


@router.get("/trending-topics", response_model=AnalyzeResponse)
def get_trending_topics(db: Session = Depends(get_db)) -> AnalyzeResponse:
    return {
        "success": True,
        "data": {"topics": _trending_topics_list(db)},
        "error": None,
    }


@router.get("/todays-topics", response_model=AnalyzeResponse)
def get_todays_topics() -> AnalyzeResponse:
    topics = _get_todays_topics_cached()
    return {"success": True, "data": {"topics": topics}, "error": None}


# --- Outlet suggestion + Media Bias Fact Check lookup ----------------------

MBFC_SEARCH_URL = "https://mediabiasfactcheck.com/?s={query}"
MBFC_HTTP_TIMEOUT = 10.0
MBFC_CACHE_TTL_SECONDS = 24 * 60 * 60
MBFC_USER_AGENT = (
    "Mozilla/5.0 (compatible; NewsLensBot/1.0; +https://newslens.app) "
    "outlet-lookup"
)

# In-memory TTL cache: outlet name (lowercased) -> (expires_at_epoch, payload)
_outlet_lookup_cache: dict[str, tuple[float, dict]] = {}
_outlet_lookup_lock = threading.Lock()

PENDING_OUTLETS_PATH = Path(__file__).resolve().parent / "pending_outlets.json"
_pending_outlets_lock = threading.Lock()

# MBFC outlet pages list ratings as "Bias Rating: X", "Factual Reporting: X",
# "Credibility Rating: X" inline. Each can be followed by either a newline or
# whitespace before the next label, so stop on label boundary or end-of-line.
_MBFC_BIAS_RE = re.compile(
    r"Bias\s*Rating\s*:\s*(.+?)(?=(?:Factual\s*Reporting|Credibility\s*Rating|Country|MBFC|$|\n))",
    re.IGNORECASE | re.DOTALL,
)
_MBFC_FACTUAL_RE = re.compile(
    r"Factual\s*Reporting\s*:\s*(.+?)(?=(?:Bias\s*Rating|Credibility\s*Rating|Country|MBFC|$|\n))",
    re.IGNORECASE | re.DOTALL,
)
_MBFC_CREDIBILITY_RE = re.compile(
    r"Credibility\s*Rating\s*:\s*(.+?)(?=(?:Bias\s*Rating|Factual\s*Reporting|Country|MBFC|$|\n))",
    re.IGNORECASE | re.DOTALL,
)


def _clean_rating_value(raw: str | None) -> str | None:
    if not raw:
        return None
    value = re.sub(r"\s+", " ", raw).strip(" |·-—")
    return value or None


def _find_first_mbfc_result_url(html: str) -> str | None:
    """Pick the first outlet detail link from an MBFC search results page."""
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return None
    selectors = (
        "article h2.entry-title a",
        "article h3.entry-title a",
        ".entry-title a",
        "article h2 a",
        "article h3 a",
    )
    for selector in selectors:
        for anchor in soup.select(selector):
            href = anchor.get("href")
            if not isinstance(href, str) or not href.strip():
                continue
            href = href.strip()
            if href.startswith("/"):
                href = f"https://mediabiasfactcheck.com{href}"
            if href.startswith("https://mediabiasfactcheck.com/"):
                return href
    return None


def _parse_mbfc_detail(html: str) -> dict[str, str | None]:
    """Parse an MBFC outlet page into (bias, factual, credibility)."""
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return {"bias": None, "factual": None, "credibility": None}
    text = soup.get_text("\n", strip=True)
    bias_match = _MBFC_BIAS_RE.search(text)
    factual_match = _MBFC_FACTUAL_RE.search(text)
    credibility_match = _MBFC_CREDIBILITY_RE.search(text)
    return {
        "bias": _clean_rating_value(bias_match.group(1) if bias_match else None),
        "factual": _clean_rating_value(factual_match.group(1) if factual_match else None),
        "credibility": _clean_rating_value(
            credibility_match.group(1) if credibility_match else None
        ),
    }


def _check_outlet_uncached(name: str) -> dict:
    headers = {"User-Agent": MBFC_USER_AGENT, "Accept": "text/html"}
    try:
        search_url = MBFC_SEARCH_URL.format(query=quote_plus(name))
        search_resp = requests.get(search_url, headers=headers, timeout=MBFC_HTTP_TIMEOUT)
        if search_resp.status_code != 200:
            return {"found": False}
        detail_url = _find_first_mbfc_result_url(search_resp.text)
        if not detail_url:
            return {"found": False}
        detail_resp = requests.get(detail_url, headers=headers, timeout=MBFC_HTTP_TIMEOUT)
        if detail_resp.status_code != 200:
            return {"found": False}
        parsed = _parse_mbfc_detail(detail_resp.text)
        if not any(parsed.values()):
            # Detail page reachable but no ratings extractable: report not-found
            # so the UI falls back to manual review instead of showing blanks.
            return {"found": False}
        return {
            "found": True,
            "outlet": name,
            "bias": parsed["bias"],
            "factual": parsed["factual"],
            "credibility": parsed["credibility"],
            "mbfc_url": detail_url,
        }
    except requests.RequestException:
        return {"found": False}
    except Exception:
        return {"found": False}


def _check_outlet_cached(name: str) -> dict:
    key = name.strip().lower()
    now = time.time()
    with _outlet_lookup_lock:
        cached = _outlet_lookup_cache.get(key)
        if cached and cached[0] > now:
            return dict(cached[1])
    result = _check_outlet_uncached(name)
    with _outlet_lookup_lock:
        _outlet_lookup_cache[key] = (now + MBFC_CACHE_TTL_SECONDS, dict(result))
    return result


class SubmitOutletPayload(BaseModel):
    name: str
    domain: str
    reason: str


def _append_pending_outlet(payload: SubmitOutletPayload) -> None:
    record = {
        "name": payload.name.strip(),
        "domain": payload.domain.strip(),
        "reason": payload.reason.strip(),
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    }
    with _pending_outlets_lock:
        existing: list[dict] = []
        if PENDING_OUTLETS_PATH.exists():
            try:
                raw = PENDING_OUTLETS_PATH.read_text(encoding="utf-8")
                parsed = json.loads(raw) if raw.strip() else []
                if isinstance(parsed, list):
                    existing = parsed
            except Exception:
                existing = []
        existing.append(record)
        PENDING_OUTLETS_PATH.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


@router.get("/check-outlet")
def check_outlet(name: str = Query(..., min_length=1, max_length=200)) -> dict:
    cleaned = name.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Outlet name must not be empty.")
    return _check_outlet_cached(cleaned)


@router.post("/submit-outlet")
def submit_outlet(payload: SubmitOutletPayload) -> dict:
    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="Outlet name must not be empty.")
    if not payload.domain.strip():
        raise HTTPException(status_code=400, detail="Domain must not be empty.")
    if not payload.reason.strip():
        raise HTTPException(status_code=400, detail="Reason must not be empty.")
    _append_pending_outlet(payload)
    return {"success": True}


app.include_router(router)

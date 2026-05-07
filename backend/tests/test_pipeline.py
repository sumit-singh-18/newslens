"""End-to-end pipeline checks (fetch → scores → framing → analyze) with NewsAPI/NLP mocked."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend import main
from backend.database import Article, ArticleScore, Base, normalize_topic
from backend.framing_extract import clean_text
from backend.llm_analyzer import LLMAnalyzer
from backend.main import _framing_by_source_for_outlets
from backend.news_fetcher import compute_selected_outlets_from_db, fetch_and_store_articles
from backend.nlp_pipeline import NLPPipeline


def _title_shares_topic_keywords(title: str, topic: str) -> bool:
    """Topic/query words (≥3 chars) that appear as whole tokens in the title."""
    topic_words = {
        m.group(0).lower()
        for m in re.finditer(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?", topic or "")
        if len(m.group(0)) >= 3
    }
    if not topic_words:
        return False
    title_tokens = {m.group(0).lower() for m in re.finditer(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?", title or "")}
    return bool(title_tokens & topic_words)


def _fake_trade_war_newsapi_articles() -> list[dict]:
    """Enough volume and outlets for fetch_and_store_articles to succeed; titles stay on-topic."""
    sources = ["CNN", "Reuters", "BBC News", "Fox News", "Politico"]
    articles: list[dict] = []
    n = 0
    for src in sources:
        for j in range(3):
            articles.append(
                {
                    "source": {"id": None, "name": src},
                    "author": None,
                    "title": f"China US trade war tariffs escalate in round {j} dispute",
                    "description": "Washington and Beijing trade policy and tariff measures. " * 6,
                    "url": f"https://pipeline.test/trade-war/{n}",
                    "urlToImage": None,
                    "publishedAt": "2025-06-01T12:00:00Z",
                    "content": "Trade negotiations and tariff war implications " + "word " * 220,
                }
            )
            n += 1
    return articles


def _patch_fetch_and_analyze_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEWSAPI_KEY", "test-pipeline-newsapi-key-not-real")

    async def _fake_fetch(_client, *, q, headers):
        _ = q
        _ = headers
        return _fake_trade_war_newsapi_articles()

    monkeypatch.setattr("backend.news_fetcher._fetch_everything_for_domains", _fake_fetch)

    def _fake_analyze_batch(self, texts: list[str]) -> list[dict]:
        return [
            {
                "sentiment_label": "Neutral",
                "sentiment_score": 0.55,
                "bias_label": "Center",
                "bias_score": 0.52,
                "raw_scores": {"sentiment": {"Neutral": 0.55}, "bias": {"Center": 0.52}},
            }
            for _ in texts
        ]

    monkeypatch.setattr(NLPPipeline, "analyze_batch", _fake_analyze_batch)


@pytest.fixture
def pipeline_db() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def _run_trade_war_fetch(db: Session, monkeypatch: pytest.MonkeyPatch) -> dict:
    _patch_fetch_and_analyze_batch(monkeypatch)
    return asyncio.run(fetch_and_store_articles("trade war", db))


def test_fetch_trade_war_titles_share_topic_words(pipeline_db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    meta = _run_trade_war_fetch(pipeline_db, monkeypatch)
    assert meta.get("count", 0) > 0
    topic = normalize_topic("trade war")
    articles = list(pipeline_db.scalars(select(Article).where(Article.topic == topic)).all())
    assert articles
    for a in articles:
        assert _title_shares_topic_keywords(a.title, "trade war"), a.title


def test_after_fetch_every_article_has_article_score(
    pipeline_db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run_trade_war_fetch(pipeline_db, monkeypatch)
    topic = normalize_topic("trade war")
    articles = list(pipeline_db.scalars(select(Article).where(Article.topic == topic)).all())
    assert articles
    for a in articles:
        n = pipeline_db.scalar(
            select(func.count(ArticleScore.id)).where(ArticleScore.article_id == a.id)
        )
        assert int(n or 0) == 1


FORBIDDEN_FRAMING_SUBSTRINGS = ("bollywood", "doomsday", "recipe", "sports", "entertainment")


def test_framing_summary_trade_war_avoids_off_topic_words(
    pipeline_db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run_trade_war_fetch(pipeline_db, monkeypatch)
    topic = normalize_topic("trade war")
    selected = compute_selected_outlets_from_db(topic, pipeline_db)
    assert selected
    framing = _framing_by_source_for_outlets(topic, pipeline_db, selected)
    assert framing
    for _src, summary in framing.items():
        low = (summary or "").lower()
        for bad in FORBIDDEN_FRAMING_SUBSTRINGS:
            assert bad not in low, summary


def test_analyze_returns_complete_outlet_fields(pipeline_db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _run_trade_war_fetch(pipeline_db, monkeypatch)

    def _override_get_db():
        try:
            yield pipeline_db
        finally:
            pass

    class _FakeNLP:
        def score_topic_articles(self, topic: str, db: Session):
            return {"topic": topic, "article_count": 0, "scored_count": 0}

    def _fake_generate_missing_angle(_self, topic: str, db: Session, outlet_sources=None):
        outlets = list(outlet_sources or [])
        return {
            "success": True,
            "data": {
                "topic": topic,
                "missing_angle": None,
                "confidence": None,
                "outlet_missing_angles": {o: None for o in outlets},
                "from_cache": False,
                "error": False,
                "error_message": None,
            },
            "error": None,
        }

    monkeypatch.setattr(LLMAnalyzer, "generate_missing_angle", _fake_generate_missing_angle)
    main.app.dependency_overrides[main.get_db] = _override_get_db
    main.app.dependency_overrides[main.get_nlp_pipeline] = lambda: _FakeNLP()
    try:
        client = TestClient(main.app)
        response = client.get("/analyze", params={"topic": "trade war"})
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload.get("success") is True
    data = payload.get("data") or {}
    outlets = data.get("outlets")
    assert isinstance(outlets, list) and len(outlets) > 0

    for o in outlets:
        assert o.get("source")
        assert "framing_summary" in o
        assert o["framing_summary"] is not None
        ac = int(o.get("article_count") or 0)
        if ac > 0:
            assert o.get("avg_sentiment_score") is not None
            assert o.get("avg_bias_score") is not None
            assert o.get("dominant_sentiment_label") is not None
            assert o.get("dominant_bias_label") is not None


def test_clean_text_removes_chars_artifact() -> None:
    assert clean_text("[+2038 chars] real content here") == "real content here"

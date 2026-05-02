from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend import main
from backend.database import Article, ArticleScore, Base
from backend.llm_analyzer import LLMAnalyzer


def _seed_articles_with_scores(db: Session, topic: str) -> None:
    now_utc = datetime.now(timezone.utc)
    sources = ["BBC News", "CNN", "Fox News", "Al Jazeera English"]
    n = 0
    for source in sources:
        for _ in range(2):
            article = Article(
                topic=topic,
                source=source,
                url=f"https://example.com/{n}",
                title=f"{source} title",
                content=f"{source} article content " + "word " * 170,
                fetched_at=now_utc,
                snapshot_date=now_utc.date(),
            )
            db.add(article)
            db.flush()
            db.add(
                ArticleScore(
                    article_id=article.id,
                    sentiment_label="Neutral",
                    sentiment_score=0.75,
                    bias_label="Center",
                    bias_score=0.61,
                    raw_scores={"sentiment": {"Neutral": 0.75}, "bias": {"Center": 0.61}},
                )
            )
            n += 1
    db.commit()


@pytest.fixture
def test_db_session():
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


def test_llm_analyzer_returns_fallback_on_gemini_failure(test_db_session: Session, monkeypatch):
    topic = "climate change"
    _seed_articles_with_scores(test_db_session, topic)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    analyzer = LLMAnalyzer()
    mock_model = MagicMock()
    mock_model.generate_content.side_effect = RuntimeError("boom")
    analyzer._model = mock_model
    result = analyzer.generate_missing_angle(topic, test_db_session)

    assert result["success"] is True
    assert result["error"] is None
    assert result["data"]["missing_angle"] is None
    assert result["data"]["error"] is True
    assert "boom" in result["data"]["error_message"]


def test_analyze_endpoint_stays_up_when_llm_fails(test_db_session: Session, monkeypatch):
    topic = "climate change"
    _seed_articles_with_scores(test_db_session, topic)

    def _override_get_db():
        try:
            yield test_db_session
        finally:
            pass

    class _FakeNLP:
        def score_topic_articles(self, topic: str, db: Session):
            return {"topic": topic, "article_count": 8, "scored_count": 8}

    async def _fake_fetch_and_store_articles(topic: str, db: Session, page_size: int = 25):
        return {
            "cached": True,
            "count": 8,
            "saved_urls": [],
            "selected_outlets": ["Al Jazeera English", "BBC News", "CNN", "Fox News"],
        }

    def _fake_generate_missing_angle(self, topic: str, db: Session, outlet_sources=None):
        return {
            "success": True,
            "data": {
                "topic": topic,
                "missing_angle": None,
                "confidence": None,
                "outlet_missing_angles": {
                    "Al Jazeera English": None,
                    "BBC News": None,
                    "CNN": None,
                    "Fox News": None,
                },
                "from_cache": False,
                "error": True,
                "error_message": "Connection error.",
            },
            "error": None,
        }

    monkeypatch.setattr(main, "fetch_and_store_articles", _fake_fetch_and_store_articles)
    monkeypatch.setattr(LLMAnalyzer, "generate_missing_angle", _fake_generate_missing_angle)
    main.app.dependency_overrides[main.get_db] = _override_get_db
    main.app.dependency_overrides[main.get_nlp_pipeline] = lambda: _FakeNLP()

    client = TestClient(main.app)
    response = client.get("/analyze", params={"topic": topic})
    payload = response.json()

    assert response.status_code == 200
    assert payload["success"] is True
    assert payload["data"]["topic"] == topic
    assert payload["data"]["scoring"]["article_count"] == 8
    assert payload["data"]["scoring"]["scored_count"] == 8
    assert payload["data"]["missing_angle"]["value"] is None
    assert payload["data"]["missing_angle"]["error"] is True
    assert "error_message" in payload["data"]["missing_angle"]
    assert len(payload["data"]["outlets"]) == 4

    main.app.dependency_overrides.clear()

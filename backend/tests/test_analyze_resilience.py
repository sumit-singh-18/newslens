from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend import main
from backend.database import Article, ArticleScore, Base
from backend.main import STRICT_RELEVANCE_CUTOFF


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


def _seed_articles_with_scores(db: Session, topic: str) -> None:
    now_utc = datetime.now(timezone.utc)
    sources = ["BBC News", "CNN", "Fox News", "Reuters"]
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
                relevance_score=STRICT_RELEVANCE_CUTOFF,
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


def test_analyze_endpoint_returns_coverage_insights_without_llm(test_db_session: Session, monkeypatch):
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
            "selected_outlets": ["Reuters", "BBC News", "CNN", "Fox News"],
            "source_pool": ["GENERAL"],
            "query_used": topic,
        }

    monkeypatch.setattr(main, "fetch_and_store_articles", _fake_fetch_and_store_articles)
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
    assert "missing_angle" not in payload["data"]
    assert isinstance(payload["data"].get("coverage_insights"), list)
    assert len(payload["data"]["outlets"]) == 4
    assert payload["data"]["status"] == "developing"
    assert payload["data"]["source_pool"] == ["GENERAL"]

    main.app.dependency_overrides.clear()

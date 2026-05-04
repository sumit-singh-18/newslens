from backend.news_fetcher import (
    MIN_RELEVANCE_SCORE,
    compute_article_relevance_score,
    suggest_broader_terms,
)


def test_relevance_title_only_passes_threshold():
    topic = "15-minute cities"
    row = {
        "title": "Discussion of 15-minute cities and zoning reform",
        "description": "Weather today.",
        "content": "x" * 400,
        "source": "CNN",
    }
    score, ok = compute_article_relevance_score(topic, row)
    assert ok
    assert score >= MIN_RELEVANCE_SCORE


def test_relevance_body_only_fails_without_title_or_description():
    topic = "15-minute cities"
    row = {
        "title": "Breaking news roundup today",
        "description": "Weekly digest.",
        "content": "The mayor discussed 15-minute cities plans for next year. " * 5,
        "source": "Reuters",
    }
    _score, ok = compute_article_relevance_score(topic, row)
    assert ok is False


def test_relevance_description_only_at_threshold():
    topic = "trade war tariffs"
    row = {
        "title": "Morning briefing",
        "description": "Analysis of trade war tariffs and exports.",
        "content": "padding " * 50,
        "source": "BBC News",
    }
    score, ok = compute_article_relevance_score(topic, row)
    assert ok
    assert score >= MIN_RELEVANCE_SCORE


def test_suggest_broader_terms_returns_three():
    s = suggest_broader_terms("floral arrangements boutique")
    assert len(s) <= 3
    assert len(s) >= 1

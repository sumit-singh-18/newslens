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


def test_right_to_repair_rejects_diplomatic_repair_metaphor():
    topic = "right to repair"
    row = {
        "title": "Trump moves to repair relations with UK after diplomatic row",
        "description": "",
        "content": "",
        "source": "BBC News",
    }
    score, ok = compute_article_relevance_score(topic, row)
    assert ok is False
    assert score == 0


def test_right_to_repair_keeps_john_deere_context():
    topic = "right to repair"
    row = {
        "title": "John Deere and farmers settle repair lawsuit over tractor restrictions",
        "description": "",
        "content": "",
        "source": "Reuters",
    }
    score, ok = compute_article_relevance_score(topic, row)
    assert ok is True
    assert score >= MIN_RELEVANCE_SCORE


def test_trade_war_rejects_star_wars_when_topic_is_trade():
    topic = "trade war"
    row = {
        "title": "Star Wars franchise announces new Disney spinoff series",
        "description": "",
        "content": "",
        "source": "CNN",
    }
    score, ok = compute_article_relevance_score(topic, row)
    assert ok is False
    assert score == 0


def test_digital_warfare_rejects_pure_diplomatic_summit_headline():
    topic = "digital warfare"
    row = {
        "title": "Summit focuses on bilateral tensions and alliance-building talks",
        "description": "",
        "content": "",
        "source": "BBC News",
    }
    score, ok = compute_article_relevance_score(topic, row)
    assert ok is False
    assert score == 0


def test_exact_topic_in_title_automatic_high_score():
    topic = "right to repair"
    row = {
        "title": "EU lawmakers debate right to repair rules for electronics",
        "description": "",
        "content": "",
        "source": "Associated Press",
    }
    score, ok = compute_article_relevance_score(topic, row)
    assert ok is True
    assert score >= 60

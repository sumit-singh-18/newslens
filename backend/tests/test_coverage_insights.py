from __future__ import annotations

from backend.main import generate_coverage_insights


def test_generate_coverage_insights_empty_when_single_active_outlet():
    outlets = [
        {
            "source": "Reuters",
            "article_count": 3,
            "avg_bias_score": 0.5,
            "avg_sentiment_score": 0.1,
            "dominant_bias_label": "Center",
            "framing_summary": "markets reacted sharply",
        },
    ]
    assert generate_coverage_insights(outlets, "trade war") == []


def test_generate_coverage_insights_basic_shapes():
    outlets = [
        {
            "source": "Fox News",
            "article_count": 2,
            "avg_bias_score": 0.72,
            "avg_sentiment_score": 0.89,
            "dominant_bias_label": "Right",
            "framing_summary": "sanctions pressure mounts over trade dispute",
        },
        {
            "source": "Reuters",
            "article_count": 5,
            "avg_bias_score": 0.32,
            "avg_sentiment_score": 0.02,
            "dominant_bias_label": "Center",
            "framing_summary": "sanctions remain central to negotiations",
        },
    ]
    insights = generate_coverage_insights(outlets, "trade war")
    kinds = [i["kind"] for i in insights]
    assert "most_charged" in kinds
    assert "most_neutral" in kinds
    assert "volume_leader" in kinds
    assert "consensus_keyword" in kinds

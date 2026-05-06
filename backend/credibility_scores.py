from __future__ import annotations

OUTLET_CREDIBILITY: dict[str, int] = {
    # Score 10 — Wire services.
    "associated-press": 10,
    "reuters": 10,
    # Score 9 — Major broadcasters/public media.
    "bbc-news": 9,
    "npr": 9,
    "pbs": 9,
    "abc-news": 9,
    "cbs-news": 9,
    "nbc-news": 9,
    # Score 8 — Major newspapers.
    "the-guardian-uk": 8,
    "the-washington-post": 8,
    "the-new-york-times": 8,
    "the-wall-street-journal": 8,
    "financial-times": 8,
    # Score 7 — Major cable/digital news.
    "cnn": 7,
    "fox-news": 7,
    "msnbc": 7,
    "bloomberg": 7,
    "politico": 7,
    "the-hill": 7,
    "axios": 7,
    "the-atlantic": 7,
    # Score 6 — Credible but niche/opinion-heavy.
    "newsweek": 6,
    "time": 6,
    "foreign-policy": 6,
    "al-jazeera-english": 6,
    "usa-today": 6,
}

MIN_CREDIBILITY = 6


def get_credibility_score(source_id: str) -> int:
    normalized = str(source_id or "").strip().lower().replace(" ", "-")
    return int(OUTLET_CREDIBILITY.get(normalized, 0))

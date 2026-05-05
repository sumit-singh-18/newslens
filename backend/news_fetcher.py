from __future__ import annotations

import asyncio
import logging
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .database import Article, ArticleScore, TopicOutletFraming, normalize_topic
from .framing_extract import clean_text
from .nlp_pipeline import NLPPipeline

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

logger = logging.getLogger(__name__)

NEWSAPI_EVERYTHING = "https://newsapi.org/v2/everything"
NEWSAPI_MAX_SOURCES_PER_REQUEST = 20
RELAX_ARTICLE_TARGET = 10


def _normalize_fetch_topic_input(raw: str) -> str:
    """
    Normalize user topic before fetch: hyphens/underscores → spaces (NewsAPI treats hyphen as word-boundary),
    collapse whitespace, lowercase via normalize_topic.
    """
    s = (raw or "").replace("-", " ").replace("_", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return normalize_topic(s)


def _newsapi_q_with_expansions(canonical_topic: str) -> str:
    """Map canonical topic to NewsAPI `q` (boolean OR where helpful)."""
    t = re.sub(r"\s+", " ", (canonical_topic or "").strip())
    tl = t.lower()
    if tl in ("us elections", "us election"):
        return "us election OR american election OR presidential race"
    return canonical_topic


# Credibility-ranked vetted pools — NewsAPI publisher IDs only (see https://newsapi.org/sources).
# Note: the everything endpoint accepts at most 20 `sources` per request; multi-chunk fetching is used.
VETTED_SOURCES_BY_CATEGORY: dict[str, tuple[str, ...]] = {
    "GENERAL": (
        "associated-press",
        "reuters",
        "bbc-news",
        "nbc-news",
        "abc-news",
        "cbs-news",
        "npr",
        "the-washington-post",
        "the-wall-street-journal",
        "the-guardian-uk",
        "the-new-york-times",
        "cnn",
        "fox-news",
        "msnbc",
        "usa-today",
        "independent",
        "al-jazeera-english",
        "the-hill",
        "politico",
        "axios",
        "sky-news",
        "newsweek",
    ),
    "TECH": (
        "techcrunch",
        "the-verge",
        "wired",
        "engadget",
        "ars-technica",
        "mashable",
        "the-next-web",
        "gizmodo",
        "digital-trends",
        "zdnet",
    ),
    "FINANCE": (
        "bloomberg",
        "financial-times",
        "forbes",
        "business-insider",
        "the-economist",
        "cnbc",
        "fortune",
        "financial-post",
        "crypto-coins-news",
        "cbc-news",
    ),
    "SCIENCE_HEALTH": (
        "national-geographic",
        "new-scientist",
        "medical-news-today",
        "scientific-american",
        "science-daily",
    ),
}

CATEGORY_ORDER: tuple[str, ...] = tuple(VETTED_SOURCES_BY_CATEGORY.keys())

SOURCE_DISPLAY_NAMES: dict[str, str] = {
    "associated-press": "Associated Press",
    "reuters": "Reuters",
    "bbc-news": "BBC News",
    "nbc-news": "NBC News",
    "abc-news": "ABC News",
    "cbs-news": "CBS News",
    "npr": "NPR",
    "the-washington-post": "Washington Post",
    "the-wall-street-journal": "Wall Street Journal",
    "the-guardian-uk": "The Guardian",
    "the-new-york-times": "New York Times",
    "cnn": "CNN",
    "fox-news": "Fox News",
    "msnbc": "MSNBC",
    "usa-today": "USA Today",
    "independent": "Independent",
    "al-jazeera-english": "Al Jazeera English",
    "the-hill": "The Hill",
    "politico": "Politico",
    "axios": "Axios",
    "sky-news": "Sky News",
    "newsweek": "Newsweek",
    "techcrunch": "TechCrunch",
    "the-verge": "The Verge",
    "wired": "Wired",
    "engadget": "Engadget",
    "ars-technica": "Ars Technica",
    "mashable": "Mashable",
    "the-next-web": "The Next Web",
    "gizmodo": "Gizmodo",
    "digital-trends": "Digital Trends",
    "zdnet": "ZDNet",
    "bloomberg": "Bloomberg",
    "financial-times": "Financial Times",
    "forbes": "Forbes",
    "business-insider": "Business Insider",
    "the-economist": "The Economist",
    "cnbc": "CNBC",
    "fortune": "Fortune",
    "financial-post": "Financial Post",
    "crypto-coins-news": "Crypto Coins News",
    "cbc-news": "CBC News",
    "national-geographic": "National Geographic",
    "new-scientist": "New Scientist",
    "medical-news-today": "Medical News Today",
    "scientific-american": "Scientific American",
    "science-daily": "Science Daily",
}

_TECH_KEYWORDS = frozenset(
    {
        "tech",
        "technology",
        "software",
        "hardware",
        "chip",
        "chips",
        "semiconductor",
        "semiconductors",
        "ai",
        "ml",
        "repair",
        "startup",
        "startups",
        "cyber",
        "cybersecurity",
        "cloud",
        "saas",
        "iphone",
        "android",
        "developer",
        "developers",
        "coding",
        "code",
        "algorithm",
        "gpu",
        "cpu",
        "silicon",
    }
)
_FINANCE_KEYWORDS = frozenset(
    {
        "market",
        "markets",
        "stock",
        "stocks",
        "cbdc",
        "fed",
        "finance",
        "financial",
        "bank",
        "banking",
        "inflation",
        "bond",
        "bonds",
        "earnings",
        "ipo",
        "trading",
        "investor",
        "investors",
        "bitcoin",
        "ethereum",
        "crypto",
        "sec",
        "treasury",
    }
)
_SCIENCE_HEALTH_KEYWORDS = frozenset(
    {
        "health",
        "medical",
        "medicine",
        "vaccine",
        "vaccines",
        "clinical",
        "trial",
        "fda",
        "disease",
        "cancer",
        "study",
        "studies",
        "science",
        "research",
        "genome",
        "biology",
        "physics",
        "climate",
        "epidemic",
        "pandemic",
    }
)

_FILLER_TOKENS = frozenset(
    {
        "latest",
        "breaking",
        "news",
        "report",
        "today",
        "update",
        "new",
        "major",
        "big",
        "full",
        "top",
        "best",
        "why",
        "how",
        "what",
        "when",
        "where",
        "after",
        "before",
        "during",
        "this",
        "that",
        "with",
        "from",
        "into",
        "over",
        "more",
        "some",
        "in",
        "on",
        "at",
        "to",
        "of",
        "for",
        "and",
        "the",
        "a",
        "an",
    }
)


def _merge_category_source_order() -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for cat in CATEGORY_ORDER:
        for sid in VETTED_SOURCES_BY_CATEGORY[cat]:
            if sid not in seen:
                seen.add(sid)
                ordered.append(sid)
    return tuple(ordered)


ALL_VETTED_SOURCE_IDS_RANKED: tuple[str, ...] = _merge_category_source_order()
ALLOWED_SOURCE_IDS: frozenset[str] = frozenset(ALL_VETTED_SOURCE_IDS_RANKED)
ALLOWED_OUTLET_DISPLAY_NAMES: frozenset[str] = frozenset(
    SOURCE_DISPLAY_NAMES[sid] for sid in ALL_VETTED_SOURCE_IDS_RANKED
)
OUTLET_DISPLAY_RANK: dict[str, int] = {
    SOURCE_DISPLAY_NAMES[sid]: i for i, sid in enumerate(ALL_VETTED_SOURCE_IDS_RANKED)
}

TOP_OUTLET_SLOTS = 5
MIN_ARTICLES_PER_SOURCE = 1
MIN_RELEVANCE_SCORE = 20
MIN_RELEVANT_OUTLETS_FOR_FETCH = 2
RELEVANCE_BODY_PREVIEW_CHARS = 300
NEWSAPI_PAGE_SIZE = 100


class NewsFetcherError(Exception):
    pass


def detect_source_categories_for_query(query: str) -> list[str]:
    """Always includes GENERAL; adds TECH, FINANCE, and/or SCIENCE_HEALTH when keywords match."""
    q = query.strip().lower()
    if not q:
        return ["GENERAL"]
    tokens = set(re.findall(r"[a-z0-9]+", q))
    categories: list[str] = ["GENERAL"]
    if tokens & _TECH_KEYWORDS:
        categories.append("TECH")
    if tokens & _FINANCE_KEYWORDS:
        categories.append("FINANCE")
    if tokens & _SCIENCE_HEALTH_KEYWORDS:
        categories.append("SCIENCE_HEALTH")
    return categories


def source_ids_for_categories(categories: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for cat in CATEGORY_ORDER:
        if cat not in categories:
            continue
        for sid in VETTED_SOURCES_BY_CATEGORY[cat]:
            if sid not in seen:
                seen.add(sid)
                merged.append(sid)
    return merged


def relax_search_query(topic: str) -> str:
    """Broaden keywords by dropping years and low-information tokens."""
    raw = topic.strip()
    if not raw:
        return raw
    without_years = re.sub(r"\b(19|20)\d{2}\b", " ", raw)
    words = re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?", without_years)
    kept = [w for w in words if w.lower() not in _FILLER_TOKENS and len(w) > 1]
    out = " ".join(kept).strip()
    return out or re.sub(r"\s+", " ", without_years).strip() or raw


_TITLE_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?")

# Substrings in the title; if present, article is dropped unless a topic keyword appears
# in title, description, or the first 200 characters of content.
_NEWSLETTER_TITLE_MARKERS: tuple[str, ...] = (
    "newsletter",
    "roundup",
    "digest",
    "briefing",
    "this week",
    "top stories",
)

_CONTENT_HEAD_CHARS = 200
_MAX_ARTICLE_BODY_CHARS = 15000
_MIN_ARTICLES_BEFORE_RELAX_FILTER = 3


def _topic_tokens_min_len4(topic: str) -> set[str]:
    return {m.group(0).lower() for m in _TITLE_WORD_RE.finditer(topic or "") if len(m.group(0)) >= 4}


def _title_tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in _TITLE_WORD_RE.finditer(text or "")}


def _topic_overlap_anywhere(row: dict[str, Any], user_topic: str) -> bool:
    """True if a topic word (len >= 4) appears in title, description, or first 200 chars of content."""
    tokens = _topic_tokens_min_len4(user_topic)
    if not tokens:
        return True
    title = row.get("title") or ""
    desc = row.get("description") or ""
    content = row.get("content") or ""
    head = content[:_CONTENT_HEAD_CHARS]
    combined = f"{title} {desc} {head}"
    return bool(_title_tokens(combined) & tokens)


def _fails_newsletter_title_only(row: dict[str, Any], user_topic: str) -> bool:
    """Reject when a newsletter marker appears in the title and no topic keyword is present anywhere."""
    tl = (row.get("title") or "").lower()
    for marker in _NEWSLETTER_TITLE_MARKERS:
        if marker in tl:
            return not _topic_overlap_anywhere(row, user_topic)
    return False


def _article_passes_full_quality(row: dict[str, Any], user_topic: str) -> bool:
    """Length cap and newsletter rule. Topic fit is enforced via relevance scoring before persistence."""
    content = row.get("content") or ""
    if len(content) > _MAX_ARTICLE_BODY_CHARS:
        return False
    if _fails_newsletter_title_only(row, user_topic):
        return False
    return True


def topic_keywords_for_relevance(topic: str) -> set[str]:
    """Prefer >=4-char tokens; fall back to >=3-char words for short queries."""
    base = _topic_tokens_min_len4(topic)
    if base:
        return base
    return {m.group(0).lower() for m in _TITLE_WORD_RE.finditer(topic or "") if len(m.group(0)) >= 3}


# Words under 4 chars are dropped unless they are not common stopwords (so "war" in "trade war" is kept).
_STOPWORDS_LEN3: frozenset[str] = frozenset(
    (
        "the",
        "and",
        "for",
        "are",
        "but",
        "not",
        "you",
        "all",
        "can",
        "her",
        "was",
        "one",
        "our",
        "out",
        "day",
        "get",
        "has",
        "him",
        "his",
        "how",
        "its",
        "may",
        "new",
        "now",
        "old",
        "see",
        "two",
        "way",
        "who",
        "any",
        "did",
        "let",
        "say",
        "too",
    )
)

# Extra phrases for known topics where pairwise keywords collide with unrelated headlines.
_TOPIC_PHRASE_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "right to repair": (
        "right repair",
        "right-to-repair",
        "repair law",
        "repair movement",
        "ifixit",
        "john deere",
        "apple repair",
        "farm equipment",
        "repair restrictions",
        "consumer repair",
    ),
    "trade war": (
        "trade war",
        "trade-war",
        "tariffs",
        "china trade",
        "import duties",
    ),
    "trade war tariffs": (
        "trade war",
        "trade-war",
        "tariffs",
        "import duties",
        "china trade",
    ),
    "digital warfare": (
        "digital warfare",
        "cyber warfare",
        "cyberwar",
        "cyber operations",
        "malware",
        "ransomware",
        "hacking campaign",
    ),
}

_REPAIR_DIPLOMACY_COLLISION_HINTS: frozenset[str] = frozenset(
    ("relations", "diplomatic", "diplomacy", "bilateral", "ties", "alliance", "foreign")
)

_WAR_FIGURATIVE_TITLE_HINTS: frozenset[str] = frozenset(
    ("star wars", "price war", "bidding war", "turf war", "talent war")
)

_TOPIC_IS_FIGURATIVE_WAR_QUERY_MARKERS: frozenset[str] = frozenset(
    ("star wars", "price war", "bidding war", "turf war", "talent war")
)

_DIGITAL_WARFARE_DIPLOMACY_HINTS: frozenset[str] = frozenset(
    ("summit", "bilateral", "embassy", "minister", "talks", "negotiations", "treaty", "alliance")
)

_DIGITAL_WARFARE_TECH_HINTS: frozenset[str] = frozenset(
    ("cyber", "hacker", "hackers", "malware", "ransomware", "ddos", "software", "chip", "online", "internet")
)


def _normalize_topic_query(topic: str) -> str:
    return re.sub(r"\s+", " ", (topic or "").strip().lower())


def meaningful_topic_words(topic: str) -> list[str]:
    """
    Meaningful topic tokens: length >= 4, or length 3 when not a common stopword (keeps e.g. 'war').
    Shorter tokens (e.g. 'to', 'a') are skipped.
    """
    seen: set[str] = set()
    out: list[str] = []
    for m in _TITLE_WORD_RE.finditer(topic or ""):
        w = m.group(0).lower()
        if len(w) < 3:
            continue
        if len(w) < 4 and w in _STOPWORDS_LEN3:
            continue
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out


def _topic_expansion_phrases(topic: str) -> tuple[str, ...]:
    return _TOPIC_PHRASE_EXPANSIONS.get(_normalize_topic_query(topic), ())


def _generated_adjacent_phrases(meaningful: list[str]) -> list[str]:
    phrases: list[str] = []
    for i in range(len(meaningful) - 1):
        a, b = meaningful[i], meaningful[i + 1]
        phrases.append(f"{a} {b}")
        phrases.append(f"{a}-{b}")
    return phrases


def _title_word_indices(title: str) -> list[str]:
    return [m.group(0).lower() for m in _TITLE_WORD_RE.finditer(title or "")]


def _two_meaningful_words_within_window(title: str, meaningful: list[str], window: int = 5) -> bool:
    """True if two distinct topic words from `meaningful` appear in the title within `window` word positions."""
    words = _title_word_indices(title)
    positions: dict[str, list[int]] = {}
    meaningful_set = set(meaningful)
    for i, w in enumerate(words):
        if w in meaningful_set:
            positions.setdefault(w, []).append(i)
    present = [w for w in meaningful if w in positions]
    if len(present) < 2:
        return False
    for i, wa in enumerate(present):
        for wb in present[i + 1 :]:
            if wa == wb:
                continue
            for ia in positions[wa]:
                for ib in positions[wb]:
                    if abs(ia - ib) <= window:
                        return True
    return False


def _phrase_hit_in_text(text: str, phrases: tuple[str, ...] | list[str]) -> bool:
    tl = (text or "").lower()
    return any(p.lower() in tl for p in phrases if p)


def _strong_topic_phrase_in_title(topic: str, title: str, meaningful: list[str]) -> bool:
    """Proximity, hyphen/adjacent phrase substring, or curated expansions in the title."""
    if _two_meaningful_words_within_window(title, meaningful):
        return True
    for p in _generated_adjacent_phrases(meaningful):
        if p.lower() in (title or "").lower():
            return True
    return _phrase_hit_in_text(title, _topic_expansion_phrases(topic))


def _multiword_field_points(text: str, meaningful: list[str], full_pts: int, one_pts: int) -> int:
    n = len(_title_tokens(text) & set(meaningful))
    if n >= 2:
        return full_pts
    if n == 1:
        return one_pts
    return 0


def _relevance_context_reject(topic: str, title: str) -> bool:
    """
    Drop headlines whose dominant context collides with the topic keyword but not the user's intent.
    """
    tl = _normalize_topic_query(topic)
    tit = (title or "").lower()

    if "repair" in tl and any(h in tit for h in _REPAIR_DIPLOMACY_COLLISION_HINTS):
        return True

    if "war" in tl and not any(m in tl for m in _TOPIC_IS_FIGURATIVE_WAR_QUERY_MARKERS):
        if any(h in tit for h in _WAR_FIGURATIVE_TITLE_HINTS):
            return True

    if "digital" in tl and "warfare" in tl:
        has_diplo = any(h in tit for h in _DIGITAL_WARFARE_DIPLOMACY_HINTS)
        has_tech = any(h in tit for h in _DIGITAL_WARFARE_TECH_HINTS)
        if has_diplo and not has_tech:
            return True

    return False


def _title_or_description_covers_multiword_topic(
    topic: str, title: str, desc: str, meaningful: list[str]
) -> bool:
    """Require >=2 meaningful words in title ∪ description, exact topic substring, or a known expansion phrase."""
    combined = _title_tokens(title) | _title_tokens(desc)
    if len(combined & set(meaningful)) >= 2:
        return True
    nt = _normalize_topic_query(topic)
    if nt and nt in (title or "").lower():
        return True
    if nt and nt in (desc or "").lower():
        return True
    extra = list(_topic_expansion_phrases(topic)) + _generated_adjacent_phrases(meaningful)
    if _phrase_hit_in_text(title, extra) or _phrase_hit_in_text(desc, extra):
        return True
    return False


def compute_article_relevance_score(topic: str, row: dict[str, Any]) -> tuple[int, bool]:
    """
    Phrase-aware relevance for multi-word topics (two or more meaningful tokens; 3-letter content
    words like “war” are kept, common 3-letter fillers are dropped):
    - Title: +60 exact topic string in title; else +40 proximity / phrase / expansion; else +20 if >=2
      topic words in title; else +10 if 1 word.
    - Description / body head: scaled points when multiple topic words appear together.
    - Minimum score to keep: MIN_RELEVANCE_SCORE (requires >=2 topic words in title ∪ description for
      multi-word topics). Title-only / description-only gates match legacy tests.
    Context rejection removes diplomacy / figurative collisions (e.g. “repair relations”, “Star Wars”).
    """
    title = row.get("title") or ""
    desc = row.get("description") or ""
    content = row.get("content") or ""
    head = content[:RELEVANCE_BODY_PREVIEW_CHARS]

    if _relevance_context_reject(topic, title):
        return 0, False

    meaningful = meaningful_topic_words(topic)
    nt = _normalize_topic_query(topic)

    # Single-token or very short topics: preserve keyword-sum scoring with MIN_RELEVANCE_SCORE gate.
    if len(meaningful) < 2:
        tokens = topic_keywords_for_relevance(topic)
        if not tokens:
            return min(100, 40 + 30 + 20 + 10), True

        t_hit = bool(_title_tokens(title) & tokens)
        d_hit = bool(_title_tokens(desc) & tokens)
        h_hit = bool(_title_tokens(head) & tokens)

        score = (40 if t_hit else 0) + (30 if d_hit else 0) + (20 if h_hit else 0) + 10
        score = min(100, score)
        passes = score >= MIN_RELEVANCE_SCORE and (t_hit or d_hit)
        return score, passes

    # Multi-word topic
    if not _title_or_description_covers_multiword_topic(topic, title, desc, meaningful):
        return 0, False

    exact_title = bool(nt and nt in title.lower())
    if exact_title:
        title_pts = 60
    elif _strong_topic_phrase_in_title(topic, title, meaningful):
        title_pts = 40
    else:
        overlap = _title_tokens(title) & set(meaningful)
        n_overlap = len(overlap)
        if n_overlap >= 2:
            title_pts = 20
        elif n_overlap == 1:
            title_pts = 10
        else:
            title_pts = 0

    desc_pts = _multiword_field_points(desc, meaningful, 30, 15)
    head_pts = _multiword_field_points(head, meaningful, 20, 10)
    score = min(100, title_pts + desc_pts + head_pts + 10)
    passes = score >= MIN_RELEVANCE_SCORE
    return score, passes


def _apply_relevance_scoring(by_source: dict[str, list[dict[str, Any]]], user_topic: str) -> int:
    """Remove low-relevance rows; attach relevance_score to survivors. Returns number removed."""
    removed = 0
    for src in list(by_source.keys()):
        kept: list[dict[str, Any]] = []
        for r in by_source[src]:
            score, ok = compute_article_relevance_score(user_topic, r)
            r["relevance_score"] = score
            if ok:
                kept.append(r)
            else:
                removed += 1
        if kept:
            by_source[src] = kept
        else:
            del by_source[src]
    return removed


def suggest_broader_terms(topic: str) -> list[str]:
    """Return up to 3 broader search ideas when credible outlet coverage is thin."""
    raw = (topic or "").strip()
    if not raw:
        return ["world news", "politics", "economy"]
    relaxed = relax_search_query(raw) or raw
    words = [w for w in re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?", relaxed) if len(w) > 2]
    out: list[str] = []
    if words:
        out.append(words[0].lower())
    if len(words) >= 2:
        out.append(f"{words[0]} {words[1]}".lower())
    pool = ["world news", "politics", "economy", "business", "technology", "global trade"]
    low = raw.lower()
    for p in pool:
        if p.lower() != low:
            out.append(p)
        if len(out) >= 5:
            break
    seen: set[str] = set()
    uniq: list[str] = []
    for s in out:
        s = s.strip()
        if not s or s.lower() in seen:
            continue
        seen.add(s.lower())
        uniq.append(s)
        if len(uniq) >= 3:
            break
    return uniq[:3] if uniq else ["world news", "politics", "economy"]


def limited_coverage_fetch_meta(topic: str, *, source_pool: list[str], query_used: str) -> dict[str, Any]:
    t = normalize_topic(topic)
    return {
        "cached": False,
        "count": 0,
        "saved_urls": [],
        "selected_outlets": [],
        "coverage_message": f"Limited credible coverage found for '{t}'. Try a broader search term.",
        "coverage_suggestions": suggest_broader_terms(t),
        "source_pool": source_pool,
        "query_used": query_used,
    }


def _article_passes_newsletter_only(row: dict[str, Any], user_topic: str) -> bool:
    """Relaxed pass: only the newsletter title rule (with topic-keyword exception)."""
    return not _fails_newsletter_title_only(row, user_topic)


def _count_articles_in_by_source(by_source: dict[str, list[dict[str, Any]]]) -> int:
    return sum(len(v) for v in by_source.values())


def _apply_row_filter(
    by_source: dict[str, list[dict[str, Any]]],
    user_topic: str,
    *,
    full: bool,
) -> int:
    if full:

        def keep(r: dict[str, Any]) -> bool:
            return _article_passes_full_quality(r, user_topic)

    else:

        def keep(r: dict[str, Any]) -> bool:
            return _article_passes_newsletter_only(r, user_topic)

    removed = 0
    for src in list(by_source.keys()):
        kept = [r for r in by_source[src] if keep(r)]
        removed += len(by_source[src]) - len(kept)
        if kept:
            by_source[src] = kept
        else:
            del by_source[src]
    return removed


def _filter_fetched_articles_for_topic(by_source: dict[str, list[dict[str, Any]]], user_topic: str) -> int:
    """Remove low-value rows in-place. If full filtering leaves fewer than 3 articles, relax to newsletter-only."""
    snapshot = {k: [dict(r) for r in v] for k, v in by_source.items()}
    removed = _apply_row_filter(by_source, user_topic, full=True)
    if _count_articles_in_by_source(by_source) < _MIN_ARTICLES_BEFORE_RELAX_FILTER:
        logger.info(
            "[NewsLens] Relaxed content filter due to low article count (topic=%r)",
            user_topic,
        )
        by_source.clear()
        for k, rows in snapshot.items():
            by_source[k] = [dict(r) for r in rows]
        removed = _apply_row_filter(by_source, user_topic, full=False)
    return removed


def _parse_newsapi_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _log_source_article_counts(stage: str, by_source: dict[str, list[Any]]) -> None:
    counts = {src: len(items) for src, items in sorted(by_source.items(), key=lambda x: x[0].lower())}
    logger.info(
        "[NewsLens] %s — %d outlets, articles per source: %s",
        stage,
        len(counts),
        counts,
    )


def _qualifying_source_names(by_source: dict[str, list[Any]], min_art: int) -> list[str]:
    eligible = [s for s, items in by_source.items() if len(items) >= min_art]
    eligible.sort(key=lambda s: (-len(by_source[s]), OUTLET_DISPLAY_RANK.get(s, 999)))
    return eligible


def _ingest_articles_into_buckets(
    incoming_articles: list[dict[str, Any]],
    by_source: dict[str, list[dict[str, Any]]],
    seen_urls: set[str],
    *,
    allowed_source_ids: frozenset[str],
    source_id_to_display: dict[str, str],
) -> int:
    added = 0
    for item in incoming_articles:
        src_obj = item.get("source") or {}
        source_id = (src_obj.get("id") or "").strip()
        if not source_id or source_id not in allowed_source_ids:
            continue
        source_name = source_id_to_display.get(source_id) or (src_obj.get("name") or "").strip()
        if not source_name:
            continue
        url = (item.get("url") or "").strip()
        title = clean_text(item.get("title"))
        description = clean_text(item.get("description"))
        content = clean_text(item.get("content")) or description

        if not url or url in seen_urls:
            continue
        if not title or not content:
            continue

        seen_urls.add(url)
        by_source[source_name].append(
            {
                "source": source_name,
                "url": url,
                "title": title,
                "description": description,
                "content": content,
                "published_at_raw": item.get("publishedAt"),
            }
        )
        added += 1
    return added


def _unique_fetch_attempts(
    attempts: list[tuple[list[str], str, list[str]]],
) -> list[tuple[list[str], str, list[str]]]:
    seen: set[tuple[tuple[str, ...], str]] = set()
    out: list[tuple[list[str], str, list[str]]] = []
    for pool_ids, q, cats in attempts:
        key = (tuple(pool_ids), q)
        if key in seen:
            continue
        seen.add(key)
        out.append((pool_ids, q, cats))
    return out


async def _fetch_everything_for_source_ids(
    client: httpx.AsyncClient,
    *,
    q: str,
    source_ids: list[str],
    headers: dict[str, str],
) -> list[dict[str, Any]]:
    if not source_ids or not q:
        return []
    base_params: dict[str, Any] = {
        "q": q,
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": NEWSAPI_PAGE_SIZE,
    }
    chunks = [
        source_ids[i : i + NEWSAPI_MAX_SOURCES_PER_REQUEST]
        for i in range(0, len(source_ids), NEWSAPI_MAX_SOURCES_PER_REQUEST)
    ]

    async def one_chunk(chunk: list[str]) -> list[dict[str, Any]]:
        params = {**base_params, "sources": ",".join(chunk)}
        r = await client.get(NEWSAPI_EVERYTHING, params=params, headers=headers)
        r.raise_for_status()
        payload = r.json()
        if payload.get("status") != "ok":
            raise NewsFetcherError(f"NewsAPI returned error: {payload.get('message', 'unknown error')}")
        return list(payload.get("articles") or [])

    batches = await asyncio.gather(*[one_chunk(c) for c in chunks])
    merged: list[dict[str, Any]] = []
    for batch in batches:
        merged.extend(batch)
    return merged


def _get_recent_cached_articles(topic: str, db: Session) -> list[Article]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    statement = (
        select(Article)
        .where(Article.topic == topic, Article.fetched_at >= cutoff)
        .order_by(Article.fetched_at.desc())
    )
    return list(db.scalars(statement).all())


def compute_selected_outlets_from_db(topic: str, db: Session) -> list[str]:
    """Top approved sources by relevant article count for this topic, max TOP_OUTLET_SLOTS."""
    rows = db.execute(
        select(Article.source, func.count(Article.id))
        .where(Article.topic == topic, Article.relevance_score >= MIN_RELEVANCE_SCORE)
        .group_by(Article.source)
    ).all()
    eligible = [
        (s, int(c))
        for s, c in rows
        if c >= MIN_ARTICLES_PER_SOURCE and s in ALLOWED_OUTLET_DISPLAY_NAMES
    ]
    eligible.sort(key=lambda x: (-x[1], OUTLET_DISPLAY_RANK.get(x[0], 999)))
    return [s for s, _ in eligible[:TOP_OUTLET_SLOTS]]


def _invalidate_topic_articles(topic: str, db: Session) -> None:
    db.execute(delete(TopicOutletFraming).where(TopicOutletFraming.topic == topic))
    db.execute(delete(Article).where(Article.topic == topic))
    db.commit()


async def fetch_and_store_articles(topic: str, db: Session) -> dict[str, Any]:
    topic = _normalize_fetch_topic_input(topic)
    if not topic:
        raise NewsFetcherError("Topic must not be empty.")

    source_categories = detect_source_categories_for_query(topic)
    narrow_pool_ids = source_ids_for_categories(source_categories)
    full_pool_ids = source_ids_for_categories(list(VETTED_SOURCES_BY_CATEGORY.keys()))
    relaxed_q = relax_search_query(topic)
    q_for_full = relaxed_q if relaxed_q else topic

    q_primary = _newsapi_q_with_expansions(topic)
    q_relaxed = _newsapi_q_with_expansions(relaxed_q) if relaxed_q else q_primary
    q_full = _newsapi_q_with_expansions(q_for_full)

    attempt_specs: list[tuple[list[str], str, list[str]]] = [
        (narrow_pool_ids, q_primary, source_categories),
    ]
    if relaxed_q != topic:
        attempt_specs.append((narrow_pool_ids, q_relaxed, source_categories))
    attempt_specs.append((full_pool_ids, q_full, list(CATEGORY_ORDER)))
    fetch_attempts = _unique_fetch_attempts(attempt_specs)

    cached_articles = _get_recent_cached_articles(topic, db)
    if cached_articles:
        selected = compute_selected_outlets_from_db(topic, db)
        if selected:
            return {
                "cached": True,
                "count": len(cached_articles),
                "saved_urls": [],
                "selected_outlets": selected,
                "source_pool": source_categories,
                "query_used": topic,
            }
        _invalidate_topic_articles(topic, db)

    news_api_key = os.getenv("NEWSAPI_KEY")
    if not news_api_key or "your_newsapi_key_here" in news_api_key:
        raise NewsFetcherError("Missing valid NEWSAPI_KEY in environment.")

    headers = {"X-Api-Key": news_api_key}
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_urls: set[str] = set()

    final_source_pool = source_categories
    final_query_used = topic

    try:
        async with httpx.AsyncClient(timeout=40.0) as client:
            for pool_ids, q, pool_cats in fetch_attempts:
                if not q:
                    continue
                by_source.clear()
                seen_urls.clear()
                pool_set = frozenset(pool_ids)
                articles = await _fetch_everything_for_source_ids(
                    client, q=q, source_ids=pool_ids, headers=headers
                )
                n1 = _ingest_articles_into_buckets(
                    articles,
                    by_source,
                    seen_urls,
                    allowed_source_ids=pool_set,
                    source_id_to_display=SOURCE_DISPLAY_NAMES,
                )
                total_in_pool = sum(len(v) for v in by_source.values())
                _log_source_article_counts(
                    f"everything vetted ingested={n1} total={total_in_pool} q={q!r} pool={pool_cats}",
                    by_source,
                )
                final_source_pool = pool_cats
                final_query_used = q
                if total_in_pool >= RELAX_ARTICLE_TARGET:
                    break

    except httpx.HTTPError as exc:
        raise NewsFetcherError(f"NewsAPI request failed: {exc}") from exc

    _n_dropped = _filter_fetched_articles_for_topic(by_source, topic)
    if _n_dropped:
        logger.info(
            "[NewsLens] dropped %s articles in quality filter before persistence (topic=%r)",
            _n_dropped,
            topic,
        )

    _n_rel = _apply_relevance_scoring(by_source, topic)
    if _n_rel:
        logger.info(
            "[NewsLens] dropped %s articles below relevance threshold (topic=%r)",
            _n_rel,
            topic,
        )

    eligible_sources = _qualifying_source_names(by_source, MIN_ARTICLES_PER_SOURCE)
    if len(eligible_sources) < MIN_RELEVANT_OUTLETS_FOR_FETCH:
        return limited_coverage_fetch_meta(topic, source_pool=final_source_pool, query_used=final_query_used)

    selected_sources = eligible_sources[:TOP_OUTLET_SLOTS]

    flat_raw: list[dict[str, Any]] = []
    for src in selected_sources:
        flat_raw.extend(by_source[src])

    if not flat_raw:
        return limited_coverage_fetch_meta(topic, source_pool=final_source_pool, query_used=final_query_used)

    urls_flat = [r["url"] for r in flat_raw]
    blocked_rows = db.execute(select(Article.url).where(Article.url.in_(urls_flat))).all()
    blocked = {str(r[0]) for r in blocked_rows if r[0]}
    if blocked:
        logger.info(
            "[NewsLens] dropping %d articles whose URLs already exist for another topic",
            len(blocked),
        )
        flat_raw = [r for r in flat_raw if r["url"] not in blocked]

    if not flat_raw:
        return limited_coverage_fetch_meta(topic, source_pool=final_source_pool, query_used=final_query_used)

    flat_by_src: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in flat_raw:
        flat_by_src[r["source"]].append(r)
    reranked = sorted(
        flat_by_src.keys(),
        key=lambda s: (-len(flat_by_src[s]), OUTLET_DISPLAY_RANK.get(s, 999)),
    )
    if len(reranked) < MIN_RELEVANT_OUTLETS_FOR_FETCH:
        return limited_coverage_fetch_meta(topic, source_pool=final_source_pool, query_used=final_query_used)

    selected_sources = reranked[:TOP_OUTLET_SLOTS]
    flat_raw = []
    for src in selected_sources:
        flat_raw.extend(flat_by_src[src])

    texts = [row["content"] for row in flat_raw]
    nlp = NLPPipeline.get_instance()
    analyses = nlp.analyze_batch(texts)
    if len(flat_raw) != len(analyses):
        raise NewsFetcherError("NLP analysis count does not match article count.")

    now_utc = datetime.now(timezone.utc)
    snapshot_date = now_utc.date()
    saved_urls: list[str] = []

    db.execute(delete(TopicOutletFraming).where(TopicOutletFraming.topic == topic))
    db.execute(delete(Article).where(Article.topic == topic))
    db.flush()

    for raw_row, analysis in zip(flat_raw, analyses):
        article = Article(
            topic=topic,
            source=raw_row["source"],
            url=raw_row["url"],
            title=raw_row["title"],
            description=(raw_row.get("description") or "").strip(),
            content=raw_row["content"],
            published_at=_parse_newsapi_datetime(raw_row.get("published_at_raw")),
            fetched_at=now_utc,
            snapshot_date=snapshot_date,
            relevance_score=int(raw_row.get("relevance_score") or 0),
        )
        db.add(article)
        db.flush()
        db.add(
            ArticleScore(
                article_id=article.id,
                sentiment_label=analysis["sentiment_label"],
                sentiment_score=analysis["sentiment_score"],
                bias_label=analysis["bias_label"],
                bias_score=analysis["bias_score"],
                raw_scores=analysis["raw_scores"],
            )
        )
        saved_urls.append(raw_row["url"])

    db.commit()
    return {
        "cached": False,
        "count": len(saved_urls),
        "saved_urls": saved_urls,
        "selected_outlets": selected_sources,
        "source_pool": final_source_pool,
        "query_used": final_query_used,
    }

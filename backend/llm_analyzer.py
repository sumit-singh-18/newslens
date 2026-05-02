from __future__ import annotations

import json
import logging
import os
import traceback
from datetime import datetime, timezone
from typing import Any

from anthropic import Anthropic
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import Article, TopicAnalysis
from .news_fetcher import ALLOWED_OUTLETS

logger = logging.getLogger(__name__)
MIN_OUTLET_SUMMARIES = 3


MISSING_ANGLE_SYSTEM_PROMPT = """You are a media analysis assistant for NewsLens.

Your job is to identify the "missing angle" in multi-outlet coverage for a topic.

Rules:
1. Return ONLY valid JSON. No markdown, no preamble, no explanation.
2. Be concise and specific.
3. Focus on what is underreported, missing context, absent stakeholders, and evidence gaps.
4. If uncertainty is high, reflect that briefly in the JSON fields.
5. Keep each outlet note short and practical.
"""

LLM_MODEL = "claude-sonnet-4-6"


class LLMAnalyzer:
    """Claude-powered missing-angle analysis with cache and fail-safe fallback."""

    def __init__(self) -> None:
        self.api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self.client = Anthropic(api_key=self.api_key) if self.api_key else None

    @staticmethod
    def _summarize_to_150_words(text: str) -> str:
        words = text.split()
        if len(words) <= 150:
            return text.strip()
        return " ".join(words[:150]).strip()

    def _select_outlet_article_summaries(self, topic: str, db: Session) -> list[dict[str, str]]:
        """One summary per allowed outlet: latest article title + body (trimmed to ~150 words)."""
        summaries: list[dict[str, str]] = []
        for source in ALLOWED_OUTLETS:
            row = db.execute(
                select(Article.title, Article.content)
                .where(Article.topic == topic, Article.source == source)
                .order_by(Article.published_at.desc().nullslast(), Article.fetched_at.desc())
                .limit(1)
            ).first()
            if not row:
                continue
            title = (row.title or "").strip()
            body = (row.content or "").strip()
            combined = f"{title}. {body}".strip() if body else title
            if not combined:
                continue
            summary = self._summarize_to_150_words(combined)
            summaries.append({"source": source, "summary": summary})
        return summaries

    @staticmethod
    def _extract_text_content(response: Any) -> str:
        parts: list[str] = []
        for block in getattr(response, "content", []) or []:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        return "\n".join(parts).strip()

    @staticmethod
    def _safe_parse_json(payload: str) -> dict[str, Any]:
        return json.loads(payload)

    @staticmethod
    def _today_utc_date():
        return datetime.now(timezone.utc).date()

    def _load_cached_topic_analysis(self, topic: str, db: Session) -> TopicAnalysis | None:
        today = self._today_utc_date()
        return db.scalar(
            select(TopicAnalysis)
            .where(TopicAnalysis.topic == topic, TopicAnalysis.snapshot_date == today)
            .order_by(TopicAnalysis.created_at.desc())
        )

    def _build_user_prompt(self, topic: str, outlet_summaries: list[dict[str, str]]) -> str:
        schema = {
            "topic": topic,
            "missing_angle": "string",
            "confidence": "low|medium|high",
            "outlet_missing_angles": {source: "string" for source in ALLOWED_OUTLETS},
        }
        summary_lines = [
            f"- {item['source']}: {item['summary']}" for item in outlet_summaries
        ]
        return (
            f"Analyze topic: {topic}\n"
            "Using the outlet summaries below, identify the missing angle.\n\n"
            "Outlet summaries:\n"
            f"{os.linesep.join(summary_lines)}\n\n"
            "Return ONLY valid JSON with this shape:\n"
            f"{json.dumps(schema)}"
        )

    def _normalize_outlet_missing_angles(self, parsed: dict[str, Any]) -> dict[str, Any]:
        outlet_missing = parsed.get("outlet_missing_angles", {})
        if not isinstance(outlet_missing, dict):
            outlet_missing = {}
        return {source: outlet_missing.get(source) for source in ALLOWED_OUTLETS}

    def generate_missing_angle(self, topic: str, db: Session) -> dict[str, Any]:
        normalized_topic = topic.strip()
        if not normalized_topic:
            return {
                "success": False,
                "data": {
                    "topic": "",
                    "missing_angle": None,
                    "confidence": None,
                    "outlet_missing_angles": {source: None for source in ALLOWED_OUTLETS},
                    "from_cache": False,
                    "error": True,
                    "error_message": "Topic must not be empty.",
                },
                "error": "Topic must not be empty.",
            }

        cached = self._load_cached_topic_analysis(normalized_topic, db)
        if cached:
            parsed_summary: dict[str, Any] = {}
            if cached.llm_summary:
                try:
                    parsed_summary = json.loads(cached.llm_summary)
                except json.JSONDecodeError:
                    parsed_summary = {}
            return {
                "success": True,
                "data": {
                    "topic": normalized_topic,
                    "missing_angle": cached.missing_angle,
                    "confidence": parsed_summary.get("confidence"),
                    "outlet_missing_angles": self._normalize_outlet_missing_angles(parsed_summary),
                    "from_cache": True,
                    "error": False,
                    "error_message": None,
                },
                "error": None,
            }

        if not self.api_key or "your_claude_api_key_here" in self.api_key:
            return {
                "success": False,
                "data": {
                    "topic": normalized_topic,
                    "missing_angle": None,
                    "confidence": None,
                    "outlet_missing_angles": {source: None for source in ALLOWED_OUTLETS},
                    "from_cache": False,
                    "error": True,
                    "error_message": "ANTHROPIC_API_KEY is not configured.",
                },
                "error": "ANTHROPIC_API_KEY is not configured.",
            }

        outlet_summaries = self._select_outlet_article_summaries(normalized_topic, db)
        logger.info(
            "Missing angle Claude input: topic=%r outlet_summary_count=%s sources=%s",
            normalized_topic,
            len(outlet_summaries),
            [s["source"] for s in outlet_summaries],
        )
        if not outlet_summaries:
            return {
                "success": True,
                "data": {
                    "topic": normalized_topic,
                    "missing_angle": None,
                    "confidence": None,
                    "outlet_missing_angles": {source: None for source in ALLOWED_OUTLETS},
                    "from_cache": False,
                    "error": False,
                    "error_message": None,
                },
                "error": None,
            }
        if len(outlet_summaries) < MIN_OUTLET_SUMMARIES:
            return {
                "success": True,
                "data": {
                    "topic": normalized_topic,
                    "missing_angle": None,
                    "confidence": None,
                    "outlet_missing_angles": {source: None for source in ALLOWED_OUTLETS},
                    "from_cache": False,
                    "error": True,
                    "error_message": (
                        f"At least {MIN_OUTLET_SUMMARIES} outlet summaries are required for missing-angle analysis."
                    ),
                },
                "error": None,
            }

        try:
            logger.info(
                "Calling Claude for missing angle with %d outlet summaries (topic=%r)",
                len(outlet_summaries),
                normalized_topic,
            )
            response = self.client.messages.create(
                model=LLM_MODEL,
                system=MISSING_ANGLE_SYSTEM_PROMPT,
                max_tokens=1000,
                temperature=0.7,
                messages=[
                    {
                        "role": "user",
                        "content": self._build_user_prompt(normalized_topic, outlet_summaries),
                    }
                ],
            )
            content_text = self._extract_text_content(response)
            parsed = self._safe_parse_json(content_text)
            missing_angle = parsed.get("missing_angle")
            confidence = parsed.get("confidence")
            outlet_missing_angles = self._normalize_outlet_missing_angles(parsed)

            db.add(
                TopicAnalysis(
                    topic=normalized_topic,
                    snapshot_date=self._today_utc_date(),
                    article_count=len(outlet_summaries),
                    missing_angle=missing_angle,
                    llm_summary=json.dumps(
                        {
                            "topic": normalized_topic,
                            "confidence": confidence,
                            "outlet_missing_angles": outlet_missing_angles,
                        }
                    ),
                )
            )
            db.commit()

            return {
                "success": True,
                "data": {
                    "topic": normalized_topic,
                    "missing_angle": missing_angle,
                    "confidence": confidence,
                    "outlet_missing_angles": outlet_missing_angles,
                    "from_cache": False,
                    "error": False,
                    "error_message": None,
                },
                "error": None,
            }
        except Exception as exc:
            traceback.print_exc()
            return {
                "success": True,
                "data": {
                    "topic": normalized_topic,
                    "missing_angle": None,
                    "confidence": None,
                    "outlet_missing_angles": {source: None for source in ALLOWED_OUTLETS},
                    "from_cache": False,
                    "error": True,
                    "error_message": str(exc),
                },
                "error": None,
            }

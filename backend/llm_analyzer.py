from __future__ import annotations

import json
import logging
import os
import traceback
from datetime import datetime, timezone
from typing import Any

import google.generativeai as genai
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import Article, TopicAnalysis
from .news_fetcher import compute_selected_outlets_from_db

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

# Default Flash model (override with GEMINI_MODEL). `gemini-1.5-flash` is often unavailable
# on current API versions — use `genai.list_models()` to pick a supported id.
DEFAULT_GEMINI_MODEL = "gemini-2.0-flash"


class LLMAnalyzer:
    """Gemini-powered missing-angle analysis with cache and fail-safe fallback."""

    def __init__(self) -> None:
        self.api_key = os.getenv("GEMINI_API_KEY", "")
        self._model: Any = None
        if self.api_key and "your_gemini_api_key_here" not in self.api_key:
            genai.configure(api_key=self.api_key)
            model_name = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
            self._model = genai.GenerativeModel(
                model_name,
                system_instruction=MISSING_ANGLE_SYSTEM_PROMPT,
            )

    @staticmethod
    def _summarize_to_150_words(text: str) -> str:
        words = text.split()
        if len(words) <= 150:
            return text.strip()
        return " ".join(words[:150]).strip()

    def _select_outlet_article_summaries(
        self, topic: str, db: Session, outlet_sources: list[str]
    ) -> list[dict[str, str]]:
        """One summary per selected outlet: latest article title + body (trimmed to ~150 words)."""
        summaries: list[dict[str, str]] = []
        for source in outlet_sources:
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
    def _extract_gemini_text(response: Any) -> str:
        try:
            text = getattr(response, "text", None)
            if text:
                return str(text).strip()
        except (ValueError, AttributeError):
            pass
        return ""

    @staticmethod
    def _normalize_json_payload(raw: str) -> str:
        t = raw.strip()
        if t.startswith("```"):
            lines = t.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            while lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            t = "\n".join(lines).strip()
        return t

    def _safe_parse_json(self, payload: str) -> dict[str, Any]:
        normalized = self._normalize_json_payload(payload)
        return json.loads(normalized)

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

    def _build_user_prompt(
        self, topic: str, outlet_summaries: list[dict[str, str]], outlet_sources: list[str]
    ) -> str:
        schema = {
            "topic": topic,
            "missing_angle": "string",
            "confidence": "low|medium|high",
            "outlet_missing_angles": {source: "string" for source in outlet_sources},
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

    @staticmethod
    def _normalize_outlet_missing_angles(
        parsed: dict[str, Any], outlet_sources: list[str]
    ) -> dict[str, Any]:
        outlet_missing = parsed.get("outlet_missing_angles", {})
        if not isinstance(outlet_missing, dict):
            outlet_missing = {}
        return {source: outlet_missing.get(source) for source in outlet_sources}

    def generate_missing_angle(
        self,
        topic: str,
        db: Session,
        outlet_sources: list[str] | None = None,
    ) -> dict[str, Any]:
        normalized_topic = topic.strip()
        if not normalized_topic:
            return {
                "success": False,
                "data": {
                    "topic": "",
                    "missing_angle": None,
                    "confidence": None,
                    "outlet_missing_angles": {},
                    "from_cache": False,
                    "error": True,
                    "error_message": "Topic must not be empty.",
                },
                "error": "Topic must not be empty.",
            }

        outlet_sources = list(outlet_sources or [])
        if not outlet_sources:
            outlet_sources = compute_selected_outlets_from_db(normalized_topic, db)

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
                    "outlet_missing_angles": self._normalize_outlet_missing_angles(
                        parsed_summary, outlet_sources
                    ),
                    "from_cache": True,
                    "error": False,
                    "error_message": None,
                },
                "error": None,
            }

        if not self._model:
            return {
                "success": False,
                "data": {
                    "topic": normalized_topic,
                    "missing_angle": None,
                    "confidence": None,
                    "outlet_missing_angles": {s: None for s in outlet_sources},
                    "from_cache": False,
                    "error": True,
                    "error_message": "GEMINI_API_KEY is not configured.",
                },
                "error": "GEMINI_API_KEY is not configured.",
            }

        outlet_summaries = self._select_outlet_article_summaries(
            normalized_topic, db, outlet_sources
        )
        logger.info(
            "Missing angle Gemini input: topic=%r outlet_summary_count=%s sources=%s",
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
                    "outlet_missing_angles": {s: None for s in outlet_sources},
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
                    "outlet_missing_angles": {s: None for s in outlet_sources},
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
                "Calling Gemini for missing angle with %d outlet summaries (topic=%r)",
                len(outlet_summaries),
                normalized_topic,
            )
            user_prompt = self._build_user_prompt(
                normalized_topic, outlet_summaries, outlet_sources
            )
            response = self._model.generate_content(
                user_prompt,
                generation_config=genai.GenerationConfig(
                    max_output_tokens=1000,
                    temperature=0.7,
                ),
            )
            content_text = self._extract_gemini_text(response)
            parsed = self._safe_parse_json(content_text)
            missing_angle = parsed.get("missing_angle")
            confidence = parsed.get("confidence")
            outlet_missing_angles = self._normalize_outlet_missing_angles(parsed, outlet_sources)

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
                    "outlet_missing_angles": {s: None for s in outlet_sources},
                    "from_cache": False,
                    "error": True,
                    "error_message": str(exc),
                },
                "error": None,
            }

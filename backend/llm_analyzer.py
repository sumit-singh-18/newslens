from __future__ import annotations

import json
import logging
import os
import traceback
from datetime import datetime, timezone
from typing import Any

import google.generativeai as genai
from google.api_core import exceptions as google_api_exceptions
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import Article, TopicAnalysis, normalize_topic
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

# Primary / fallback (override via env). Use IDs valid for your Google AI Studio / Vertex project.
DEFAULT_GEMINI_PRO_MODEL = "gemini-2.5-pro"
DEFAULT_GEMINI_FLASH_MODEL = "gemini-2.5-flash"

GEMINI_PRO_TEMPERATURE = 0.45
GEMINI_FLASH_TEMPERATURE = 0.45

# User-safe copy when quota is exhausted on both tiers (matches frontend tone).
GEMINI_QUOTA_USER_MESSAGE = (
    "Editorial analysis temporarily unavailable. Check back shortly."
)


class LLMAnalyzer:
    """Gemini-only missing-angle analysis: Pro (primary) → Flash (fallback on 429/5xx)."""

    def __init__(self) -> None:
        self.api_key = os.getenv("GEMINI_API_KEY", "")
        if self.api_key and "your_gemini_api_key_here" not in self.api_key:
            genai.configure(api_key=self.api_key)

    @staticmethod
    def pro_model_name() -> str:
        return os.getenv("GEMINI_PRO_MODEL", DEFAULT_GEMINI_PRO_MODEL)

    @staticmethod
    def flash_model_name() -> str:
        return os.getenv("GEMINI_FLASH_MODEL", DEFAULT_GEMINI_FLASH_MODEL)

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
    def _is_gemini_quota_error(exc: BaseException) -> bool:
        if isinstance(
            exc,
            (
                google_api_exceptions.ResourceExhausted,
                google_api_exceptions.TooManyRequests,
            ),
        ):
            return True
        msg = str(exc).lower()
        if "resource exhausted" in msg or "too many requests" in msg:
            return True
        if "429" in msg and any(
            k in msg for k in ("quota", "exceeded", "rate", "limit", "resource")
        ):
            return True
        return False

    @staticmethod
    def _is_server_error_for_fallback(exc: BaseException) -> bool:
        """Tier-1 failure modes that trigger an immediate Flash attempt (5xx-class)."""
        if isinstance(
            exc,
            (
                google_api_exceptions.InternalServerError,
                google_api_exceptions.ServiceUnavailable,
                google_api_exceptions.BadGateway,
                google_api_exceptions.GatewayTimeout,
                google_api_exceptions.DeadlineExceeded,
            ),
        ):
            return True
        msg = str(exc).lower()
        if any(code in msg for code in (" 500", " 502", " 503", " 504")):
            return True
        if "internal error" in msg or "unavailable" in msg:
            return True
        return False

    def _generate_with_gemini(
        self, model_name: str, user_prompt: str, temperature: float
    ) -> Any:
        """Single call (override in tests if needed)."""
        model = genai.GenerativeModel(
            model_name,
            system_instruction=MISSING_ANGLE_SYSTEM_PROMPT,
        )
        return model.generate_content(
            user_prompt,
            generation_config=genai.GenerationConfig(
                max_output_tokens=1000,
                temperature=temperature,
            ),
        )

    def _quota_limited_response(self, normalized_topic: str, outlet_sources: list[str]) -> dict[str, Any]:
        return {
            "success": True,
            "data": {
                "topic": normalized_topic,
                "missing_angle": None,
                "analysis_status": "quota_limited",
                "confidence": None,
                "outlet_missing_angles": {s: None for s in outlet_sources},
                "from_cache": False,
                "error": True,
                "error_message": GEMINI_QUOTA_USER_MESSAGE,
            },
            "error": None,
        }

    def _llm_error_response(
        self, normalized_topic: str, outlet_sources: list[str], message: str
    ) -> dict[str, Any]:
        return {
            "success": True,
            "data": {
                "topic": normalized_topic,
                "missing_angle": None,
                "analysis_status": None,
                "confidence": None,
                "outlet_missing_angles": {s: None for s in outlet_sources},
                "from_cache": False,
                "error": True,
                "error_message": message,
            },
            "error": None,
        }

    @staticmethod
    def _normalize_outlet_missing_angles(
        parsed: dict[str, Any], outlet_sources: list[str]
    ) -> dict[str, Any]:
        outlet_missing = parsed.get("outlet_missing_angles", {})
        if not isinstance(outlet_missing, dict):
            outlet_missing = {}
        return {source: outlet_missing.get(source) for source in outlet_sources}

    def _persist_and_build_success(
        self,
        normalized_topic: str,
        outlet_sources: list[str],
        outlet_summaries: list[dict[str, str]],
        parsed: dict[str, Any],
        db: Session,
    ) -> dict[str, Any]:
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
                "analysis_status": "ok",
                "confidence": confidence,
                "outlet_missing_angles": outlet_missing_angles,
                "from_cache": False,
                "error": False,
                "error_message": None,
            },
            "error": None,
        }

    def generate_missing_angle(
        self,
        topic: str,
        db: Session,
        outlet_sources: list[str] | None = None,
    ) -> dict[str, Any]:
        normalized_topic = normalize_topic(topic)
        if not normalized_topic:
            return {
                "success": False,
                "data": {
                    "topic": "",
                    "missing_angle": None,
                    "analysis_status": None,
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
                    "analysis_status": "ok",
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

        if not self.api_key or "your_gemini_api_key_here" in self.api_key:
            return {
                "success": False,
                "data": {
                    "topic": normalized_topic,
                    "missing_angle": None,
                    "analysis_status": None,
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
                    "analysis_status": None,
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
                    "analysis_status": None,
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

        user_prompt = self._build_user_prompt(
            normalized_topic, outlet_summaries, outlet_sources
        )
        pro_name = self.pro_model_name()
        flash_name = self.flash_model_name()

        response = None
        pro_exc: BaseException | None = None
        try:
            logger.info(
                "Analysis attempted with Gemini Pro -> invoking model=%s",
                pro_name,
            )
            response = self._generate_with_gemini(
                pro_name, user_prompt, GEMINI_PRO_TEMPERATURE
            )
            logger.info("Analysis attempted with Gemini Pro -> Result: Success")
        except Exception as exc:
            pro_exc = exc
            logger.info(
                "Analysis attempted with Gemini Pro -> Result: Fail (%s)",
                type(exc).__name__,
            )
            allow_flash = self._is_gemini_quota_error(exc) or self._is_server_error_for_fallback(
                exc
            )
            if not allow_flash:
                traceback.print_exc()
                return self._llm_error_response(
                    normalized_topic, outlet_sources, str(exc)
                )
            try:
                logger.info(
                    "Gemini Pro failed (%s); attempting Gemini Flash (model=%s)",
                    type(exc).__name__,
                    flash_name,
                )
                response = self._generate_with_gemini(
                    flash_name, user_prompt, GEMINI_FLASH_TEMPERATURE
                )
                logger.info("Gemini Flash fallback -> Result: Success")
            except Exception as exc2:
                logger.warning(
                    "Gemini Flash fallback -> Result: Fail (%s)",
                    type(exc2).__name__,
                )
                pro_q = pro_exc is not None and self._is_gemini_quota_error(pro_exc)
                flash_q = self._is_gemini_quota_error(exc2)
                if pro_q and flash_q:
                    logger.warning(
                        "Both Gemini Pro and Flash returned quota-class errors; analysis_status=quota_limited"
                    )
                    return self._quota_limited_response(normalized_topic, outlet_sources)
                traceback.print_exc()
                return self._llm_error_response(
                    normalized_topic, outlet_sources, str(exc2)
                )

        try:
            content_text = self._extract_gemini_text(response)
            parsed = self._safe_parse_json(content_text)
            return self._persist_and_build_success(
                normalized_topic, outlet_sources, outlet_summaries, parsed, db
            )
        except Exception as exc:
            logger.warning("Failed to parse or persist Gemini JSON: %s", exc)
            traceback.print_exc()
            return self._llm_error_response(
                normalized_topic, outlet_sources, str(exc)
            )

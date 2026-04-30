from __future__ import annotations

from typing import Any


class NLPPipeline:
    """Phase 2 placeholder. Loads once and reused across requests."""

    def __init__(self) -> None:
        self.model_name = "cardiffnlp/twitter-roberta-base-sentiment"

    def analyze_article(self, text: str) -> dict[str, Any]:
        # Placeholder response for Phase 1.
        return {
            "sentiment_label": None,
            "sentiment_score": None,
            "bias_label": None,
            "bias_score": None,
            "raw_scores": {},
        }

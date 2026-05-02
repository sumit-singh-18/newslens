from __future__ import annotations

import os
import re
import threading
from html import unescape
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from transformers import AutoModelForSequenceClassification, AutoTokenizer, pipeline

from .bias_utils import bias_label_from_axis
from .database import Article, ArticleScore


DEFAULT_SENTIMENT_MODEL = "cardiffnlp/twitter-roberta-base-sentiment"
DEFAULT_BIAS_MODEL = "bucketresearch/politicalBiasBERT"

# Blend HF axis score with lexical cues (HF alone often collapses to Center).
HF_BIAS_BLEND = 0.4
KEYWORD_BIAS_BLEND = 0.6

LEFT_BIAS_KEYWORDS = (
    "climate justice",
    "equity",
    "systemic",
    "marginalized",
    "progressive",
    "regulation",
    "universal",
    "social",
)

RIGHT_BIAS_KEYWORDS = (
    "second amendment",
    "free market",
    "deregulation",
    "socialism",
    "conservative",
    "tradition",
    "patriot",
    "border",
    "freedom",
)


class NLPPipeline:
    """Singleton NLP pipeline for sentiment and bias scoring."""

    _instance: "NLPPipeline | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self.sentiment_model_name = os.getenv("SENTIMENT_MODEL_NAME", DEFAULT_SENTIMENT_MODEL)
        self.bias_model_name = os.getenv("BIAS_MODEL_NAME", DEFAULT_BIAS_MODEL)

        self.sentiment_tokenizer = AutoTokenizer.from_pretrained(self.sentiment_model_name)
        self.sentiment_model = AutoModelForSequenceClassification.from_pretrained(self.sentiment_model_name)
        self.sentiment_pipeline = pipeline(
            task="text-classification",
            model=self.sentiment_model,
            tokenizer=self.sentiment_tokenizer,
            top_k=None,
            truncation=True,
            max_length=512,
            device=-1,
        )
        self.sentiment_id2label = self.sentiment_model.config.id2label

        self.bias_tokenizer = AutoTokenizer.from_pretrained(self.bias_model_name)
        self.bias_model = AutoModelForSequenceClassification.from_pretrained(self.bias_model_name)
        self.bias_pipeline = pipeline(
            task="text-classification",
            model=self.bias_model,
            tokenizer=self.bias_tokenizer,
            top_k=None,
            truncation=True,
            max_length=512,
            device=-1,
        )
        self.bias_id2label = self.bias_model.config.id2label

    @classmethod
    def get_instance(cls) -> "NLPPipeline":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @staticmethod
    def clean_text(text: str | None) -> str:
        if not text:
            return ""
        cleaned = unescape(text)
        cleaned = re.sub(r"<[^>]+>", " ", cleaned)
        cleaned = re.sub(r"https?://\S+|www\.\S+", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned.strip()

    @staticmethod
    def _normalize_label(label: str) -> str:
        return label.replace("_", " ").replace("-", " ").strip().lower()

    @staticmethod
    def _human_label(label: str) -> str:
        return NLPPipeline._normalize_label(label).title()

    def _resolve_label(self, label: str, id2label: dict[int, str], task: str) -> str:
        normalized = self._normalize_label(label)
        match = re.search(r"label\s*(\d+)$", normalized)
        if match:
            label_idx = int(match.group(1))
            mapped = id2label.get(label_idx)
            if mapped:
                normalized = self._normalize_label(str(mapped))
            if task == "sentiment" and self.sentiment_model_name == DEFAULT_SENTIMENT_MODEL:
                sentiment_map = {0: "negative", 1: "neutral", 2: "positive"}
                if normalized.startswith("label "):
                    normalized = sentiment_map.get(label_idx, normalized)
        return normalized.title()

    def _truncate_text(self, text: str, tokenizer: Any) -> str:
        tokenized = tokenizer(
            text,
            truncation=True,
            max_length=512,
            add_special_tokens=True,
            return_attention_mask=False,
            return_token_type_ids=False,
        )
        token_ids = tokenized["input_ids"]
        return tokenizer.decode(token_ids, skip_special_tokens=True)

    def _select_top_label(
        self, predictions: list[dict[str, Any]], id2label: dict[int, str], task: str
    ) -> tuple[str, float]:
        top = max(predictions, key=lambda item: float(item["score"]))
        return self._resolve_label(str(top["label"]), id2label, task), float(top["score"])

    def _to_raw_score_map(
        self, predictions: list[dict[str, Any]], id2label: dict[int, str], task: str
    ) -> dict[str, float]:
        return {
            self._resolve_label(str(item["label"]), id2label, task): float(item["score"])
            for item in predictions
        }

    @staticmethod
    def _bias_label_axis_weight(label: str) -> float:
        """Map classifier label text to 0=left … 1=right."""
        s = str(label).strip().lower()
        if any(x in s for x in ("left", "liberal", "progressive", "socialist")):
            return 0.25
        if any(x in s for x in ("right", "conservative", "republican", "nationalist", "populist")):
            return 0.75
        return 0.5

    def _hf_bias_axis(self, raw_bias: dict[str, float]) -> float:
        if not raw_bias:
            return 0.5
        total = 0.0
        wsum = 0.0
        for lab, p in raw_bias.items():
            try:
                pf = float(p)
            except (TypeError, ValueError):
                continue
            total += pf * self._bias_label_axis_weight(lab)
            wsum += pf
        return total / wsum if wsum > 1e-9 else 0.5

    @staticmethod
    def _keyword_bias_axis(text: str) -> float:
        t = (text or "").lower()
        left_hits = 0
        right_hits = 0
        for kw in LEFT_BIAS_KEYWORDS:
            if " " in kw:
                left_hits += t.count(kw.lower())
            else:
                left_hits += len(re.findall(rf"\b{re.escape(kw.lower())}\b", t))
        for kw in RIGHT_BIAS_KEYWORDS:
            if " " in kw:
                right_hits += t.count(kw.lower())
            else:
                right_hits += len(re.findall(rf"\b{re.escape(kw.lower())}\b", t))
        if left_hits + right_hits == 0:
            return 0.5
        return right_hits / (left_hits + right_hits)

    def analyze_batch(self, texts: list[str]) -> list[dict[str, Any]]:
        if not texts:
            return []

        cleaned_texts = [self.clean_text(text) for text in texts]
        truncated_sentiment = [self._truncate_text(text, self.sentiment_tokenizer) for text in cleaned_texts]
        truncated_bias = [self._truncate_text(text, self.bias_tokenizer) for text in cleaned_texts]

        sentiment_results = self.sentiment_pipeline(truncated_sentiment, batch_size=16)
        bias_results = self.bias_pipeline(truncated_bias, batch_size=16)

        if len(sentiment_results) != len(bias_results):
            raise RuntimeError("Sentiment and bias result counts do not match.")

        output: list[dict[str, Any]] = []
        for text_full, sentiment_prediction, bias_prediction in zip(
            cleaned_texts, sentiment_results, bias_results
        ):
            sentiment_label, sentiment_score = self._select_top_label(
                sentiment_prediction, self.sentiment_id2label, "sentiment"
            )
            raw_bias = self._to_raw_score_map(bias_prediction, self.bias_id2label, "bias")
            hf_axis = self._hf_bias_axis(raw_bias)
            kw_axis = self._keyword_bias_axis(text_full)
            final_axis = HF_BIAS_BLEND * hf_axis + KEYWORD_BIAS_BLEND * kw_axis
            bias_label = bias_label_from_axis(final_axis)

            output.append(
                {
                    "sentiment_label": sentiment_label,
                    "sentiment_score": sentiment_score,
                    "bias_label": bias_label,
                    "bias_score": round(float(final_axis), 6),
                    "raw_scores": {
                        "sentiment": self._to_raw_score_map(
                            sentiment_prediction, self.sentiment_id2label, "sentiment"
                        ),
                        "bias": raw_bias,
                        "bias_blend": {
                            "hf_axis": round(hf_axis, 6),
                            "keyword_axis": round(kw_axis, 6),
                            "final_axis": round(final_axis, 6),
                        },
                    },
                }
            )
        return output

    def score_topic_articles(self, topic: str, db: Session) -> dict[str, Any]:
        normalized_topic = topic.strip()
        if not normalized_topic:
            raise ValueError("Topic must not be empty.")

        articles = list(
            db.scalars(select(Article).where(Article.topic == normalized_topic).order_by(Article.id.asc())).all()
        )
        if not articles:
            return {"topic": normalized_topic, "article_count": 0, "scored_count": 0}

        analyses = self.analyze_batch([article.content for article in articles])
        article_ids = [article.id for article in articles]

        db.execute(delete(ArticleScore).where(ArticleScore.article_id.in_(article_ids)))
        if len(articles) != len(analyses):
            raise RuntimeError("Article and analysis counts do not match.")

        for article, analysis in zip(articles, analyses):
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
        db.commit()

        return {
            "topic": normalized_topic,
            "article_count": len(articles),
            "scored_count": len(analyses),
        }

    def rescore_all_articles(self, db: Session, batch_size: int = 16) -> dict[str, Any]:
        """Re-run analyze_batch for every article and replace article_scores (blend-aware)."""
        articles = list(db.scalars(select(Article).order_by(Article.id.asc())).all())
        if not articles:
            db.execute(delete(ArticleScore))
            db.commit()
            return {"article_count": 0, "scored_count": 0}

        db.execute(delete(ArticleScore))
        db.flush()

        scored = 0
        for i in range(0, len(articles), batch_size):
            chunk = articles[i : i + batch_size]
            analyses = self.analyze_batch([a.content for a in chunk])
            if len(chunk) != len(analyses):
                raise RuntimeError("Article and analysis counts do not match.")
            for article, analysis in zip(chunk, analyses):
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
                scored += 1
        db.commit()
        return {"article_count": len(articles), "scored_count": scored}

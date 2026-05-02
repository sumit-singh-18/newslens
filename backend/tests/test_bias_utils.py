from __future__ import annotations

from backend.bias_utils import (
    bias_distribution_from_outlets,
    bias_label_from_axis,
    bias_spectrum_bucket,
)


def test_bias_spectrum_bucket_keywords() -> None:
    assert bias_spectrum_bucket("Lean Left") == "left"
    assert bias_spectrum_bucket("Right") == "right"
    assert bias_spectrum_bucket("Conservative") == "right"
    assert bias_spectrum_bucket("Liberal") == "left"
    assert bias_spectrum_bucket("Center") == "center"
    assert bias_spectrum_bucket(None) == "center"


def test_bias_label_from_axis() -> None:
    assert bias_label_from_axis(0.40) == "Left"
    assert bias_label_from_axis(0.50) == "Center"
    assert bias_label_from_axis(0.55) == "Right"


def test_bias_distribution_counts_outlets() -> None:
    outlets = [
        {"article_count": 1, "dominant_bias_label": "Left"},
        {"article_count": 1, "dominant_bias_label": "Right"},
        {"article_count": 2, "dominant_bias_label": "Center"},
        {"article_count": 0, "dominant_bias_label": "Right"},
    ]
    d = bias_distribution_from_outlets(outlets)
    assert d["left_count"] == 1
    assert d["right_count"] == 1
    assert d["center_count"] == 1
    assert d["outlet_total"] == 3
    assert d["denominator"] == 5
    assert d["left_pct"] == 20
    assert d["center_pct"] == 20
    assert d["right_pct"] == 20

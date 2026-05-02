from __future__ import annotations

"""Shared helpers for mapping NLP bias labels onto left / center / right buckets."""

_LEFT_HINTS = (
    "left",
    "liberal",
    "progressive",
    "socialist",
    "democrat",
)
_RIGHT_HINTS = (
    "right",
    "conservative",
    "republican",
    "nationalist",
    "populist",
)


def bias_spectrum_bucket(label: str | None) -> str:
    """
    Map a bias label string to 'left', 'center', or 'right'.
    Uses substring hints so varied model outputs (e.g. Lean Left, Far Right) classify consistently.
    Unknown / empty labels are treated as center for aggregation (same as neutral bucket).
    """
    if not label or not str(label).strip():
        return "center"
    s = str(label).strip().lower()
    for h in _LEFT_HINTS:
        if h in s:
            return "left"
    for h in _RIGHT_HINTS:
        if h in s:
            return "right"
    return "center"


def bias_distribution_from_outlets(outlets: list[dict]) -> dict:
    """Count one spectrum bucket per outlet that has at least one scored article."""
    left = center = right = 0
    for o in outlets:
        if not isinstance(o, dict):
            continue
        ac = o.get("article_count") or 0
        if ac <= 0:
            continue
        bucket = bias_spectrum_bucket(o.get("dominant_bias_label"))
        if bucket == "left":
            left += 1
        elif bucket == "right":
            right += 1
        else:
            center += 1
    total = left + center + right
    if total <= 0:
        return {
            "left_pct": 0,
            "center_pct": 0,
            "right_pct": 0,
            "left_count": 0,
            "center_count": 0,
            "right_count": 0,
            "outlet_total": 0,
        }

    def pct(n: int) -> int:
        return int(round(100 * n / total))

    return {
        "left_pct": pct(left),
        "center_pct": pct(center),
        "right_pct": pct(right),
        "left_count": left,
        "center_count": center,
        "right_count": right,
        "outlet_total": total,
    }

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


FIXED_OUTLET_DENOMINATOR = 5


def bias_distribution_from_outlets(outlets: list[dict]) -> dict:
    """Count spectrum buckets per outlet; percentages use a fixed denominator of 5."""
    return bias_distribution_fixed_denominator(outlets, FIXED_OUTLET_DENOMINATOR)


def bias_distribution_fixed_denominator(outlets: list[dict], denominator: int) -> dict:
    """
    Count left/center/right from each outlet's dominant bias label (mapped to spectrum).
    left_pct = (left_count / denominator) * 100, same for center and right.
    """
    left = center = right = 0
    active = 0
    for o in outlets:
        if not isinstance(o, dict):
            continue
        ac = o.get("article_count") or 0
        if ac <= 0:
            continue
        active += 1
        bucket = bias_spectrum_bucket(o.get("dominant_bias_label"))
        if bucket == "left":
            left += 1
        elif bucket == "right":
            right += 1
        else:
            center += 1
    den = max(1, int(denominator))
    return {
        "left_pct": int(round(100 * left / den)),
        "center_pct": int(round(100 * center / den)),
        "right_pct": int(round(100 * right / den)),
        "left_count": left,
        "center_count": center,
        "right_count": right,
        "outlet_total": active,
        "denominator": den,
    }


def bias_label_from_axis(axis: float) -> str:
    """
    Map blended bias axis (0≈left … 1≈right) to a headline label.
    Thresholds match NLPPipeline's blend output range on real articles.
    """
    try:
        a = float(axis)
    except (TypeError, ValueError):
        return "Center"
    if a < 0.47:
        return "Left"
    if a > 0.51:
        return "Right"
    return "Center"


def extrem_bias_outlets(outlets: list[dict]) -> tuple[str | None, str | None]:
    """Most left = lowest avg bias score; most right = highest."""
    scored: list[dict] = []
    for o in outlets:
        if not isinstance(o, dict):
            continue
        if (o.get("article_count") or 0) <= 0:
            continue
        abs_score = o.get("avg_bias_score")
        if abs_score is None:
            continue
        try:
            float(abs_score)
        except (TypeError, ValueError):
            continue
        scored.append(o)
    if not scored:
        return None, None
    lo = min(scored, key=lambda x: float(x["avg_bias_score"]))
    hi = max(scored, key=lambda x: float(x["avg_bias_score"]))
    src_lo = lo.get("source")
    src_hi = hi.get("source")
    lo_name = str(src_lo) if src_lo is not None else None
    hi_name = str(src_hi) if src_hi is not None else None
    return lo_name, hi_name

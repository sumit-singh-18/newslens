from __future__ import annotations

import re

# Dynamic credibility scoring based on signals derived from NewsAPI's `article["source"]` object.
#
# This is intentionally heuristic (domain, verification, description quality, etc.) so the system can
# expand beyond a hardcoded outlet allowlist.
MIN_CREDIBILITY_SCORE = 60


OUTLET_DESCRIPTION_MIN_LEN = 30
OUTLET_DESCRIPTION_MAX_LEN = 300


credible_tlds = [".com", ".org", ".net", ".co.uk", ".in", ".de", ".fr", ".au", ".ca", ".co.za", ".jp", ".br"]


def compute_credibility_score(source: dict) -> int:
    score = 0

    # Signal 1: Has verified NewsAPI source ID (+40)
    # NewsAPI only assigns IDs to manually verified outlets.
    if source.get("id") and source["id"] != "":
        score += 40

    # Signal 2: HTTPS domain (+10)
    url = source.get("url", "")
    if isinstance(url, str) and url.startswith("https://"):
        score += 10

    # Signal 3: Credible TLD (+10)
    if isinstance(url, str):
        if any(url.endswith(t) or f"{t}/" in url for t in credible_tlds):
            score += 10

    # Signal 4: Domain length proxy (+10)
    # Short domains = established outlets
    domain = ""
    if isinstance(url, str):
        domain = re.sub(r"https?://(www\\.)?", "", url)
        domain = domain.split("/")[0]
    if domain and len(domain) <= 20:
        score += 10

    # Signal 5: Description quality (+15)
    # Real outlets have clean factual descriptions
    description = source.get("description", "") or ""
    if isinstance(description, str) and OUTLET_DESCRIPTION_MIN_LEN <= len(description) <= OUTLET_DESCRIPTION_MAX_LEN:
        score += 15

    # Signal 6: Language is English (+15)
    # We only analyze English articles accurately.
    if source.get("language") == "en":
        score += 15

    return min(score, 100)


def is_credible(source: dict) -> bool:
    return compute_credibility_score(source or {}) >= MIN_CREDIBILITY_SCORE


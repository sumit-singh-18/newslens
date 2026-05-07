CREDIBLE_DOMAINS = [
    # Tier 1 — Wire services
    "reuters.com",
    "apnews.com",
    # Tier 2 — International broadcasters
    "bbc.co.uk",
    "bbc.com",
    "npr.org",
    "dw.com",
    "france24.com",
    "aljazeera.com",
    # Tier 3 — Major newspapers
    "theguardian.com",
    "washingtonpost.com",
    "nytimes.com",
    "wsj.com",
    "ft.com",
    "theatlantic.com",
    "foreignpolicy.com",
    "economist.com",
    # Tier 4 — Cable/digital news
    "cnn.com",
    "foxnews.com",
    "msnbc.com",
    "bloomberg.com",
    "politico.com",
    "axios.com",
    "thehill.com",
    "nbcnews.com",
    "cbsnews.com",
    "abcnews.go.com",
    "newsweek.com",
    "time.com",
    "usatoday.com",
    # Tier 5 — International credible outlets
    "indianexpress.com",
    "thehindu.com",
    "ndtv.com",
    "hindustantimes.com",
    "dawn.com",
    "scmp.com",
    "japantimes.co.jp",
    "haaretz.com",
    "theconversation.com",
]


def get_domains_string() -> str:
    return ",".join(CREDIBLE_DOMAINS)

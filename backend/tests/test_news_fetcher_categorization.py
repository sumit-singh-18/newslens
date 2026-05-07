from backend.news_fetcher import detect_source_categories_for_query, relax_search_query, source_ids_for_categories


def test_detect_source_categories_returns_domains_pool():
    assert detect_source_categories_for_query("election results") == ["credible_domains"]
    assert detect_source_categories_for_query("chip software repair supply chain") == ["credible_domains"]


def test_relax_search_query_strips_years_and_filler():
    assert relax_search_query("Breaking news: AI chips in 2025") == "AI chips"


def test_source_ids_for_categories_empty():
    """Domains allowlist replaces category-based NewsAPI source IDs."""
    assert source_ids_for_categories(["GENERAL", "TECH"]) == []

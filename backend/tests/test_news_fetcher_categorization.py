from backend.news_fetcher import (
    VETTED_SOURCES_BY_CATEGORY,
    detect_source_categories_for_query,
    relax_search_query,
    source_ids_for_categories,
)


def test_detect_source_categories_always_general():
    assert detect_source_categories_for_query("election results") == ["GENERAL"]


def test_detect_source_categories_adds_tech_finance_science():
    cats = detect_source_categories_for_query("chip software repair supply chain")
    assert cats == ["GENERAL", "TECH"]
    assert detect_source_categories_for_query("stock market CBDC policy") == [
        "GENERAL",
        "FINANCE",
    ]
    assert "SCIENCE_HEALTH" in detect_source_categories_for_query("vaccine trial FDA update")


def test_relax_search_query_strips_years_and_filler():
    assert relax_search_query("Breaking news: AI chips in 2025") == "AI chips"


def test_vetted_source_counts():
    assert len(VETTED_SOURCES_BY_CATEGORY["GENERAL"]) >= 20
    assert len(VETTED_SOURCES_BY_CATEGORY["TECH"]) == 10
    assert len(VETTED_SOURCES_BY_CATEGORY["FINANCE"]) == 10
    assert len(VETTED_SOURCES_BY_CATEGORY["SCIENCE_HEALTH"]) == 5


def test_source_ids_for_categories_union_order():
    ids = source_ids_for_categories(["GENERAL", "TECH"])
    assert len(ids) == len(set(ids))
    assert ids[0] == "associated-press"

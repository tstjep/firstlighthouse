"""E2E integration tests — real SerpAPI calls, no mocks, no LinkedIn API.

Run with:
    pytest tests/test_integration_serp.py -v -m integration

Skipped by default (--integration flag or PYTEST_RUN_INTEGRATION=1 required).

These tests call the real SerpAPI and verify that for well-known UK immigration
companies we can find LinkedIn profiles and enrichment data for each sheet tab.

Real companies used:
  LawFirms        — Fragomen (world's largest corporate immigration firm)
  Advisors        — Global Migrate (OISC-regulated corporate immigration adviser)
  Charities       — Refugee Council (UK's leading refugee charity)
  LegaltechBrokers — BamLegal (legaltech consultant known for law firm software)
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as cfg
from tools.serp_tool import SerpSearchTool
from agents.immigration_contact_agent import (
    _search_profiles_for_companies,
    _build_search_queries,
    _build_fallback_queries,
    _parse_profiles_from_serp,
)
from agents.immigration_enrich_agent import (
    _company_queries,
    _prefetch_searches,
)


# ── Pytest marker setup ───────────────────────────────────────────────────────

def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: mark test as an integration test that calls real APIs",
    )


def _integration_enabled() -> bool:
    return (
        os.environ.get("PYTEST_RUN_INTEGRATION", "").lower() in ("1", "true", "yes")
        or "--integration" in sys.argv
    )


pytestmark = pytest.mark.skipif(
    not _integration_enabled(),
    reason="Integration tests disabled — set PYTEST_RUN_INTEGRATION=1 or pass --integration",
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def serp_tool():
    assert cfg.SERPAPI_KEY, "SERPAPI_KEY must be set in config.py"
    return SerpSearchTool(api_key=cfg.SERPAPI_KEY)


def _company(name: str, website: str = "", linkedin: str = "") -> dict:
    return {
        "name":      name,
        "rating":    "8",
        "notes":     "",
        "website":   website,
        "linkedin":  linkedin,
        "size":      "",
        "hq":        "London, UK",
        "sheet_row": 2,
    }


def _enrich_company(name: str, website: str = "", linkedin: str = "") -> dict:
    return {
        "row_index":    2,
        "company_name": name,
        "website":      website,
        "linkedin":     linkedin,
        "size":         "",
        "hq_location":  "",
    }


# ── LawFirms: Fragomen ────────────────────────────────────────────────────────

class TestLawFirmsFragomen:
    """Fragomen — world's largest corporate immigration law firm."""

    COMPANY = _company(
        name="Fragomen",
        website="https://www.fragomen.com",
        linkedin="https://www.linkedin.com/company/fragomen",
    )

    def test_contact_search_finds_at_least_one_profile(self):
        results = asyncio.run(
            _search_profiles_for_companies(
                [self.COMPANY], cfg.SERPAPI_KEY,
                tab="LawFirms", max_profiles=3,
            )
        )
        profiles = results.get("Fragomen", [])
        assert len(profiles) >= 1, "Expected at least 1 LinkedIn profile for Fragomen"

    def test_contact_profiles_have_linkedin_urls(self):
        results = asyncio.run(
            _search_profiles_for_companies(
                [self.COMPANY], cfg.SERPAPI_KEY,
                tab="LawFirms", max_profiles=3,
            )
        )
        profiles = results.get("Fragomen", [])
        with_url = [p for p in profiles if p["url"]]
        assert len(with_url) >= 1, "Expected at least 1 profile with a LinkedIn URL"

    def test_contact_profiles_have_names(self):
        results = asyncio.run(
            _search_profiles_for_companies(
                [self.COMPANY], cfg.SERPAPI_KEY,
                tab="LawFirms", max_profiles=3,
            )
        )
        profiles = results.get("Fragomen", [])
        for p in profiles:
            assert p["first_name"] or p["last_name"], "Profile should have a name"

    def test_enrich_queries_return_results(self, serp_tool):
        company = _enrich_company("Fragomen", website="https://www.fragomen.com")
        qa, qb = _company_queries(company, tab="LawFirms")
        assert qa is not None
        result = asyncio.run(serp_tool.execute(query=qa, num=5))
        assert "fragomen" in result.lower(), "SerpAPI should return results mentioning Fragomen"

    def test_enrich_linkedin_query_finds_company_page(self, serp_tool):
        company = _enrich_company("Fragomen")
        _, qb = _company_queries(company, tab="LawFirms")
        assert qb is not None
        result = asyncio.run(serp_tool.execute(query=qb, num=3))
        assert "linkedin.com/company" in result.lower()


# ── Advisors: Global Migrate ──────────────────────────────────────────────────

class TestAdvisorsGlobalMigrate:
    """Global Migrate — OISC-regulated corporate immigration adviser."""

    COMPANY = _company(
        name="Global Migrate",
        website="https://www.globalmigrate.co.uk",
    )

    def test_contact_search_returns_result_or_fallback(self):
        """Even if no profiles found, the function should return without error."""
        results = asyncio.run(
            _search_profiles_for_companies(
                [self.COMPANY], cfg.SERPAPI_KEY,
                tab="Advisors", max_profiles=3,
            )
        )
        assert "Global Migrate" in results

    def test_search_queries_contain_company_name(self):
        queries = _build_search_queries(self.COMPANY, tab="Advisors")
        assert any("Global Migrate" in q for q in queries)

    def test_search_queries_target_linkedin_profiles(self):
        queries = _build_search_queries(self.COMPANY, tab="Advisors")
        linkedin_queries = [q for q in queries if "site:linkedin.com/in" in q]
        assert len(linkedin_queries) >= 1

    def test_enrich_queries_return_results(self, serp_tool):
        company = _enrich_company("Global Migrate", website="https://www.globalmigrate.co.uk")
        qa, _ = _company_queries(company, tab="Advisors")
        assert qa is not None
        result = asyncio.run(serp_tool.execute(query=qa, num=5))
        assert len(result) > 100, "Should return non-trivial SerpAPI results"

    def test_fallback_queries_include_broad_linkedin(self):
        queries = _build_fallback_queries(self.COMPANY)
        assert any("site:linkedin.com/in" in q for q in queries)
        assert any("Global Migrate" in q for q in queries)


# ── Charities: Refugee Council ────────────────────────────────────────────────

class TestCharitiesRefugeeCouncil:
    """Refugee Council — UK's leading charity for refugees and asylum seekers."""

    COMPANY = _company(
        name="Refugee Council",
        website="https://www.refugeecouncil.org.uk",
        linkedin="https://www.linkedin.com/company/refugee-council",
    )

    def test_contact_search_returns_result(self):
        results = asyncio.run(
            _search_profiles_for_companies(
                [self.COMPANY], cfg.SERPAPI_KEY,
                tab="Charities", max_profiles=3,
            )
        )
        assert "Refugee Council" in results

    def test_charity_queries_mention_ceo_or_director(self):
        queries = _build_search_queries(self.COMPANY, tab="Charities")
        all_text = " ".join(queries)
        assert "CEO" in all_text or "Director" in all_text or "Chief Executive" in all_text

    def test_enrich_finds_refugee_council_website(self, serp_tool):
        company = _enrich_company("Refugee Council")
        qa, _ = _company_queries(company, tab="Charities")
        assert qa is not None
        result = asyncio.run(serp_tool.execute(query=qa, num=5))
        assert "refugee" in result.lower()

    def test_enrich_linkedin_query_targets_company(self, serp_tool):
        company = _enrich_company("Refugee Council")
        _, qb = _company_queries(company, tab="Charities")
        assert qb is not None
        result = asyncio.run(serp_tool.execute(query=qb, num=3))
        assert "linkedin" in result.lower()


# ── LegaltechBrokers: BamLegal ────────────────────────────────────────────────

class TestLegaltechBrokersBamLegal:
    """BamLegal — UK legaltech consultant known for law firm software selection."""

    COMPANY = _company(
        name="BamLegal",
        website="https://www.bamlegal.co.uk",
    )

    def test_contact_search_returns_result(self):
        results = asyncio.run(
            _search_profiles_for_companies(
                [self.COMPANY], cfg.SERPAPI_KEY,
                tab="LegaltechBrokers", max_profiles=3,
            )
        )
        assert "BamLegal" in results

    def test_legaltech_queries_mention_consultant_or_director(self):
        queries = _build_search_queries(self.COMPANY, tab="LegaltechBrokers")
        all_text = " ".join(queries)
        assert "Consultant" in all_text or "Director" in all_text or "Managing" in all_text

    def test_enrich_returns_results(self, serp_tool):
        company = _enrich_company("BamLegal", website="https://www.bamlegal.co.uk")
        qa, _ = _company_queries(company, tab="LegaltechBrokers")
        assert qa is not None
        result = asyncio.run(serp_tool.execute(query=qa, num=5))
        assert len(result) > 100

    def test_enrich_linkedin_query(self, serp_tool):
        company = _enrich_company("BamLegal")
        _, qb = _company_queries(company, tab="LegaltechBrokers")
        assert qb is not None
        result = asyncio.run(serp_tool.execute(query=qb, num=3))
        assert "linkedin" in result.lower()


# ── Cross-tab: prefetch_searches ─────────────────────────────────────────────

class TestPrefetchSearches:
    """Verify _prefetch_searches works for all tabs with real companies."""

    @pytest.mark.parametrize("tab,name,website", [
        ("LawFirms",        "Fragomen",       "https://www.fragomen.com"),
        ("Advisors",        "Global Migrate",  "https://www.globalmigrate.co.uk"),
        ("Charities",       "Refugee Council", "https://www.refugeecouncil.org.uk"),
        ("LegaltechBrokers","BamLegal",        "https://www.bamlegal.co.uk"),
    ])
    def test_prefetch_returns_results_for_each_tab(self, tab, name, website):
        companies = [{
            "row_index":    2,
            "company_name": name,
            "website":      website,
            "linkedin":     "",
            "size":         "",
            "hq_location":  "",
        }]
        results = asyncio.run(
            _prefetch_searches(companies, cfg.SERPAPI_KEY, tab=tab)
        )
        assert 2 in results, f"Expected results for row_index=2 ({tab}: {name})"
        row_results = results[2]
        assert row_results.get("a") or row_results.get("b"), \
            f"Expected at least one search result for {name}"


# ── Parse quality: real SerpAPI output ───────────────────────────────────────

class TestParseRealSerpOutput:
    """Verify profile parsing produces valid output from real SerpAPI searches."""

    def test_fragomen_linkedin_search_parseable(self, serp_tool):
        """Real LinkedIn search for Fragomen partners should yield parseable profiles."""
        query = 'site:linkedin.com/in "Fragomen" ("Managing Partner" OR "Partner" OR "Director")'
        result = asyncio.run(serp_tool.execute(query=query, num=10))
        profiles = _parse_profiles_from_serp(result, "Fragomen")
        # Should find at least some profiles (Fragomen is a large, well-indexed firm)
        assert len(profiles) >= 1, (
            f"Expected ≥1 parsed profile from Fragomen LinkedIn search, got 0.\n"
            f"Raw SerpAPI output (first 500 chars):\n{result[:500]}"
        )

    def test_parsed_profiles_have_valid_structure(self, serp_tool):
        query = 'site:linkedin.com/in "Fragomen" "Partner"'
        result = asyncio.run(serp_tool.execute(query=query, num=10))
        profiles = _parse_profiles_from_serp(result, "Fragomen")
        for p in profiles:
            assert "url" in p
            assert "first_name" in p
            assert "last_name" in p
            assert "title_hint" in p
            if p["url"]:
                assert "linkedin.com/in/" in p["url"]

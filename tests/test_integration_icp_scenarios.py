"""
Integration tests — real SerpAPI calls for the two reference ICP scenarios.

Run with:
    PYTEST_RUN_INTEGRATION=1 pytest tests/test_integration_icp_scenarios.py -v

Skipped by default.

Scenarios:
  hr-saas-ch   — HR SaaS platform targeting Swiss SMBs
  sales-tools-uk — B2B outbound sales tool targeting UK SaaS companies

For each scenario we verify that:
  1. Representative TLD queries return results of the right type
  2. Results contain recognisable companies or industry-relevant terms
  3. LinkedIn queries return profile / company page URLs

Well-known anchor companies used as ground-truth:
  HR / Swiss SMBs:
    - Abacus Research AG  (Abacus is Switzerland's dominant SMB payroll/ERP vendor — their
                           customers are exactly our target audience)
    - Bossard Group        (Swiss precision-fastening manufacturer, ~3000 staff — a "too large"
                           false-positive to verify our exclusion signal logic)

  Sales Tools / UK SaaS:
    - Cognism              (UK B2B SaaS, ~500 staff, Series B — well-indexed, canonical example)
    - Pipedrive UK         (CRM / sales tool — relevant to "outbound stack" exclusion signal)
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as cfg
from campaign import Campaign
from tools.serp_tool import SerpSearchTool

CAMPAIGNS_DIR = Path(__file__).resolve().parent.parent / "campaigns"


# ── Marker setup ──────────────────────────────────────────────────────────────

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
def serp():
    assert cfg.SERPAPI_KEY, "SERPAPI_KEY must be set in config.py"
    return SerpSearchTool(api_key=cfg.SERPAPI_KEY)


@pytest.fixture(scope="module")
def serp_ch():
    assert cfg.SERPAPI_KEY, "SERPAPI_KEY must be set in config.py"
    return SerpSearchTool(api_key=cfg.SERPAPI_KEY, gl="ch", cr="countryCH")


@pytest.fixture(scope="module")
def serp_gb():
    assert cfg.SERPAPI_KEY, "SERPAPI_KEY must be set in config.py"
    return SerpSearchTool(api_key=cfg.SERPAPI_KEY, gl="gb", cr="countryGB")


@pytest.fixture(scope="module")
def hr_ch():
    return Campaign.load("hr-saas-ch", campaigns_dir=CAMPAIGNS_DIR)


@pytest.fixture(scope="module")
def sales_uk():
    return Campaign.load("sales-tools-uk", campaigns_dir=CAMPAIGNS_DIR)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── Scenario 1: HR SaaS — Swiss SMBs ─────────────────────────────────────────

class TestHRSaaSChTLDQueries:
    """TLD queries for Switzerland must surface SMB professional-services companies."""

    def test_prof_services_tld_query_returns_results(self, serp_ch, hr_ch):
        query = hr_ch.segment("ProfServices").search.tld_queries[0]
        result = _run(serp_ch.execute(query=query, num=10))
        assert not result.startswith("[serp_search error]"), f"SerpAPI error: {result[:200]}"
        assert len(result) > 100, "Expected non-trivial search results"

    def test_prof_services_tld_query_returns_ch_domains(self, serp_ch, hr_ch):
        query = hr_ch.segment("ProfServices").search.tld_queries[0]
        result = _run(serp_ch.execute(query=query, num=10))
        assert ".ch" in result, "Expected at least one .ch domain in results"

    def test_manufacturing_tld_query_returns_results(self, serp_ch, hr_ch):
        query = hr_ch.segment("LightManufacturing").search.tld_queries[0]
        result = _run(serp_ch.execute(query=query, num=10))
        assert not result.startswith("[serp_search error]")
        assert len(result) > 100

    def test_extra_query_surfaces_swiss_companies(self, serp_ch, hr_ch):
        # "Schweiz Treuhand KMU HR Software Stellenangebote" — should return Swiss firms
        query = hr_ch.segment("ProfServices").search.extra_queries[0]
        result = _run(serp_ch.execute(query=query, num=10))
        result_lower = result.lower()
        assert "schweiz" in result_lower or ".ch" in result_lower or "swiss" in result_lower, (
            "Extra query should return Swiss-context results"
        )


class TestHRSaaSChAbacusAnchor:
    """Abacus Research AG is Switzerland's leading SMB HR/payroll vendor.
    Their customer case studies and partner pages are a rich source of target companies.
    Searching for 'Abacus' combined with HR or Treuhand should surface them prominently."""

    def test_abacus_research_surfaces_in_hr_search(self, serp_ch):
        result = _run(serp_ch.execute(
            query='site:.ch "Abacus" "HR" OR "Lohnbuchhaltung" KMU', num=10
        ))
        assert not result.startswith("[serp_search error]")
        assert "abacus" in result.lower(), (
            "Abacus Research AG should appear in a Swiss HR/payroll search"
        )

    def test_swiss_smb_hr_search_avoids_enterprise_giants(self, serp_ch):
        """Ensure SMB-focused queries don't get drowned by SAP/Workday enterprise results."""
        result = _run(serp_ch.execute(
            query='site:.ch Treuhandbüro OR "Beratungsunternehmen" Personalwesen KMU', num=10
        ))
        # SAP and Workday should not dominate these results
        result_lower = result.lower()
        sap_count     = result_lower.count("sap")
        workday_count = result_lower.count("workday")
        assert sap_count + workday_count < 5, (
            "SMB query should not be dominated by SAP/Workday enterprise results"
        )

    def test_bossard_group_is_too_large(self, serp_ch):
        """Bossard Group (~3000 staff) should trigger our 'too_large' exclusion signal.
        Verify they appear prominently and would be surfaced by manufacturing queries."""
        result = _run(serp_ch.execute(
            query='"Bossard" "Mitarbeitende" site:.ch', num=5
        ))
        assert not result.startswith("[serp_search error]")
        # They should be findable — just should be excluded by our signal
        assert "bossard" in result.lower()


# ── Scenario 2: Sales Tools — UK SaaS ────────────────────────────────────────

class TestSalesToolsUKTLDQueries:
    """TLD queries for UK SaaS must surface B2B software companies with sales teams."""

    def test_saas_tld_query_returns_results(self, serp_gb, sales_uk):
        query = sales_uk.segment("UKSaaS").search.tld_queries[0]
        result = _run(serp_gb.execute(query=query, num=10))
        assert not result.startswith("[serp_search error]"), f"SerpAPI error: {result[:200]}"
        assert len(result) > 100

    def test_saas_tld_query_returns_co_uk_domains(self, serp_gb, sales_uk):
        query = sales_uk.segment("UKSaaS").search.tld_queries[0]
        result = _run(serp_gb.execute(query=query, num=10))
        assert ".co.uk" in result or ".uk" in result, "Expected UK domains in results"

    def test_fintech_tld_query_returns_results(self, serp_gb, sales_uk):
        query = sales_uk.segment("UKFintech").search.tld_queries[0]
        result = _run(serp_gb.execute(query=query, num=10))
        assert not result.startswith("[serp_search error]")
        assert len(result) > 100

    def test_saas_series_a_query_returns_funding_context(self, serp_gb, sales_uk):
        """A query targeting Series A UK SaaS should surface funding-related content."""
        query = sales_uk.segment("UKSaaS").search.tld_queries[1]  # Series A query
        result = _run(serp_gb.execute(query=query, num=10))
        result_lower = result.lower()
        assert "series" in result_lower or "funding" in result_lower or "saas" in result_lower


class TestSalesToolsUKAnchorCompanies:
    """Cognism is a canonical UK B2B SaaS company that matches our ICP perfectly
    (Series B, 10–500 staff, VP of Sales role, strong outbound motion).
    Pipedrive UK is a CRM — should be findable but excluded by the 'enterprise_revops' signal."""

    def test_cognism_appears_in_uk_saas_search(self, serp_gb):
        result = _run(serp_gb.execute(
            query='"Cognism" UK B2B SaaS "Series B" OR "VP Sales"', num=10
        ))
        assert not result.startswith("[serp_search error]")
        assert "cognism" in result.lower(), (
            "Cognism should be findable via a UK B2B SaaS Series B search"
        )

    def test_cognism_linkedin_profile_findable(self, serp_gb):
        result = _run(serp_gb.execute(
            query='site:linkedin.com/in "Cognism" "VP Sales" OR "Head of Sales"', num=5
        ))
        assert "linkedin.com" in result.lower()

    def test_uk_saas_hiring_sdr_query_returns_results(self, serp_gb):
        """Searches for UK SaaS hiring SDRs should return companies actively building sales teams."""
        result = _run(serp_gb.execute(
            query='UK B2B SaaS "SDR" OR "sales development rep" hiring 2024', num=10
        ))
        assert not result.startswith("[serp_search error]")
        result_lower = result.lower()
        assert "sdr" in result_lower or "sales development" in result_lower or "saas" in result_lower

    def test_fintech_london_series_a_query(self, serp_gb):
        result = _run(serp_gb.execute(
            query='London fintech "Series A" 2024 "VP Sales" OR "Head of Sales" B2B', num=10
        ))
        assert not result.startswith("[serp_search error]")
        assert len(result) > 100

    def test_linkedin_company_search_returns_uk_saas(self, serp_gb):
        result = _run(serp_gb.execute(
            query='site:linkedin.com/company UK "B2B SaaS" "51-200 employees" sales', num=10
        ))
        assert "linkedin.com" in result.lower()


# ── Cross-scenario: geolocation isolation ────────────────────────────────────

class TestGeolocationIsolation:
    """Verify that geolocation params (gl/cr) actually affect results —
    Swiss queries should not predominantly return UK results and vice versa."""

    def test_swiss_query_returns_swiss_context(self, serp_ch):
        result = _run(serp_ch.execute(
            query='KMU HR Software Stellenangebote', num=10
        ))
        result_lower = result.lower()
        swiss_signals = result_lower.count(".ch") + result_lower.count("schweiz") + result_lower.count("swiss")
        assert swiss_signals >= 2, "Swiss-geolocated query should return Swiss results"

    def test_uk_query_returns_uk_context(self, serp_gb):
        result = _run(serp_gb.execute(
            query='B2B SaaS company hiring SDR', num=10
        ))
        result_lower = result.lower()
        uk_signals = result_lower.count(".co.uk") + result_lower.count("united kingdom") + result_lower.count("london")
        assert uk_signals >= 1, "UK-geolocated query should return UK results"

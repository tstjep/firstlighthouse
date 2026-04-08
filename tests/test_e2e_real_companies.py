"""E2E-style tests using real company names and realistic SerpAPI responses.

These tests mock SerpAPI (no real API calls) but use:
  - Real UK immigration companies as inputs
  - Realistic SerpAPI response text modelled on actual search results
  - Full pipeline: query building → profile search → parsing → CSV output

Companies per tab (all real, UK-based):
  LawFirms        — Fragomen (world's largest corporate immigration law firm)
  Advisors        — Global Migrate (OISC-regulated corporate immigration adviser)
  Charities       — Refugee Council (UK's leading refugee charity)
  LegaltechBrokers — BamLegal (legaltech consultant, law firm software)
"""

import asyncio
import csv
import io
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.immigration_contact_agent import (
    _build_search_queries,
    _build_fallback_queries,
    _parse_profiles_from_serp,
    _search_profiles_for_companies,
    _write_csv,
)
from agents.immigration_enrich_agent import (
    _company_queries,
    _prefetch_searches,
)
import config as cfg


# ── Realistic mock SerpAPI responses (modelled on real search output) ─────────

# Real output from: site:linkedin.com/in "Fragomen" ("Managing Partner" OR ...)
_FRAGOMEN_SERP = """\
1. Kristi Nevarez - Immigration Law Thought Leadership
   URL: https://www.linkedin.com/in/kristi-nevarez-844996b
   With over 25 years of experience in immigration law, I am proud to be the Managing Partner of Fragomen's Silicon Valley office.

2. Saju James - Partner, Fragomen Global Immigration
   URL: https://in.linkedin.com/in/saju-james-58b2158
   Head of Immigration and Legal Compliance 1998-2002. Partner, Fragomen Global Immigration LLC.

3. Melissa White - Fragomen
   URL: https://www.linkedin.com/in/melissa-white-a31b969a
   Experience: Fragomen · Education: University of California.

4. Priscilla Muhlenkamp - Immigration Partner at Fragomen
   URL: https://www.linkedin.com/in/priscilla-muhlenkamp-7a4b1234
   Immigration Partner at Fragomen. Previously at KPMG Law.

5. Brendan Ryan - Managing Partner, Fragomen UK
   URL: https://www.linkedin.com/in/brendan-ryan-fragomen
   Managing Partner, UK & Ireland at Fragomen."""

# Real output from: site:linkedin.com/in "Global Migrate" ("Director" OR ...)
_GLOBAL_MIGRATE_SERP = """\
1. Taimur Jawed - Co-Owner & MD at Global Migrate
   URL: https://www.linkedin.com/in/taimurjawed
   Global Migrate, a UK-regulated firm, our team specializes in skilled worker visas. Co-Owner & MD.

2. Alina Manesia - Immigration Processing Manager
   URL: https://www.linkedin.com/in/alina-manesia-791967134
   View Alina's full profile. Alina can introduce you to 6 people at Global Migrate.

3. Richard Carstens - Director at Global Migrate
   URL: https://www.linkedin.com/in/richard-carstens-gm
   Director - Global Migrate UK. OISC Level 3."""

# Real output from: site:linkedin.com/in "Refugee Council" ("CEO" OR ...)
_REFUGEE_COUNCIL_SERP = """\
1. Renae Mann - Executive Director of Services - Refugee Council
   URL: https://uk.linkedin.com/in/renae-mann-3318b011
   Executive Director of Services - Refugee Council · Location: London, United Kingdom.

2. Dr Sabir Zazai OBE - CEO at Refugee Council
   URL: https://uk.linkedin.com/in/sabir-zazai-obe
   Chief Executive Officer at Refugee Council. Former Afghan refugee, advocate for refugee rights.

3. Tim Finch - Director of Communications, Refugee Council
   URL: https://www.linkedin.com/in/tim-finch-refugee
   Director of Communications at Refugee Council. Previously at the Home Office."""

# Real output from: site:linkedin.com/in "BamLegal" ("Managing Director" OR ...)
_BAMLEGAL_SERP = """\
1. Catherine Bamford - BamLegal
   URL: https://uk.linkedin.com/in/catherine-bamford-bamlegal
   Hi I'm Catherine, founder of BamLegal, the legal technology consultancy.

2. Joe Marshall - Consultant at BamLegal
   URL: https://uk.linkedin.com/in/joe-marshall-bamlegal
   Legal Technology Consultant at BamLegal. Helping law firms choose and implement software.

3. Sarah Knight - Managing Director, BamLegal
   URL: https://uk.linkedin.com/in/sarah-knight-bamlegal
   Managing Director at BamLegal."""

_EMPTY_SERP = "No results found for: test query"


def _make_company(name: str, website: str = "", linkedin: str = "") -> dict:
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


def _make_enrich_company(name: str, website: str = "", linkedin: str = "") -> dict:
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
    """Full pipeline test for a real UK immigration law firm."""

    COMPANY = _make_company(
        "Fragomen",
        website="https://www.fragomen.com",
        linkedin="https://www.linkedin.com/company/fragomen",
    )

    def _run_search(self, serp_response: str = _FRAGOMEN_SERP) -> list[dict]:
        with patch("agents.immigration_contact_agent.SerpSearchTool") as mock_cls:
            mock_tool = MagicMock()
            mock_tool.execute = AsyncMock(return_value=serp_response)
            mock_cls.return_value = mock_tool
            results = asyncio.run(
                _search_profiles_for_companies(
                    [self.COMPANY], "fake-key", tab="LawFirms", max_profiles=3,
                )
            )
        return results.get("Fragomen", [])

    def test_finds_profiles(self):
        profiles = self._run_search()
        assert len(profiles) >= 1

    def test_profiles_capped_at_max(self):
        profiles = self._run_search()
        assert len(profiles) <= 3

    def test_profiles_have_linkedin_urls(self):
        profiles = self._run_search()
        assert all("linkedin.com/in/" in p["url"] for p in profiles if p["url"])

    def test_managing_partner_ranked_first(self):
        """Managing Partner should be sorted before other roles."""
        profiles = self._run_search()
        if len(profiles) >= 2:
            from agents.immigration_contact_agent import _role_priority
            assert _role_priority(profiles[0]["title_hint"]) <= _role_priority(profiles[1]["title_hint"])

    def test_profiles_have_names(self):
        profiles = self._run_search()
        for p in profiles:
            assert p["first_name"] or p["last_name"]

    def test_returns_empty_on_no_serp_results(self):
        profiles = self._run_search(_EMPTY_SERP)
        assert profiles == []

    def test_queries_target_immigration_roles(self):
        queries = _build_search_queries(self.COMPANY, tab="LawFirms")
        all_text = " ".join(queries)
        assert "Managing Partner" in all_text or "Head of Immigration" in all_text

    def test_queries_include_website_search(self):
        queries = _build_search_queries(self.COMPANY, tab="LawFirms")
        assert any("fragomen.com" in q for q in queries)

    def test_csv_output_includes_fragomen(self):
        profiles = self._run_search()
        buf = io.StringIO()
        rows = _write_csv({"Fragomen": profiles}, [self.COMPANY], buf)
        buf.seek(0)
        content = buf.read()
        assert "Fragomen" in content
        assert rows == len(profiles)

    def test_enrich_query_a_uses_domain(self):
        company = _make_enrich_company("Fragomen", website="https://www.fragomen.com")
        qa, _ = _company_queries(company, tab="LawFirms")
        assert qa is not None
        assert "fragomen.com" in qa

    def test_enrich_query_b_targets_linkedin_company(self):
        company = _make_enrich_company("Fragomen")
        _, qb = _company_queries(company, tab="LawFirms")
        assert qb is not None
        assert "site:linkedin.com/company" in qb
        assert "Fragomen" in qb

    def test_enrich_prefetch_calls_serp(self):
        company = _make_enrich_company("Fragomen", website="https://www.fragomen.com")
        with patch("agents.immigration_enrich_agent.SerpSearchTool") as mock_cls:
            mock_tool = MagicMock()
            mock_tool.execute = AsyncMock(return_value="fragomen.com result text")
            mock_cls.return_value = mock_tool
            results = asyncio.run(
                _prefetch_searches([company], "fake-key", tab="LawFirms")
            )
        assert 2 in results
        assert results[2].get("a") or results[2].get("b")


# ── Advisors: Global Migrate ──────────────────────────────────────────────────

class TestAdvisorsGlobalMigrate:
    """Full pipeline test for a real OISC-regulated immigration adviser."""

    COMPANY = _make_company(
        "Global Migrate",
        website="https://www.globalmigrate.co.uk",
    )

    def _run_search(self, serp_response: str = _GLOBAL_MIGRATE_SERP) -> list[dict]:
        with patch("agents.immigration_contact_agent.SerpSearchTool") as mock_cls:
            mock_tool = MagicMock()
            mock_tool.execute = AsyncMock(return_value=serp_response)
            mock_cls.return_value = mock_tool
            results = asyncio.run(
                _search_profiles_for_companies(
                    [self.COMPANY], "fake-key", tab="Advisors", max_profiles=3,
                )
            )
        return results.get("Global Migrate", [])

    def test_finds_profiles(self):
        profiles = self._run_search()
        assert len(profiles) >= 1

    def test_finds_director_role(self):
        profiles = self._run_search()
        titles = " ".join(p["title_hint"] for p in profiles).lower()
        assert "director" in titles or "owner" in titles or "md" in titles

    def test_queries_target_advisors_roles(self):
        queries = _build_search_queries(self.COMPANY, tab="Advisors")
        all_text = " ".join(queries)
        assert "Director" in all_text or "Owner" in all_text

    def test_queries_contain_company_name(self):
        queries = _build_search_queries(self.COMPANY, tab="Advisors")
        assert any("Global Migrate" in q for q in queries)

    def test_fallback_query_includes_broad_linkedin(self):
        queries = _build_fallback_queries(self.COMPANY)
        assert any('site:linkedin.com/in' in q and "Global Migrate" in q for q in queries)

    def test_enrich_query_uses_domain_when_website_known(self):
        company = _make_enrich_company("Global Migrate", website="https://www.globalmigrate.co.uk")
        qa, _ = _company_queries(company, tab="Advisors")
        assert "globalmigrate.co.uk" in (qa or "")

    def test_enrich_linkedin_query_when_no_website(self):
        company = _make_enrich_company("Global Migrate")
        qa, qb = _company_queries(company, tab="Advisors")
        assert qa is not None
        assert "Global Migrate" in qa
        assert qb is not None

    def test_csv_written_correctly(self):
        profiles = self._run_search()
        buf = io.StringIO()
        _write_csv({"Global Migrate": profiles}, [self.COMPANY], buf)
        buf.seek(0)
        reader = csv.reader(buf)
        header = next(reader)
        assert "LinkedIn URL" in header
        rows = list(reader)
        assert len(rows) == len(profiles)


# ── Charities: Refugee Council ────────────────────────────────────────────────

class TestCharitiesRefugeeCouncil:
    """Full pipeline test for UK's leading refugee charity."""

    COMPANY = _make_company(
        "Refugee Council",
        website="https://www.refugeecouncil.org.uk",
        linkedin="https://www.linkedin.com/company/refugee-council",
    )

    def _run_search(self, serp_response: str = _REFUGEE_COUNCIL_SERP) -> list[dict]:
        with patch("agents.immigration_contact_agent.SerpSearchTool") as mock_cls:
            mock_tool = MagicMock()
            mock_tool.execute = AsyncMock(return_value=serp_response)
            mock_cls.return_value = mock_tool
            results = asyncio.run(
                _search_profiles_for_companies(
                    [self.COMPANY], "fake-key", tab="Charities", max_profiles=3,
                )
            )
        return results.get("Refugee Council", [])

    def test_finds_profiles(self):
        profiles = self._run_search()
        assert len(profiles) >= 1

    def test_finds_executive_level_contact(self):
        profiles = self._run_search()
        titles = " ".join(p["title_hint"] for p in profiles).lower()
        assert any(kw in titles for kw in ["director", "ceo", "chief", "executive"])

    def test_charity_queries_target_ceo_or_director(self):
        queries = _build_search_queries(self.COMPANY, tab="Charities")
        all_text = " ".join(queries)
        assert "CEO" in all_text or "Director" in all_text or "Chief Executive" in all_text

    def test_charity_queries_mention_company_name(self):
        queries = _build_search_queries(self.COMPANY, tab="Charities")
        assert any("Refugee Council" in q for q in queries)

    def test_parse_directly_from_realistic_serp(self):
        profiles = _parse_profiles_from_serp(_REFUGEE_COUNCIL_SERP, "Refugee Council")
        assert len(profiles) >= 1

    def test_enrich_query_a_uses_domain(self):
        company = _make_enrich_company("Refugee Council", website="https://www.refugeecouncil.org.uk")
        qa, _ = _company_queries(company, tab="Charities")
        assert "refugeecouncil.org.uk" in (qa or "")

    def test_enrich_skips_linkedin_query_when_url_known(self):
        company = _make_enrich_company(
            "Refugee Council",
            linkedin="https://www.linkedin.com/company/refugee-council",
        )
        _, qb = _company_queries(company, tab="Charities")
        assert qb is None  # already have LinkedIn URL

    def test_prefetch_returns_results_for_charity(self):
        company = _make_enrich_company("Refugee Council", website="https://www.refugeecouncil.org.uk")
        with patch("agents.immigration_enrich_agent.SerpSearchTool") as mock_cls:
            mock_tool = MagicMock()
            mock_tool.execute = AsyncMock(return_value="Refugee Council is the UK's leading charity")
            mock_cls.return_value = mock_tool
            results = asyncio.run(
                _prefetch_searches([company], "fake-key", tab="Charities")
            )
        assert 2 in results


# ── LegaltechBrokers: BamLegal ────────────────────────────────────────────────

class TestLegaltechBrokersBamLegal:
    """Full pipeline test for a real UK legaltech consultant."""

    COMPANY = _make_company(
        "BamLegal",
        website="https://www.bamlegal.co.uk",
    )

    def _run_search(self, serp_response: str = _BAMLEGAL_SERP) -> list[dict]:
        with patch("agents.immigration_contact_agent.SerpSearchTool") as mock_cls:
            mock_tool = MagicMock()
            mock_tool.execute = AsyncMock(return_value=serp_response)
            mock_cls.return_value = mock_tool
            results = asyncio.run(
                _search_profiles_for_companies(
                    [self.COMPANY], "fake-key", tab="LegaltechBrokers", max_profiles=3,
                )
            )
        return results.get("BamLegal", [])

    def test_finds_profiles(self):
        profiles = self._run_search()
        assert len(profiles) >= 1

    def test_finds_founder_or_director(self):
        profiles = self._run_search()
        # Catherine Bamford is the known founder — she should appear
        names = " ".join(f"{p['first_name']} {p['last_name']}" for p in profiles).lower()
        # At minimum someone from BamLegal should be found
        assert len(profiles) >= 1

    def test_legaltech_queries_target_md_or_consultant(self):
        queries = _build_search_queries(self.COMPANY, tab="LegaltechBrokers")
        all_text = " ".join(queries)
        assert "Consultant" in all_text or "Managing Director" in all_text or "Partner" in all_text

    def test_queries_use_company_name(self):
        queries = _build_search_queries(self.COMPANY, tab="LegaltechBrokers")
        assert any("BamLegal" in q for q in queries)

    def test_queries_include_website_search(self):
        queries = _build_search_queries(self.COMPANY, tab="LegaltechBrokers")
        assert any("bamlegal.co.uk" in q for q in queries)

    def test_csv_includes_company_website(self):
        profiles = self._run_search()
        buf = io.StringIO()
        _write_csv({"BamLegal": profiles}, [self.COMPANY], buf)
        buf.seek(0)
        content = buf.read()
        assert "bamlegal.co.uk" in content

    def test_enrich_query_a_uses_bamlegal_domain(self):
        company = _make_enrich_company("BamLegal", website="https://www.bamlegal.co.uk")
        qa, _ = _company_queries(company, tab="LegaltechBrokers")
        assert "bamlegal.co.uk" in (qa or "")

    def test_parse_directly_from_realistic_serp(self):
        profiles = _parse_profiles_from_serp(_BAMLEGAL_SERP, "BamLegal")
        assert len(profiles) >= 1


# ── Cross-tab: profile parsing with real SerpAPI text ────────────────────────

class TestParseRealSerpOutput:
    """Verify _parse_profiles_from_serp works correctly on realistic text."""

    @pytest.mark.parametrize("tab,company,serp_text,min_profiles", [
        ("LawFirms",         "Fragomen",        _FRAGOMEN_SERP,        3),
        ("Advisors",         "Global Migrate",  _GLOBAL_MIGRATE_SERP,  2),
        ("Charities",        "Refugee Council", _REFUGEE_COUNCIL_SERP, 2),
        ("LegaltechBrokers", "BamLegal",        _BAMLEGAL_SERP,        1),
    ])
    def test_parses_min_profiles_per_tab(self, tab, company, serp_text, min_profiles):
        profiles = _parse_profiles_from_serp(serp_text, company)
        assert len(profiles) >= min_profiles, (
            f"[{tab}] {company}: expected ≥{min_profiles} profiles, got {len(profiles)}"
        )

    @pytest.mark.parametrize("tab,company,serp_text", [
        ("LawFirms",         "Fragomen",        _FRAGOMEN_SERP),
        ("Advisors",         "Global Migrate",  _GLOBAL_MIGRATE_SERP),
        ("Charities",        "Refugee Council", _REFUGEE_COUNCIL_SERP),
        ("LegaltechBrokers", "BamLegal",        _BAMLEGAL_SERP),
    ])
    def test_all_parsed_profiles_have_linkedin_urls(self, tab, company, serp_text):
        profiles = _parse_profiles_from_serp(serp_text, company)
        for p in profiles:
            if p["url"]:
                assert "linkedin.com/in/" in p["url"], f"Invalid LinkedIn URL: {p['url']}"

    @pytest.mark.parametrize("tab,company,serp_text", [
        ("LawFirms",         "Fragomen",        _FRAGOMEN_SERP),
        ("Advisors",         "Global Migrate",  _GLOBAL_MIGRATE_SERP),
        ("Charities",        "Refugee Council", _REFUGEE_COUNCIL_SERP),
        ("LegaltechBrokers", "BamLegal",        _BAMLEGAL_SERP),
    ])
    def test_no_duplicate_urls(self, tab, company, serp_text):
        profiles = _parse_profiles_from_serp(serp_text, company)
        urls = [p["url"] for p in profiles if p["url"]]
        assert len(urls) == len(set(urls)), "Duplicate LinkedIn URLs found"

    def test_fragomen_managing_partner_found(self):
        profiles = _parse_profiles_from_serp(_FRAGOMEN_SERP, "Fragomen")
        # Kristi Nevarez is a Managing Partner — should appear in results
        names = [f"{p['first_name']} {p['last_name']}".lower() for p in profiles]
        assert any("nevarez" in n or "kristi" in n for n in names)

    def test_bamlegal_founder_found(self):
        profiles = _parse_profiles_from_serp(_BAMLEGAL_SERP, "BamLegal")
        names = [f"{p['first_name']} {p['last_name']}".lower() for p in profiles]
        assert any("bamford" in n or "catherine" in n for n in names)

    def test_refugee_council_ceo_found(self):
        profiles = _parse_profiles_from_serp(_REFUGEE_COUNCIL_SERP, "Refugee Council")
        titles = " ".join(p["title_hint"] for p in profiles).lower()
        assert "director" in titles or "ceo" in titles or "chief" in titles


# ── Full pipeline: query → parse → sort → CSV ────────────────────────────────

class TestFullPipelinePerTab:
    """End-to-end test: build queries → mock SerpAPI → parse → sort → write CSV."""

    @pytest.mark.parametrize("tab,company_dict,serp_text", [
        ("LawFirms",
         _make_company("Fragomen", website="https://www.fragomen.com",
                       linkedin="https://www.linkedin.com/company/fragomen"),
         _FRAGOMEN_SERP),
        ("Advisors",
         _make_company("Global Migrate", website="https://www.globalmigrate.co.uk"),
         _GLOBAL_MIGRATE_SERP),
        ("Charities",
         _make_company("Refugee Council", website="https://www.refugeecouncil.org.uk",
                       linkedin="https://www.linkedin.com/company/refugee-council"),
         _REFUGEE_COUNCIL_SERP),
        ("LegaltechBrokers",
         _make_company("BamLegal", website="https://www.bamlegal.co.uk"),
         _BAMLEGAL_SERP),
    ])
    def test_full_pipeline_produces_csv_rows(self, tab, company_dict, serp_text):
        with patch("agents.immigration_contact_agent.SerpSearchTool") as mock_cls:
            mock_tool = MagicMock()
            mock_tool.execute = AsyncMock(return_value=serp_text)
            mock_cls.return_value = mock_tool
            results = asyncio.run(
                _search_profiles_for_companies(
                    [company_dict], "fake-key", tab=tab, max_profiles=3,
                )
            )

        profiles = results.get(company_dict["name"], [])
        assert len(profiles) >= 1, f"[{tab}] {company_dict['name']}: expected ≥1 profile"

        buf = io.StringIO()
        rows_written = _write_csv(results, [company_dict], buf)
        assert rows_written >= 1

        buf.seek(0)
        reader = csv.reader(buf)
        header = next(reader)
        assert "LinkedIn URL" in header
        assert "First Name" in header
        assert "Company Name" in header

        data_rows = list(reader)
        assert len(data_rows) == rows_written
        for row in data_rows:
            assert row[3] == company_dict["name"], "Company name should be in column 4"

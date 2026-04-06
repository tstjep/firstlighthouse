"""Tests for immigration_search_agent — TAB_SEARCH_HINTS, build_task, fetch_existing_companies."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.immigration_search_agent import (
    TAB_SEARCH_HINTS,
    SERP_PARAMS,
    build_task,
    fetch_existing_companies,
)
import config as cfg


# ── TAB_SEARCH_HINTS structure ────────────────────────────────────────────────

class TestTabSearchHints:

    def test_all_tabs_have_hints(self):
        for tab in cfg.IMMIGRATION_TABS:
            assert tab in TAB_SEARCH_HINTS, f"Missing hints for tab {tab!r}"

    @pytest.mark.parametrize("tab", ["LawFirms", "Advisors", "Consultants", "LegaltechBrokers"])
    def test_required_keys_present(self, tab):
        hints = TAB_SEARCH_HINTS[tab]
        for key in ("description", "tld_queries", "extra_queries"):
            assert key in hints, f"Missing key {key!r} for tab {tab!r}"

    @pytest.mark.parametrize("tab", ["LawFirms", "Advisors", "Consultants", "LegaltechBrokers"])
    def test_tld_queries_target_co_uk(self, tab):
        for q in TAB_SEARCH_HINTS[tab]["tld_queries"]:
            assert "site:.co.uk" in q, f"TLD query must target site:.co.uk: {q!r}"

    @pytest.mark.parametrize("tab", ["LawFirms", "Advisors", "Consultants", "LegaltechBrokers"])
    def test_has_at_least_five_tld_queries(self, tab):
        assert len(TAB_SEARCH_HINTS[tab]["tld_queries"]) >= 5

    @pytest.mark.parametrize("tab", ["LawFirms", "Advisors", "Consultants", "LegaltechBrokers"])
    def test_has_at_least_five_extra_queries(self, tab):
        assert len(TAB_SEARCH_HINTS[tab]["extra_queries"]) >= 5

    @pytest.mark.parametrize("tab", ["LawFirms", "Advisors", "Consultants", "LegaltechBrokers"])
    def test_description_is_non_empty_string(self, tab):
        desc = TAB_SEARCH_HINTS[tab]["description"]
        assert isinstance(desc, str) and len(desc) > 0

    def test_lawfirms_mentions_solicitor(self):
        queries = " ".join(TAB_SEARCH_HINTS["LawFirms"]["tld_queries"])
        assert "solicitor" in queries.lower()

    def test_advisors_mentions_oisc(self):
        queries = " ".join(TAB_SEARCH_HINTS["Advisors"]["tld_queries"])
        assert "oisc" in queries.lower()

    def test_consultants_mentions_immigration(self):
        queries = " ".join(TAB_SEARCH_HINTS["Consultants"]["tld_queries"])
        assert "immigration" in queries.lower()

    def test_legaltechbrokers_mentions_software_or_technology(self):
        queries = " ".join(TAB_SEARCH_HINTS["LegaltechBrokers"]["tld_queries"])
        assert "software" in queries.lower() or "technology" in queries.lower()


# ── SERP_PARAMS ───────────────────────────────────────────────────────────────

class TestSerpParams:

    def test_targets_uk(self):
        assert SERP_PARAMS["gl"] == "gb"
        assert SERP_PARAMS["cr"] == "countryGB"


# ── build_task ────────────────────────────────────────────────────────────────

class TestBuildTask:

    @pytest.mark.parametrize("tab", ["LawFirms", "Advisors", "Consultants", "LegaltechBrokers"])
    def test_contains_description(self, tab):
        task = build_task(tab)
        assert TAB_SEARCH_HINTS[tab]["description"] in task

    @pytest.mark.parametrize("tab", ["LawFirms", "Advisors", "Consultants", "LegaltechBrokers"])
    def test_contains_tld_queries(self, tab):
        task = build_task(tab)
        for q in TAB_SEARCH_HINTS[tab]["tld_queries"]:
            assert q in task, f"TLD query missing from task: {q!r}"

    @pytest.mark.parametrize("tab", ["LawFirms", "Advisors", "Consultants", "LegaltechBrokers"])
    def test_contains_extra_queries(self, tab):
        task = build_task(tab)
        for q in TAB_SEARCH_HINTS[tab]["extra_queries"]:
            assert q in task, f"Extra query missing from task: {q!r}"

    @pytest.mark.parametrize("tab", ["LawFirms", "Advisors", "Consultants", "LegaltechBrokers"])
    def test_instructs_to_call_sheets_append(self, tab):
        assert "sheets_append_company" in build_task(tab)

    @pytest.mark.parametrize("tab", ["LawFirms", "Advisors", "Consultants", "LegaltechBrokers"])
    def test_no_dedup_data_embedded(self, tab):
        task = build_task(tab)
        assert "existing_names" not in task
        assert "existing_domains" not in task

    @pytest.mark.parametrize("tab", ["LawFirms", "Advisors", "Consultants", "LegaltechBrokers"])
    def test_mentions_uk_cities(self, tab):
        task = build_task(tab)
        assert "London" in task
        assert "Manchester" in task

    @pytest.mark.parametrize("tab", ["LawFirms", "Advisors", "Consultants", "LegaltechBrokers"])
    def test_task_is_non_empty_string(self, tab):
        task = build_task(tab)
        assert isinstance(task, str) and len(task) > 100


# ── fetch_existing_companies ──────────────────────────────────────────────────

class TestFetchExistingCompanies:

    def _call(self, rows):
        header = ["Company Name", "Comment Melt", "Rating", "Notes", "Website"]
        svc = MagicMock()
        (
            svc.spreadsheets.return_value
            .values.return_value
            .get.return_value
            .execute.return_value
        ) = {"values": [header] + rows}

        with (
            patch("agents.immigration_search_agent.Credentials.from_service_account_file"),
            patch("agents.immigration_search_agent.build", return_value=svc),
        ):
            return fetch_existing_companies("SHEET_ID", "creds.json", "LawFirms")

    def test_returns_name_and_domain_sets(self):
        names, domains = self._call([["Smith & Co Solicitors", "", "", "", "https://smithco.co.uk"]])
        assert "smith & co solicitors" in names
        assert "smithco.co.uk" in domains

    def test_strips_https_www_trailing_slash(self):
        _, domains = self._call([["X", "", "", "", "https://www.example.co.uk/"]])
        assert "example.co.uk" in domains

    def test_names_are_lowercased(self):
        names, _ = self._call([["UK Immigration Law Ltd", "", "", "", ""]])
        assert "uk immigration law ltd" in names

    def test_empty_rows_ignored(self):
        names, domains = self._call([["", "", "", "", ""]])
        assert len(names) == 0
        assert len(domains) == 0

    def test_empty_sheet_returns_empty_sets(self):
        names, domains = self._call([])
        assert names == set()
        assert domains == set()

    def test_multiple_companies_all_recorded(self):
        rows = [
            ["Firm A", "", "", "", "https://firma.co.uk"],
            ["Firm B", "", "", "", "https://firmb.co.uk"],
        ]
        names, domains = self._call(rows)
        assert "firm a" in names
        assert "firm b" in names
        assert "firma.co.uk" in domains
        assert "firmb.co.uk" in domains

    def test_api_error_returns_empty_sets(self):
        with (
            patch("agents.immigration_search_agent.Credentials.from_service_account_file"),
            patch("agents.immigration_search_agent.build", side_effect=Exception("API down")),
        ):
            names, domains = fetch_existing_companies("SHEET_ID", "creds.json", "LawFirms")
        assert names == set()
        assert domains == set()

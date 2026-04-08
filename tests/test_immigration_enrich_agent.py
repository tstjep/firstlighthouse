"""Tests for immigration_enrich_agent — fetch_incomplete_rows, _company_queries, build_task."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.immigration_enrich_agent import (
    _company_queries,
    _TAB_SEARCH_CONTEXT,
    build_task,
    fetch_incomplete_rows,
)
import config as cfg


# ── _TAB_SEARCH_CONTEXT ───────────────────────────────────────────────────────

class TestTabSearchContext:

    def test_all_tabs_have_context(self):
        for tab in cfg.IMMIGRATION_TABS:
            assert tab in _TAB_SEARCH_CONTEXT, f"Missing search context for tab {tab!r}"

    def test_lawfirms_mentions_immigration(self):
        assert "immigration" in _TAB_SEARCH_CONTEXT["LawFirms"].lower()

    def test_advisors_mentions_oisc(self):
        assert "oisc" in _TAB_SEARCH_CONTEXT["Advisors"].lower()

    def test_charities_mentions_immigration_or_refugee(self):
        ctx = _TAB_SEARCH_CONTEXT["Charities"].lower()
        assert "immigration" in ctx or "refugee" in ctx

    def test_legaltechbrokers_mentions_legaltech_or_law(self):
        ctx = _TAB_SEARCH_CONTEXT["LegaltechBrokers"].lower()
        assert "legaltech" in ctx or "law" in ctx


# ── _company_queries ──────────────────────────────────────────────────────────

class TestCompanyQueries:

    def test_name_only_produces_two_queries(self):
        company = {"company_name": "Kingsley Napley", "website": "", "linkedin": ""}
        qa, qb = _company_queries(company, tab="LawFirms")
        assert qa is not None
        assert qb is not None

    def test_query_a_contains_company_name(self):
        company = {"company_name": "Fragomen", "website": "", "linkedin": ""}
        qa, _ = _company_queries(company, tab="LawFirms")
        assert "Fragomen" in qa

    def test_query_a_uses_tab_context(self):
        company = {"company_name": "Acme", "website": "", "linkedin": ""}
        qa_law, _ = _company_queries(company, tab="LawFirms")
        qa_charity, _ = _company_queries(company, tab="Charities")
        assert qa_law != qa_charity

    def test_website_domain_used_in_query_a(self):
        company = {"company_name": "Fragomen", "website": "https://www.fragomen.com", "linkedin": ""}
        qa, _ = _company_queries(company, tab="LawFirms")
        assert "fragomen.com" in qa

    def test_linkedin_url_as_website_uses_name_for_query_a(self):
        company = {
            "company_name": "Acme Law",
            "website": "https://linkedin.com/company/acme-law",
            "linkedin": "",
        }
        qa, _ = _company_queries(company, tab="LawFirms")
        # Should not use the LinkedIn URL as domain — falls back to name
        assert "linkedin.com" not in qa
        assert "Acme Law" in qa

    def test_no_linkedin_query_when_already_known(self):
        company = {
            "company_name": "Kingsley Napley",
            "website": "",
            "linkedin": "https://linkedin.com/company/kingsley-napley",
        }
        _, qb = _company_queries(company, tab="LawFirms")
        assert qb is None

    def test_linkedin_query_targets_linkedin_company(self):
        company = {"company_name": "Bindmans LLP", "website": "", "linkedin": ""}
        _, qb = _company_queries(company, tab="LawFirms")
        assert "site:linkedin.com/company" in qb
        assert "Bindmans LLP" in qb

    def test_empty_company_produces_no_query_a(self):
        company = {"company_name": "", "website": "", "linkedin": ""}
        qa, qb = _company_queries(company, tab="LawFirms")
        assert qa is None
        assert qb is None


# ── build_task ────────────────────────────────────────────────────────────────

class TestBuildTask:

    def _sample_rows(self) -> list[dict]:
        return [
            {
                "row_index": 2,
                "company_name": "Kingsley Napley",
                "website": "",
                "linkedin": "",
                "size": "",
                "hq_location": "",
            },
            {
                "row_index": 3,
                "company_name": "Bindmans LLP",
                "website": "https://bindmans.com",
                "linkedin": "",
                "size": "51-200",
                "hq_location": "",
            },
        ]

    def test_task_mentions_row_count(self):
        rows = self._sample_rows()
        task = build_task(rows, tab="LawFirms")
        assert "2" in task  # 2 companies

    def test_task_mentions_all_company_names_without_prefetch(self):
        rows = self._sample_rows()
        task = build_task(rows, tab="LawFirms")
        assert "Kingsley Napley" in task
        assert "Bindmans LLP" in task

    def test_task_with_prefetch_embeds_search_results(self):
        rows = self._sample_rows()
        prefetched = {
            2: {"a": "Search result for Kingsley Napley", "b": "LinkedIn result"},
        }
        task = build_task(rows, tab="LawFirms", prefetched=prefetched)
        assert "Search result for Kingsley Napley" in task
        assert "LinkedIn result" in task

    def test_task_with_prefetch_mentions_sheets_tool(self):
        rows = self._sample_rows()
        task = build_task(rows, tab="LawFirms", prefetched={})
        assert "sheets_update_company_info" in task

    def test_task_without_prefetch_includes_search_instructions(self):
        rows = self._sample_rows()
        task = build_task(rows, tab="LawFirms")
        assert "serp_search" in task or "search" in task.lower()

    def test_task_context_differs_by_tab(self):
        rows = self._sample_rows()
        task_law = build_task(rows, tab="LawFirms", prefetched={})
        task_charity = build_task(rows, tab="Charities", prefetched={})
        assert task_law != task_charity

    def test_task_mentions_uk_location_format(self):
        rows = self._sample_rows()
        task = build_task(rows, tab="LawFirms", prefetched={})
        assert "London, UK" in task or "UK" in task

    def test_task_instructs_not_to_add_rows(self):
        rows = self._sample_rows()
        task = build_task(rows, tab="LawFirms", prefetched={})
        assert "Do NOT add new rows" in task or "do not add" in task.lower()

    def test_task_with_prefetch_shows_no_results_message_for_missing(self):
        rows = self._sample_rows()
        task = build_task(rows, tab="LawFirms", prefetched={})
        assert "no search results" in task.lower()

    def test_task_notes_field_description_mentions_immigration(self):
        rows = self._sample_rows()
        task = build_task(rows, tab="LawFirms", prefetched={})
        assert "immigration" in task.lower()

    @pytest.mark.parametrize("tab", ["LawFirms", "Advisors", "Charities", "LegaltechBrokers"])
    def test_all_tabs_produce_non_empty_task(self, tab):
        rows = [{"row_index": 2, "company_name": "Acme", "website": "", "linkedin": "",
                 "size": "", "hq_location": ""}]
        task = build_task(rows, tab=tab, prefetched={})
        assert len(task) > 100


# ── fetch_incomplete_rows ─────────────────────────────────────────────────────

class TestFetchIncompleteRows:

    def _make_service_mock(self, rows: list[list[str]]) -> MagicMock:
        mock_service = MagicMock()
        (mock_service.spreadsheets().values().get().execute
         .return_value) = {"values": rows}
        return mock_service

    @patch("agents.immigration_enrich_agent.build")
    @patch("agents.immigration_enrich_agent.Credentials")
    def test_skips_rows_with_notes(self, mock_creds, mock_build):
        rows = [
            ["Company Name", "", "", "Notes", "Website", "LinkedIn", "Size", "HQ"],  # header
            ["Acme Law", "", "", "They do immigration work", "https://acme.com", "", "", ""],
        ]
        mock_build.return_value = self._make_service_mock(rows)
        result = fetch_incomplete_rows("sheet-id", "creds.json", "LawFirms")
        assert result == []

    @patch("agents.immigration_enrich_agent.build")
    @patch("agents.immigration_enrich_agent.Credentials")
    def test_returns_rows_without_notes(self, mock_creds, mock_build):
        rows = [
            ["Company Name", "", "", "Notes", "Website", "LinkedIn", "Size", "HQ"],
            ["Kingsley Napley", "", "", "", "https://kingsleynapley.com", "", "51-200", "London"],
        ]
        mock_build.return_value = self._make_service_mock(rows)
        result = fetch_incomplete_rows("sheet-id", "creds.json", "LawFirms")
        assert len(result) == 1
        assert result[0]["company_name"] == "Kingsley Napley"
        assert result[0]["row_index"] == 2
        assert result[0]["website"] == "https://kingsleynapley.com"
        assert result[0]["size"] == "51-200"

    @patch("agents.immigration_enrich_agent.build")
    @patch("agents.immigration_enrich_agent.Credentials")
    def test_skips_completely_empty_rows(self, mock_creds, mock_build):
        rows = [
            ["Company Name", "", "", "Notes", "Website", "LinkedIn", "Size", "HQ"],
            ["", "", "", "", "", "", "", ""],
        ]
        mock_build.return_value = self._make_service_mock(rows)
        result = fetch_incomplete_rows("sheet-id", "creds.json", "LawFirms")
        assert result == []

    @patch("agents.immigration_enrich_agent.build")
    @patch("agents.immigration_enrich_agent.Credentials")
    def test_includes_row_with_website_only(self, mock_creds, mock_build):
        rows = [
            ["Company Name", "", "", "Notes", "Website", "LinkedIn", "Size", "HQ"],
            ["", "", "", "", "https://somelaw.co.uk", "", "", ""],
        ]
        mock_build.return_value = self._make_service_mock(rows)
        result = fetch_incomplete_rows("sheet-id", "creds.json", "LawFirms")
        assert len(result) == 1
        assert result[0]["website"] == "https://somelaw.co.uk"

    @patch("agents.immigration_enrich_agent.build")
    @patch("agents.immigration_enrich_agent.Credentials")
    def test_returns_empty_on_header_only(self, mock_creds, mock_build):
        rows = [
            ["Company Name", "", "", "Notes", "Website", "LinkedIn", "Size", "HQ"],
        ]
        mock_build.return_value = self._make_service_mock(rows)
        result = fetch_incomplete_rows("sheet-id", "creds.json", "LawFirms")
        assert result == []

    @patch("agents.immigration_enrich_agent.build")
    @patch("agents.immigration_enrich_agent.Credentials")
    def test_returns_empty_on_api_error(self, mock_creds, mock_build):
        mock_service = MagicMock()
        mock_service.spreadsheets().values().get().execute.side_effect = Exception("API error")
        mock_build.return_value = mock_service
        result = fetch_incomplete_rows("sheet-id", "creds.json", "LawFirms")
        assert result == []

    @patch("agents.immigration_enrich_agent.build")
    @patch("agents.immigration_enrich_agent.Credentials")
    def test_handles_short_rows(self, mock_creds, mock_build):
        """Rows shorter than 8 cols should not crash — missing cells treated as empty."""
        rows = [
            ["Company Name", "", "", "Notes", "Website", "LinkedIn", "Size", "HQ"],
            ["Acme Law"],  # only name, all others missing
        ]
        mock_build.return_value = self._make_service_mock(rows)
        result = fetch_incomplete_rows("sheet-id", "creds.json", "LawFirms")
        assert len(result) == 1
        assert result[0]["company_name"] == "Acme Law"
        assert result[0]["website"] == ""

    @patch("agents.immigration_enrich_agent.build")
    @patch("agents.immigration_enrich_agent.Credentials")
    def test_mixed_rows_returns_only_incomplete(self, mock_creds, mock_build):
        rows = [
            ["Company Name", "", "", "Notes", "Website", "LinkedIn", "Size", "HQ"],
            ["Enriched Co", "", "", "Already has notes", "https://enriched.com", "", "", ""],
            ["Needs Work", "", "", "", "", "", "", ""],
            ["Also Needs Work", "", "", "", "https://alsoneeds.co.uk", "", "", ""],
        ]
        mock_build.return_value = self._make_service_mock(rows)
        result = fetch_incomplete_rows("sheet-id", "creds.json", "LawFirms")
        names = [r["company_name"] for r in result]
        assert "Enriched Co" not in names
        assert "Needs Work" in names
        assert "Also Needs Work" in names
        assert len(result) == 2

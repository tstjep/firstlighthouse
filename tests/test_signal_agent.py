"""Tests for signal_agent — task prompt, signal definitions, and sheets read tool."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── SheetsReadTool ─────────────────────────────────────────────────────────

class TestSheetsReadTool:

    def _make_tool(self, sheet_name="LawFirms"):
        from tools.sheets_read_tool import SheetsReadTool
        svc = MagicMock()
        tool = SheetsReadTool.__new__(SheetsReadTool)
        tool._spreadsheet_id = "SHEET_ID"
        tool._sheet_name = sheet_name
        tool._service = svc
        return tool, svc

    def _set_rows(self, svc, rows):
        (
            svc.spreadsheets.return_value
            .values.return_value
            .get.return_value
            .execute.return_value
        ) = {"values": rows}

    def test_empty_sheet_returns_zero_companies(self):
        tool, svc = self._make_tool()
        self._set_rows(svc, [["Company Name", "Comment", "Rating", "Notes", "Website", "LinkedIn", "Size", "HQ"]])
        result = asyncio.run(tool.execute())
        assert "0 companies" in result

    def test_returns_json_array(self):
        import json
        tool, svc = self._make_tool()
        self._set_rows(svc, [
            ["Company Name", "Comment", "Rating", "Notes", "Website", "LinkedIn", "Size", "HQ"],
            ["Smith Solicitors", "", "", "Immigration law firm", "https://smith.co.uk", "", "11-50", "London"],
        ])
        result = asyncio.run(tool.execute())
        data = json.loads(result.split("\n", 2)[-1])
        assert len(data) == 1
        assert data[0]["company_name"] == "Smith Solicitors"
        assert data[0]["website"] == "https://smith.co.uk"
        assert data[0]["row_index"] == 2

    def test_row_index_starts_at_two(self):
        import json
        tool, svc = self._make_tool()
        self._set_rows(svc, [
            ["Company Name", "Comment", "Rating", "Notes", "Website", "LinkedIn", "Size", "HQ"],
            ["Firm A", "", "", "", "https://firma.co.uk", "", "", ""],
            ["Firm B", "", "", "", "https://firmb.co.uk", "", "", ""],
        ])
        result = asyncio.run(tool.execute())
        data = json.loads(result.split("\n", 2)[-1])
        assert data[0]["row_index"] == 2
        assert data[1]["row_index"] == 3

    def test_missing_cells_return_empty_string(self):
        import json
        tool, svc = self._make_tool()
        self._set_rows(svc, [
            ["Company Name"],
            ["Minimal Firm"],  # only name, rest missing
        ])
        result = asyncio.run(tool.execute())
        data = json.loads(result.split("\n", 2)[-1])
        assert data[0]["website"] == ""
        assert data[0]["notes"] == ""

    def test_summary_counts_missing_website(self):
        tool, svc = self._make_tool()
        self._set_rows(svc, [
            ["Company Name", "Comment", "Rating", "Notes", "Website", "LinkedIn", "Size", "HQ"],
            ["Firm A", "", "", "desc", "", "", "", ""],  # no website
            ["Firm B", "", "", "desc", "https://firmb.co.uk", "", "", ""],
        ])
        result = asyncio.run(tool.execute())
        assert "1 missing website" in result

    def test_api_error_returns_error_message(self):
        from tools.sheets_read_tool import SheetsReadTool
        tool = SheetsReadTool.__new__(SheetsReadTool)
        tool._spreadsheet_id = "SHEET_ID"
        tool._sheet_name = "LawFirms"
        tool._service = None
        tool._credentials_file = "/nonexistent/creds.json"
        result = asyncio.run(tool.execute())
        assert "error" in result.lower()

    def test_reads_correct_range(self):
        tool, svc = self._make_tool(sheet_name="Advisors")
        self._set_rows(svc, [["Company Name"]])
        asyncio.run(tool.execute())
        get_call = svc.spreadsheets.return_value.values.return_value.get
        kwargs = get_call.call_args.kwargs
        assert kwargs["range"] == "Advisors!A:H"


# ── signal_agent task prompt ───────────────────────────────────────────────

class TestSkipLogic:

    def test_skip_done_checks_corporate_signal_column(self):
        """skip_done uses col J (corporate signal) as the scanned marker.
        After the signal agent runs, J always has Yes or No — never stays empty."""
        from agents.signal_agent import _is_already_scanned
        row_yes  = [""] * 19; row_yes[9]  = "Yes"
        row_no   = [""] * 19; row_no[9]   = "No"
        row_empty = [""] * 19
        assert _is_already_scanned(row_yes)   is True
        assert _is_already_scanned(row_no)    is True
        assert _is_already_scanned(row_empty) is False

    def test_all_no_signals_still_considered_scanned(self):
        """A row where all signals came back No should be treated as scanned."""
        from agents.signal_agent import _is_already_scanned
        row = [""] * 19
        row[9] = "No"   # corporate = No
        assert _is_already_scanned(row) is True

    def test_is_all_no_true_when_every_signal_no(self):
        from agents.signal_agent import _is_all_no
        row = [""] * 19
        for col in [9, 11, 13, 15, 17]:
            row[col] = "No"
        assert _is_all_no(row) is True

    def test_is_all_no_false_when_any_yes(self):
        from agents.signal_agent import _is_all_no
        row = [""] * 19
        for col in [9, 11, 13, 15, 17]:
            row[col] = "No"
        row[9] = "Yes"
        assert _is_all_no(row) is False

    def test_is_all_no_false_when_unscanned(self):
        from agents.signal_agent import _is_all_no
        assert _is_all_no([""] * 19) is False

    def test_retry_empty_flag_is_supported(self):
        import inspect
        from agents.signal_agent import main
        sig = inspect.signature(main)
        assert "retry_empty" in sig.parameters

    def test_specialist_prompt_distinguishes_primary_practice(self):
        """Specialist signal prompt must explain primary vs general practice distinction."""
        from agents.signal_agent import TASK
        task_lower = TASK.lower()
        assert "primary" in task_lower or "sole" in task_lower or "specialist" in task_lower

    def test_legaltech_brokers_tab_is_skipped(self):
        """signal_agent should refuse to process LegaltechBrokers."""
        from agents.signal_agent import _tab_supports_signals
        assert _tab_supports_signals("LawFirms")       is True
        assert _tab_supports_signals("Advisors")        is True
        assert _tab_supports_signals("Charities")       is True
        assert _tab_supports_signals("LegaltechBrokers") is False


class TestSignalAgentTask:
    """TASK is the LLM analysis prompt — it describes what the LLM should do
    with pre-fetched search results, not the agent loop instructions."""

    @pytest.fixture(autouse=True)
    def import_task(self):
        from agents.signal_agent import TASK
        self.task = TASK

    def test_task_mentions_all_five_signals(self):
        for signal in ("corporate", "specialist", "multivisa", "highvolume", "growth"):
            assert signal in self.task.lower(), f"Signal '{signal}' missing from task"

    def test_task_mentions_search_results_field(self):
        # LLM receives pre-fetched search_results — task must reference this
        assert "search_results" in self.task

    def test_task_requires_source_for_yes(self):
        assert "source" in self.task.lower()

    def test_task_requires_not_found_for_no(self):
        assert "not found" in self.task.lower()

    def test_task_instructs_no_website_fallback(self):
        assert "no website" in self.task.lower()

    def test_corporate_signal_keywords_in_task(self):
        assert "sponsor licence" in self.task
        assert "skilled worker" in self.task

    def test_specialist_signal_keywords_in_task(self):
        assert "immigration law firm" in self.task or "specialist immigration" in self.task
        assert "primary" in self.task.lower() or "sole" in self.task.lower()

    def test_multivisa_threshold_in_task(self):
        assert "3" in self.task  # 3+ visa types

    def test_task_returns_json_array(self):
        assert "row_index" in self.task
        assert "detected" in self.task

    def test_skip_done_flag_is_supported(self):
        import inspect
        from agents.signal_agent import main
        sig = inspect.signature(main)
        assert "skip_done" in sig.parameters

    def test_dry_run_flag_is_supported(self):
        import inspect
        from agents.signal_agent import main
        sig = inspect.signature(main)
        assert "dry_run" in sig.parameters


class TestSignalAgentHelpers:

    def test_domain_from_url_strips_https_www(self):
        from agents.signal_agent import _domain_from_url
        assert _domain_from_url("https://www.smithlaw.co.uk/about") == "smithlaw.co.uk"

    def test_domain_from_url_strips_http(self):
        from agents.signal_agent import _domain_from_url
        assert _domain_from_url("http://example.co.uk/") == "example.co.uk"

    def test_domain_from_url_bare_domain(self):
        from agents.signal_agent import _domain_from_url
        assert _domain_from_url("visalaw.com") == "visalaw.com"

    def test_format_results_returns_string(self):
        from agents.signal_agent import _format_results
        results = [{"title": "Immigration Solicitors", "snippet": "We handle skilled worker visas.", "link": "https://example.co.uk"}]
        out = _format_results(results)
        assert "Immigration Solicitors" in out
        assert "skilled worker" in out

    def test_format_results_empty_list(self):
        from agents.signal_agent import _format_results
        assert _format_results([]) == ""

    def test_format_results_caps_at_ten(self):
        from agents.signal_agent import _format_results
        results = [{"title": f"Title {i}", "snippet": "x", "link": "http://x.co.uk"} for i in range(20)]
        out = _format_results(results)
        assert out.count("Title:") <= 10


# ── website scrape helpers ────────────────────────────────────────────────

class TestScrapeHelpers:

    def test_extract_text_returns_string(self):
        from agents.signal_agent import _extract_text_from_html
        html = "<html><body><h1>Immigration Solicitors</h1><p>We handle sponsor licences.</p></body></html>"
        text = _extract_text_from_html(html)
        assert "Immigration Solicitors" in text
        assert "sponsor licences" in text

    def test_extract_text_strips_scripts(self):
        from agents.signal_agent import _extract_text_from_html
        html = "<html><body><script>var x=1;</script><p>Real content</p></body></html>"
        text = _extract_text_from_html(html)
        assert "var x" not in text
        assert "Real content" in text

    def test_extract_text_strips_styles(self):
        from agents.signal_agent import _extract_text_from_html
        html = "<html><head><style>.foo{color:red}</style></head><body><p>Content</p></body></html>"
        text = _extract_text_from_html(html)
        assert ".foo" not in text
        assert "Content" in text

    def test_extract_text_empty_html(self):
        from agents.signal_agent import _extract_text_from_html
        assert _extract_text_from_html("") == ""

    def test_scrape_results_format_as_fake_serp_entries(self):
        from agents.signal_agent import _scrape_to_results
        # Given scraped text from the company's own website, it should be
        # wrapped in a fake result dict the LLM prompt can consume
        results = _scrape_to_results("https://example.co.uk", "Sponsor licence service page content here")
        assert isinstance(results, list)
        assert len(results) >= 1
        assert results[0].get("link") == "https://example.co.uk"
        assert "content" in results[0].get("snippet", "").lower() or \
               "Sponsor" in results[0].get("snippet", "") or \
               len(results[0].get("snippet", "")) > 0

    def test_scrape_results_empty_text_returns_empty(self):
        from agents.signal_agent import _scrape_to_results
        assert _scrape_to_results("https://example.co.uk", "") == []

    def test_should_scrape_when_both_fallbacks_empty(self):
        from agents.signal_agent import _should_scrape_website
        assert _should_scrape_website([], []) is True

    def test_should_not_scrape_when_results_exist(self):
        from agents.signal_agent import _should_scrape_website
        assert _should_scrape_website([{"title": "x"}], []) is False


# ── fallback queries ──────────────────────────────────────────────────────

class TestFallbackQueries:
    """When site: searches return 0 results, name-based fallback queries are used."""

    def test_fallback_queries_contain_company_name(self):
        from agents.signal_agent import _build_fallback_queries
        qa, qb = _build_fallback_queries("Fragomen")
        assert "Fragomen" in qa
        assert "Fragomen" in qb

    def test_fallback_queries_have_no_site_operator(self):
        from agents.signal_agent import _build_fallback_queries
        qa, qb = _build_fallback_queries("Fragomen")
        assert "site:" not in qa
        assert "site:" not in qb

    def test_fallback_query_a_covers_corporate_signals(self):
        from agents.signal_agent import _build_fallback_queries
        qa, _ = _build_fallback_queries("Smith Stone Walters")
        terms = qa.lower()
        assert any(kw in terms for kw in ("sponsor licence", "skilled worker", "corporate immigration"))

    def test_fallback_query_b_covers_specialist_and_multivisa(self):
        from agents.signal_agent import _build_fallback_queries
        _, qb = _build_fallback_queries("IAS Immigration")
        terms = qb.lower()
        assert any(kw in terms for kw in ("immigration law firm", "specialist immigration", "family visa", "student visa"))

    def test_fallback_query_b_covers_growth(self):
        from agents.signal_agent import _build_fallback_queries
        _, qb = _build_fallback_queries("Gherson Solicitors")
        terms = qb.lower()
        assert any(kw in terms for kw in ("hiring", "join our team", "expanding", "new office"))

    def test_fallback_returns_two_strings(self):
        from agents.signal_agent import _build_fallback_queries
        result = _build_fallback_queries("Any Firm Ltd")
        assert len(result) == 2
        assert all(isinstance(q, str) and len(q) > 10 for q in result)

    def test_fallback_triggered_when_zero_site_results(self):
        """_should_use_fallback returns True when both site searches return empty."""
        from agents.signal_agent import _should_use_fallback
        assert _should_use_fallback([], []) is True

    def test_fallback_not_triggered_when_results_exist(self):
        from agents.signal_agent import _should_use_fallback
        assert _should_use_fallback([{"title": "x"}], []) is False
        assert _should_use_fallback([], [{"title": "x"}]) is False
        assert _should_use_fallback([{"title": "x"}], [{"title": "y"}]) is False


# ── signal definitions consistency ────────────────────────────────────────

class TestSignalConsistency:
    """Verify signal names in agent are consistent with the update tool."""

    def test_agent_signals_match_tool_signals(self):
        from tools.sheets_update_signal_tool import VALID_SIGNALS
        from agents.signal_agent import TASK
        for signal in VALID_SIGNALS:
            assert signal in TASK.lower(), f"Signal '{signal}' from tool not mentioned in agent task"

    def test_five_signals_total(self):
        from tools.sheets_update_signal_tool import VALID_SIGNALS
        assert len(VALID_SIGNALS) == 5

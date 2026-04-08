"""Tests for SheetsAppendTool — dedup logic and row writing."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.sheets_tool import SheetsAppendTool


def _make_tool(existing_names=None, existing_domains=None, sheet_name="LawFirms"):
    svc = MagicMock()
    tool = SheetsAppendTool.__new__(SheetsAppendTool)
    tool._spreadsheet_id = "SHEET_ID"
    tool._sheet_name = sheet_name
    tool._service = svc
    tool._existing_names = set(existing_names or [])
    tool._existing_domains = set(existing_domains or [])
    return tool, svc


class TestSheetsAppendToolDedup:

    def test_skips_known_name(self):
        tool, svc = _make_tool(existing_names={"smith & co solicitors"})
        result = asyncio.run(tool.execute(company_name="Smith & Co Solicitors", website="https://smithco.co.uk"))
        assert "skipped" in result
        svc.spreadsheets.return_value.values.return_value.append.assert_not_called()

    def test_skips_known_domain(self):
        tool, svc = _make_tool(existing_domains={"smithco.co.uk"})
        result = asyncio.run(tool.execute(company_name="Smith & Co Solicitors", website="https://smithco.co.uk"))
        assert "skipped" in result
        svc.spreadsheets.return_value.values.return_value.append.assert_not_called()

    def test_skips_domain_with_www_prefix(self):
        tool, svc = _make_tool(existing_domains={"example.co.uk"})
        result = asyncio.run(tool.execute(company_name="Example", website="https://www.example.co.uk/"))
        assert "skipped" in result

    def test_new_company_is_appended(self):
        tool, svc = _make_tool()
        result = asyncio.run(tool.execute(company_name="New Firm Ltd", website="https://newfirm.co.uk"))
        assert "Added to sheet" in result
        svc.spreadsheets.return_value.values.return_value.append.assert_called_once()

    def test_new_company_added_to_sets(self):
        tool, svc = _make_tool()
        asyncio.run(tool.execute(company_name="New Firm Ltd", website="https://newfirm.co.uk"))
        assert "new firm ltd" in tool._existing_names
        assert "newfirm.co.uk" in tool._existing_domains

    def test_second_call_with_same_name_skipped(self):
        tool, svc = _make_tool()
        asyncio.run(tool.execute(company_name="New Firm Ltd", website="https://newfirm.co.uk"))
        result = asyncio.run(tool.execute(company_name="New Firm Ltd", website="https://newfirm.co.uk"))
        assert "skipped" in result
        assert svc.spreadsheets.return_value.values.return_value.append.call_count == 1

    def test_case_insensitive_name_match(self):
        tool, svc = _make_tool(existing_names={"uk visa law ltd"})
        result = asyncio.run(tool.execute(company_name="UK Visa Law Ltd", website="https://ukvisalaw.co.uk"))
        assert "skipped" in result

    def test_empty_existing_sets_appends_normally(self):
        tool, svc = _make_tool(existing_names=set(), existing_domains=set())
        result = asyncio.run(tool.execute(company_name="Fresh Firm Ltd", website="https://freshfirm.co.uk"))
        assert "Added to sheet" in result


class TestSheetsAppendToolRowStructure:

    def test_row_has_nineteen_columns(self):
        tool, svc = _make_tool()
        asyncio.run(tool.execute(
            company_name="Test Ltd",
            website="https://test.co.uk",
            linkedin="https://linkedin.com/company/test",
            size="11-50",
            hq_location="London, UK",
            notes="Immigration law firm",
        ))
        append_call = svc.spreadsheets.return_value.values.return_value.append
        body = append_call.call_args.kwargs["body"]
        row = body["values"][0]
        assert len(row) == 19

    def test_row_columns_in_correct_order(self):
        tool, svc = _make_tool()
        asyncio.run(tool.execute(
            company_name="Test Ltd",
            website="https://test.co.uk",
            linkedin="https://linkedin.com/company/test",
            size="11-50",
            hq_location="London, UK",
            notes="Immigration law firm",
        ))
        append_call = svc.spreadsheets.return_value.values.return_value.append
        body = append_call.call_args.kwargs["body"]
        row = body["values"][0]
        assert row[0] == "Test Ltd"          # A – Company Name
        assert row[1] == ""                   # B – Comment Melt
        assert row[2] == ""                   # C – Rating
        assert row[3] == "Immigration law firm"  # D – Notes
        assert row[4] == "https://test.co.uk"    # E – Website
        assert row[5] == "https://linkedin.com/company/test"  # F – LinkedIn
        assert row[6] == "11-50"              # G – Size
        assert row[7] == "London, UK"         # H – HQ Location
        # row[8] = Date Added (I) — dynamic, just check it's set
        assert row[8] != ""

    def test_optional_fields_default_to_empty(self):
        tool, svc = _make_tool()
        asyncio.run(tool.execute(company_name="Minimal Ltd", website="https://minimal.co.uk"))
        body = svc.spreadsheets.return_value.values.return_value.append.call_args.kwargs["body"]
        row = body["values"][0]
        assert row[5] == ""   # LinkedIn
        assert row[6] == ""   # Size
        assert row[7] == ""   # HQ Location

    def test_appends_to_correct_range(self):
        tool, svc = _make_tool(sheet_name="Advisors")
        asyncio.run(tool.execute(company_name="Adviser Ltd", website="https://adviser.co.uk"))
        append_call = svc.spreadsheets.return_value.values.return_value.append
        kwargs = append_call.call_args.kwargs
        assert kwargs["range"] == "Advisors!A:S"


class TestSheetsAppendToolDomainKey:

    def test_strips_https(self):
        assert SheetsAppendTool._domain_key("https://example.com") == "example.com"

    def test_strips_http(self):
        assert SheetsAppendTool._domain_key("http://example.com") == "example.com"

    def test_strips_www(self):
        assert SheetsAppendTool._domain_key("https://www.example.com") == "example.com"

    def test_strips_trailing_slash(self):
        assert SheetsAppendTool._domain_key("https://example.co.uk/") == "example.co.uk"

    def test_strips_path(self):
        assert SheetsAppendTool._domain_key("https://example.co.uk/about") == "example.co.uk"

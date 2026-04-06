"""Tests for enrich_agent — fetch_incomplete_rows filtering and build_task output."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.enrich_agent import build_task, fetch_incomplete_rows

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

HEADER = ["Company Name", "Rating", "Human Comment", "Notes", "Website", "LinkedIn", "Size", "HQ Location"]


def _make_sheet_row(name="", notes="", website="", linkedin="", size="", hq=""):
    """Build a raw Google Sheets row (list of strings, cols A-H)."""
    return [name, "", "", notes, website, linkedin, size, hq]


def _mock_service(rows: list[list[str]]):
    """Return a mock Google Sheets service whose get() returns the given rows."""
    svc = MagicMock()
    (
        svc.spreadsheets.return_value
        .values.return_value
        .get.return_value
        .execute.return_value
    ) = {"values": [HEADER] + rows}
    return svc


# ---------------------------------------------------------------------------
# fetch_incomplete_rows
# ---------------------------------------------------------------------------

class TestFetchIncompleteRows:

    def _call(self, rows):
        with (
            patch("agents.enrich_agent.Credentials.from_service_account_file"),
            patch("agents.enrich_agent.build", return_value=_mock_service(rows)),
        ):
            return fetch_incomplete_rows("SHEET_ID", "creds.json", "CH")

    def test_url_only_row_is_included(self):
        """Row with only a website URL and no company_name must be returned."""
        result = self._call([_make_sheet_row(website="https://example.ch")])
        assert len(result) == 1
        assert result[0]["website"] == "https://example.ch"
        assert result[0]["company_name"] == ""

    def test_name_only_row_is_included(self):
        """Row with only a company name (no website) must be returned."""
        result = self._call([_make_sheet_row(name="Acme AG")])
        assert len(result) == 1
        assert result[0]["company_name"] == "Acme AG"

    def test_linkedin_only_row_is_included(self):
        """Row with only a LinkedIn URL must be returned."""
        result = self._call([_make_sheet_row(linkedin="https://linkedin.com/company/acme")])
        assert len(result) == 1

    def test_row_with_notes_is_skipped(self):
        """Agent-generated rows (notes filled) must be skipped."""
        result = self._call([
            _make_sheet_row(name="Enriched AG", notes="IT provider in Zurich", website="https://enriched.ch"),
        ])
        assert result == []

    def test_fully_empty_row_is_skipped(self):
        """A row with no identifying info at all must be skipped."""
        result = self._call([_make_sheet_row()])
        assert result == []

    def test_empty_sheet_returns_empty_list(self):
        """Sheet with header only returns empty list."""
        with (
            patch("agents.enrich_agent.Credentials.from_service_account_file"),
            patch("agents.enrich_agent.build", return_value=_mock_service([])),
        ):
            result = fetch_incomplete_rows("SHEET_ID", "creds.json", "CH")
        assert result == []

    def test_mixed_rows_filters_correctly(self):
        """Only rows without notes and with at least one identifier are returned."""
        rows = [
            _make_sheet_row(website="https://a.ch"),                          # include
            _make_sheet_row(name="B GmbH", notes="Already enriched"),        # skip — has notes
            _make_sheet_row(),                                                # skip — empty
            _make_sheet_row(name="C AG", website="https://c.ch"),            # include
            _make_sheet_row(linkedin="https://linkedin.com/company/d"),      # include
        ]
        result = self._call(rows)
        assert len(result) == 3

    def test_row_index_is_1_based_starting_at_2(self):
        """First data row must have row_index=2 (row 1 is the header)."""
        rows = [_make_sheet_row(name="First"), _make_sheet_row(name="Second")]
        result = self._call(rows)
        assert result[0]["row_index"] == 2
        assert result[1]["row_index"] == 3

    def test_existing_fields_are_preserved_in_output(self):
        """Already-known website/linkedin/size/hq are passed through to the result."""
        rows = [_make_sheet_row(
            name="Test AG",
            website="https://test.ch",
            linkedin="https://linkedin.com/company/test",
            size="11-50",
            hq="Zurich, Switzerland",
        )]
        result = self._call(rows)
        r = result[0]
        assert r["website"] == "https://test.ch"
        assert r["linkedin"] == "https://linkedin.com/company/test"
        assert r["size"] == "11-50"
        assert r["hq_location"] == "Zurich, Switzerland"

    def test_whitespace_is_stripped(self):
        """Leading/trailing whitespace in cells must be stripped."""
        rows = [_make_sheet_row(name="  Acme AG  ", website="  https://acme.ch  ")]
        result = self._call(rows)
        assert result[0]["company_name"] == "Acme AG"
        assert result[0]["website"] == "https://acme.ch"


# ---------------------------------------------------------------------------
# build_task
# ---------------------------------------------------------------------------

class TestBuildTask:

    def test_contains_row_count(self):
        rows = [{"row_index": 2, "company_name": "Acme", "website": "https://acme.ch",
                 "linkedin": "", "size": "", "hq_location": ""}]
        task = build_task(rows)
        assert "1 manually-added" in task

    def test_embeds_rows_as_valid_json(self):
        rows = [
            {"row_index": 2, "company_name": "", "website": "https://a.ch",
             "linkedin": "", "size": "", "hq_location": ""},
            {"row_index": 3, "company_name": "B AG", "website": "",
             "linkedin": "", "size": "11-50", "hq_location": "Bern, Switzerland"},
        ]
        task = build_task(rows)
        # The JSON block must be parseable and contain both rows
        start = task.index("[")
        end = task.rindex("]") + 1
        parsed = json.loads(task[start:end])
        assert len(parsed) == 2
        assert parsed[0]["row_index"] == 2
        assert parsed[1]["company_name"] == "B AG"

    def test_instructs_to_fill_company_name(self):
        task = build_task([{"row_index": 2, "company_name": "", "website": "https://x.ch",
                            "linkedin": "", "size": "", "hq_location": ""}])
        assert "company_name" in task.lower() or "company name" in task.lower()

    def test_mentions_linkedin_url_handling(self):
        task = build_task([{"row_index": 2, "company_name": "", "website": "https://x.ch",
                            "linkedin": "", "size": "", "hq_location": ""}])
        assert "linkedin" in task.lower()

    def test_mentions_sheets_update_tool(self):
        task = build_task([{"row_index": 2, "company_name": "X", "website": "https://x.ch",
                            "linkedin": "", "size": "", "hq_location": ""}])
        assert "sheets_update_company_info" in task

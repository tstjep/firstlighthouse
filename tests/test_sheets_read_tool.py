"""Tests for SheetsReadTool — summary header and JSON output."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.sheets_read_tool import (
    SheetsReadTool,
    _DACH_COL_NOTES,
    _DACH_COL_WEBSITE,
    _DACH_COL_LINKEDIN,
    _DACH_COL_SIZE,
    _DACH_COL_HQ_LOCATION,
)

# DACH header (no Human Comment)
HEADER = ["Company Name", "Comment Melt", "Rating", "Notes", "Website", "LinkedIn", "Size", "HQ Location"]


def _make_tool(rows: list[list[str]]) -> tuple[SheetsReadTool, MagicMock]:
    svc = MagicMock()
    (
        svc.spreadsheets.return_value
        .values.return_value
        .get.return_value
        .execute.return_value
    ) = {"values": [HEADER] + rows}
    tool = SheetsReadTool.__new__(SheetsReadTool)
    tool._spreadsheet_id = "SHEET_ID"
    tool._sheet_name = "CH"
    tool._service = svc
    tool._col_notes = _DACH_COL_NOTES
    tool._col_website = _DACH_COL_WEBSITE
    tool._col_linkedin = _DACH_COL_LINKEDIN
    tool._col_size = _DACH_COL_SIZE
    tool._col_hq = _DACH_COL_HQ_LOCATION
    tool._read_range = "CH!A:H"
    return tool, svc


def _row(name="Acme AG", notes="IT provider", website="https://acme.ch",
         linkedin="", size="11-50", hq="Zurich, Switzerland"):
    # DACH layout: A=Name, B=Comment Melt, C=Rating, D=Notes, E=Website, F=LinkedIn, G=Size, H=HQ
    return [name, "", "", notes, website, linkedin, size, hq]


class TestSheetsReadToolEmptySheet:

    def test_header_only_returns_zero_summary(self):
        import asyncio
        tool, _ = _make_tool([])
        result = asyncio.run(tool.execute())
        assert "# 0 companies" in result
        assert "[]" in result


class TestSheetsReadToolSummaryHeader:

    def _run(self, rows):
        import asyncio
        tool, _ = _make_tool(rows)
        return asyncio.run(tool.execute())

    def test_total_count_in_header(self):
        result = self._run([_row(), _row(name="Beta AG", website="https://beta.ch")])
        assert "# 2 companies" in result

    def test_enrichment_count_notes_empty(self):
        """Rows without notes count as needing enrichment."""
        result = self._run([
            _row(notes=""),               # needs enrichment
            _row(notes="IT provider"),    # already enriched
        ])
        assert "Rows needing enrichment (notes empty): 1" in result

    def test_all_complete_shows_zero_enrichment_needed(self):
        result = self._run([_row(), _row(name="Beta AG", website="https://beta.ch")])
        assert "Rows needing enrichment (notes empty): 0" in result

    def test_field_gaps_shown(self):
        """Missing fields are reported in the header."""
        result = self._run([_row(linkedin="", size="")])
        assert "missing linkedin" in result
        assert "missing size" in result

    def test_no_field_gaps_line_when_all_complete(self):
        """No 'Field gaps' line when every field is present."""
        result = self._run([_row(
            notes="desc", website="https://x.ch", linkedin="https://li.com/x",
            size="11-50", hq="Zurich, Switzerland",
        )])
        assert "Field gaps" not in result


class TestSheetsReadToolJsonOutput:

    def _parse(self, rows):
        import asyncio
        tool, _ = _make_tool(rows)
        raw = asyncio.run(tool.execute())
        # JSON starts after the header comment lines
        start = raw.index("[")
        return json.loads(raw[start:])

    def test_company_fields_present(self):
        parsed = self._parse([_row()])
        assert parsed[0].keys() >= {"row_index", "company_name", "notes", "website", "linkedin", "size", "hq_location"}

    def test_row_index_correct(self):
        parsed = self._parse([_row(), _row(name="Beta AG", website="https://beta.ch")])
        assert parsed[0]["row_index"] == 2
        assert parsed[1]["row_index"] == 3

    def test_values_mapped_to_correct_fields(self):
        parsed = self._parse([_row(
            name="Acme AG", notes="IT firm", website="https://acme.ch",
            linkedin="https://li.com/acme", size="51-200", hq="Basel, Switzerland",
        )])
        r = parsed[0]
        assert r["company_name"] == "Acme AG"
        assert r["notes"] == "IT firm"
        assert r["website"] == "https://acme.ch"
        assert r["linkedin"] == "https://li.com/acme"
        assert r["size"] == "51-200"
        assert r["hq_location"] == "Basel, Switzerland"

    def test_short_rows_return_empty_strings(self):
        """A row with fewer columns than expected fills missing cells with ''."""
        parsed = self._parse([["Only Name"]])
        r = parsed[0]
        assert r["company_name"] == "Only Name"
        assert r["notes"] == ""
        assert r["website"] == ""

    def test_whitespace_stripped(self):
        parsed = self._parse([_row(name="  Acme AG  ", website="  https://acme.ch  ")])
        assert parsed[0]["company_name"] == "Acme AG"
        assert parsed[0]["website"] == "https://acme.ch"

    def test_json_is_compact(self):
        """Output must use compact separators (no indentation)."""
        import asyncio
        tool, _ = _make_tool([_row()])
        raw = asyncio.run(tool.execute())
        start = raw.index("[")
        json_part = raw[start:]
        assert "\n  " not in json_part  # no indented formatting

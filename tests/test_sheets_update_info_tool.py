"""Tests for SheetsUpdateInfoTool — field writing and no-overwrite logic."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.sheets_update_info_tool import _DACH_INFO_COLS as INFO_COLS, SheetsUpdateInfoTool


def _make_tool(date_added_exists: bool = False) -> tuple[SheetsUpdateInfoTool, MagicMock]:
    svc = MagicMock()
    # Simulate reading Date Added cell: return value only if it "exists"
    existing_val = {"values": [["2026-01-01"]]} if date_added_exists else {}
    (
        svc.spreadsheets.return_value
        .values.return_value
        .get.return_value
        .execute.return_value
    ) = existing_val

    tool = SheetsUpdateInfoTool.__new__(SheetsUpdateInfoTool)
    tool._spreadsheet_id = "SHEET_ID"
    tool._sheet_name = "CH"
    tool._service = svc
    tool._info_cols = INFO_COLS
    tool._date_added_col = "X"
    return tool, svc


def _update_calls(svc: MagicMock) -> list[dict]:
    """Collect all range/value pairs from batchUpdate (fields) and individual update (date_added)."""
    calls_made = []
    # Field writes go through batchUpdate
    for c in svc.spreadsheets.return_value.values.return_value.batchUpdate.call_args_list:
        for entry in c.kwargs.get("body", {}).get("data", []):
            calls_made.append({
                "range": entry.get("range", ""),
                "value": entry.get("values", [[""]])[0][0],
            })
    # Date Added write goes through individual update
    for c in svc.spreadsheets.return_value.values.return_value.update.call_args_list:
        calls_made.append({
            "range": c.kwargs.get("range", c.args[0] if c.args else ""),
            "value": c.kwargs.get("body", {}).get("values", [[""]])[0][0],
        })
    return calls_made


class TestSheetsUpdateInfoToolFieldWriting:

    def test_company_name_written_to_col_a(self):
        tool, svc = _make_tool()
        asyncio.run(tool.execute(row_index=2, company_name="Acme AG"))
        updates = _update_calls(svc)
        written = {u["range"]: u["value"] for u in updates}
        assert "CH!A2" in written
        assert written["CH!A2"] == "Acme AG"

    def test_website_written_to_col_e(self):
        tool, svc = _make_tool()
        asyncio.run(tool.execute(row_index=5, website="https://example.ch"))
        updates = _update_calls(svc)
        written = {u["range"]: u["value"] for u in updates}
        assert "CH!E5" in written

    def test_all_fields_written_at_correct_columns(self):
        tool, svc = _make_tool()
        asyncio.run(tool.execute(
            row_index=3,
            company_name="Test AG",
            notes="IT provider",
            website="https://test.ch",
            linkedin="https://linkedin.com/company/test",
            size="11-50",
            hq_location="Bern, Switzerland",
        ))
        updates = _update_calls(svc)
        written = {u["range"]: u["value"] for u in updates}
        assert written.get("CH!A3") == "Test AG"
        assert written.get("CH!D3") == "IT provider"
        assert written.get("CH!E3") == "https://test.ch"
        assert written.get("CH!F3") == "https://linkedin.com/company/test"
        assert written.get("CH!G3") == "11-50"
        assert written.get("CH!H3") == "Bern, Switzerland"

    def test_empty_fields_are_not_written(self):
        """Passing empty string for a field must not trigger an update call for that field."""
        tool, svc = _make_tool()
        asyncio.run(tool.execute(row_index=2, company_name="Acme AG", website=""))
        updates = _update_calls(svc)
        written_ranges = {u["range"] for u in updates}
        assert "CH!E2" not in written_ranges  # website not written

    def test_nothing_to_update_when_all_empty_and_date_already_set(self):
        # date_added_exists=True so the tool won't write that either
        tool, svc = _make_tool(date_added_exists=True)
        result = asyncio.run(tool.execute(row_index=2))
        assert "nothing to update" in result

    def test_return_message_lists_written_fields(self):
        tool, svc = _make_tool()
        result = asyncio.run(tool.execute(row_index=4, notes="desc", size="51-200"))
        assert "notes" in result
        assert "size" in result
        assert "Row 4" in result


class TestSheetsUpdateInfoToolDateAdded:

    def test_date_added_written_when_blank(self):
        tool, svc = _make_tool(date_added_exists=False)
        asyncio.run(tool.execute(row_index=2, notes="desc"))
        updates = _update_calls(svc)
        written_ranges = {u["range"] for u in updates}
        assert "CH!X2" in written_ranges

    def test_date_added_not_overwritten_when_present(self):
        tool, svc = _make_tool(date_added_exists=True)
        asyncio.run(tool.execute(row_index=2, notes="desc"))
        updates = _update_calls(svc)
        written_ranges = {u["range"] for u in updates}
        assert "CH!X2" not in written_ranges

    def test_date_added_included_in_return_message(self):
        tool, svc = _make_tool(date_added_exists=False)
        result = asyncio.run(tool.execute(row_index=2, notes="desc"))
        assert "date_added" in result


class TestSheetsUpdateInfoToolColumnMapping:

    def test_info_cols_covers_all_fields(self):
        expected = {"company_name", "notes", "website", "linkedin", "size", "hq_location"}
        assert set(INFO_COLS.keys()) == expected

    def test_column_letters_correct(self):
        assert INFO_COLS["company_name"] == "A"
        assert INFO_COLS["notes"]        == "D"
        assert INFO_COLS["website"]      == "E"
        assert INFO_COLS["linkedin"]     == "F"
        assert INFO_COLS["size"]         == "G"
        assert INFO_COLS["hq_location"]  == "H"

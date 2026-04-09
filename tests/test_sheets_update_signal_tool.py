"""Tests for SheetsUpdateSignalTool — signal writing and validation."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.sheets_update_signal_tool import SheetsUpdateSignalTool, SIGNAL_COLS, VALID_SIGNALS


def _make_tool(sheet_name="LawFirms"):
    svc = MagicMock()
    tool = SheetsUpdateSignalTool.__new__(SheetsUpdateSignalTool)
    tool._spreadsheet_id = "SHEET_ID"
    tool._sheet_name = sheet_name
    tool._service = svc
    return tool, svc


class TestSignalCols:

    def test_all_five_signals_defined(self):
        assert set(SIGNAL_COLS.keys()) == {"corporate", "specialist", "multivisa", "highvolume", "growth"}

    def test_each_signal_has_two_columns(self):
        for name, cols in SIGNAL_COLS.items():
            assert len(cols) == 2, f"{name} should have 2 columns (signal + source)"

    def test_column_mapping(self):
        assert SIGNAL_COLS["corporate"]   == ("J", "K")
        assert SIGNAL_COLS["specialist"]  == ("L", "M")
        assert SIGNAL_COLS["multivisa"]   == ("N", "O")
        assert SIGNAL_COLS["highvolume"]  == ("P", "Q")
        assert SIGNAL_COLS["growth"]      == ("R", "S")

    def test_no_column_overlap(self):
        all_cols = [c for cols in SIGNAL_COLS.values() for c in cols]
        assert len(all_cols) == len(set(all_cols))

    def test_valid_signals_matches_signal_cols(self):
        assert set(VALID_SIGNALS) == set(SIGNAL_COLS.keys())


class TestValidation:

    def test_unknown_signal_returns_error(self):
        tool, _ = _make_tool()
        result = asyncio.run(tool.execute(row_index=2, signal="foobar", detected=True, source="x"))
        assert "error" in result.lower()
        assert "foobar" in result

    def test_row_index_one_returns_error(self):
        tool, _ = _make_tool()
        result = asyncio.run(tool.execute(row_index=1, signal="corporate", detected=True, source="x"))
        assert "error" in result.lower()

    def test_row_index_zero_returns_error(self):
        tool, _ = _make_tool()
        result = asyncio.run(tool.execute(row_index=0, signal="corporate", detected=True, source="x"))
        assert "error" in result.lower()


class TestWrites:

    @pytest.mark.parametrize("signal", VALID_SIGNALS)
    def test_valid_signal_calls_batch_update(self, signal):
        tool, svc = _make_tool()
        asyncio.run(tool.execute(row_index=3, signal=signal, detected=True, source="test"))
        svc.spreadsheets.return_value.values.return_value.batchUpdate.assert_called_once()

    def test_detected_true_writes_yes(self):
        tool, svc = _make_tool()
        asyncio.run(tool.execute(row_index=2, signal="corporate", detected=True, source="sponsor page"))
        body = svc.spreadsheets.return_value.values.return_value.batchUpdate.call_args.kwargs["body"]
        assert body["data"][0]["values"] == [["Yes"]]

    def test_detected_false_writes_no(self):
        tool, svc = _make_tool()
        asyncio.run(tool.execute(row_index=2, signal="corporate", detected=False, source="not found"))
        body = svc.spreadsheets.return_value.values.return_value.batchUpdate.call_args.kwargs["body"]
        assert body["data"][0]["values"] == [["No"]]

    def test_writes_two_ranges(self):
        tool, svc = _make_tool()
        asyncio.run(tool.execute(row_index=5, signal="specialist", detected=True, source="immigration law firm"))
        body = svc.spreadsheets.return_value.values.return_value.batchUpdate.call_args.kwargs["body"]
        assert len(body["data"]) == 2

    def test_correct_row_number_in_ranges(self):
        tool, svc = _make_tool()
        asyncio.run(tool.execute(row_index=7, signal="growth", detected=True, source="hiring"))
        body = svc.spreadsheets.return_value.values.return_value.batchUpdate.call_args.kwargs["body"]
        assert all("7" in entry["range"] for entry in body["data"])

    def test_uses_correct_tab_name(self):
        tool, svc = _make_tool(sheet_name="Advisors")
        asyncio.run(tool.execute(row_index=2, signal="corporate", detected=True, source="x"))
        body = svc.spreadsheets.return_value.values.return_value.batchUpdate.call_args.kwargs["body"]
        assert all(entry["range"].startswith("Advisors!") for entry in body["data"])

    def test_success_message_contains_signal_and_value(self):
        tool, svc = _make_tool()
        result = asyncio.run(tool.execute(row_index=2, signal="multivisa", detected=True, source="family visa"))
        assert "multivisa" in result
        assert "Yes" in result

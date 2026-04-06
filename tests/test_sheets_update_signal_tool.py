"""Tests for SheetsUpdateSignalTool — signal column writing."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.sheets_update_tool import _DACH_SIGNAL_COLS as SIGNAL_COLS, SheetsUpdateSignalTool


def _make_tool() -> tuple[SheetsUpdateSignalTool, MagicMock]:
    svc = MagicMock()
    tool = SheetsUpdateSignalTool.__new__(SheetsUpdateSignalTool)
    tool._spreadsheet_id = "SHEET_ID"
    tool._sheet_name = "CH"
    tool._service = svc
    tool._signal_cols = SIGNAL_COLS
    return tool, svc


def _update_calls(svc: MagicMock) -> dict[str, str]:
    """Extract range→value from the single batchUpdate call."""
    result = {}
    for c in svc.spreadsheets.return_value.values.return_value.batchUpdate.call_args_list:
        for entry in c.kwargs.get("body", {}).get("data", []):
            result[entry.get("range", "")] = entry.get("values", [[""]])[0][0]
    return result


class TestSheetsUpdateSignalToolWriting:

    @pytest.mark.parametrize("signal,cols", SIGNAL_COLS.items())
    def test_correct_columns_written_for_each_signal(self, signal, cols):
        bool_col, src_col, date_col = cols
        tool, svc = _make_tool()
        asyncio.run(tool.execute(row_index=2, signal=signal, detected=True, source="evidence"))
        written = _update_calls(svc)
        assert f"CH!{bool_col}2" in written
        assert f"CH!{src_col}2" in written
        assert f"CH!{date_col}2" in written

    def test_detected_true_writes_yes(self):
        tool, svc = _make_tool()
        asyncio.run(tool.execute(row_index=3, signal="ai", detected=True, source="AI mention"))
        written = _update_calls(svc)
        assert written["CH!I3"] == "Yes"

    def test_detected_false_writes_no(self):
        tool, svc = _make_tool()
        asyncio.run(tool.execute(row_index=3, signal="ai", detected=False, source="not found"))
        written = _update_calls(svc)
        assert written["CH!I3"] == "No"

    def test_source_written_to_source_column(self):
        tool, svc = _make_tool()
        asyncio.run(tool.execute(row_index=4, signal="kubernetes", detected=True, source="k8s blog post"))
        written = _update_calls(svc)
        assert written["CH!V4"] == "k8s blog post"

    def test_always_writes_exactly_three_columns(self):
        tool, svc = _make_tool()
        asyncio.run(tool.execute(row_index=2, signal="cost", detected=False, source="not found"))
        batch_call = svc.spreadsheets.return_value.values.return_value.batchUpdate.call_args
        data = batch_call.kwargs["body"]["data"]
        assert len(data) == 3

    def test_return_message_contains_signal_and_value(self):
        tool, svc = _make_tool()
        result = asyncio.run(tool.execute(row_index=5, signal="edge", detected=True, source="edge computing page"))
        assert "edge" in result
        assert "Yes" in result
        assert "Row 5" in result


class TestSignalColsMapping:

    def test_all_signals_present(self):
        assert {"ai", "sovereignty", "edge", "cost", "kubernetes", "kubecon"} <= set(SIGNAL_COLS.keys())

    def test_column_triples_correct(self):
        assert SIGNAL_COLS["ai"]          == ("I", "J", "K")
        assert SIGNAL_COLS["sovereignty"] == ("L", "M", "N")
        assert SIGNAL_COLS["edge"]        == ("O", "P", "Q")
        assert SIGNAL_COLS["cost"]        == ("R", "S", "T")
        assert SIGNAL_COLS["kubernetes"]  == ("U", "V", "W")

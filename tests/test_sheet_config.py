"""Tests for agents.sheet_config — encoding, parsing, and load_sheet_config."""

import copy
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.sheet_config import (
    TAB_NAME,
    _build_rows,
    _decode_value,
    _encode_value,
    _parse_rows,
    load_sheet_config,
)
from agents.it_search_agent import COUNTRY_SEARCH_HINTS, COUNTRY_SERP_PARAMS


# ── Fixtures ────────────────────────────────────────────────────────────────

_MINIMAL_HINTS = {
    "CH": {"tld": "ch", "min_searches": 10, "target_companies": 20,
           "cities": ["Zürich", "Basel"], "regions": [],
           "tld_queries": ['site:.ch "IT"'], "extra_queries": ['"IT" Schweiz']},
}
_MINIMAL_SERP = {"CH": {"gl": "ch", "cr": "countryCH"}}


def _make_service(rows: list[list[str]], tab_exists: bool = True) -> MagicMock:
    svc = MagicMock()
    # _tab_exists → spreadsheets().get().execute()
    titles = [TAB_NAME] if tab_exists else []
    (
        svc.spreadsheets.return_value
        .get.return_value
        .execute.return_value
    ) = {"sheets": [{"properties": {"title": t}} for t in titles]}
    # load rows → spreadsheets().values().get().execute()
    (
        svc.spreadsheets.return_value
        .values.return_value
        .get.return_value
        .execute.return_value
    ) = {"values": rows}
    return svc


def _call_load(rows, tab_exists=True, hints=None, serp=None):
    hints = hints or copy.deepcopy(COUNTRY_SEARCH_HINTS)
    serp = serp or copy.deepcopy(COUNTRY_SERP_PARAMS)
    svc = _make_service(rows, tab_exists=tab_exists)
    with (
        patch("pathlib.Path.exists", return_value=True),
        patch("agents.sheet_config.Credentials.from_service_account_file"),
        patch("agents.sheet_config.build", return_value=svc),
    ):
        return load_sheet_config("SHEET_ID", "creds.json", hints, serp)


# ── Encode / decode ──────────────────────────────────────────────────────────

class TestEncodeValue:

    def test_list_joined_with_newline(self):
        assert _encode_value("cities", ["Zürich", "Basel"]) == "Zürich\nBasel"

    def test_empty_list_returns_empty_string(self):
        assert _encode_value("cities", []) == ""

    def test_int_converted_to_string(self):
        assert _encode_value("min_searches", 25) == "25"

    def test_scalar_unchanged(self):
        assert _encode_value("tld", "ch") == "ch"

    def test_serp_scalar_unchanged(self):
        assert _encode_value("gl", "de") == "de"


class TestDecodeValue:

    def test_list_split_on_newline(self):
        assert _decode_value("cities", "Zürich\nBasel") == ["Zürich", "Basel"]

    def test_empty_string_list_returns_empty_list(self):
        assert _decode_value("cities", "") == []

    def test_list_strips_whitespace(self):
        assert _decode_value("cities", " Zürich \n Basel ") == ["Zürich", "Basel"]

    def test_int_parsed(self):
        assert _decode_value("min_searches", "25") == 25

    def test_invalid_int_returns_none(self):
        assert _decode_value("min_searches", "bad") is None

    def test_scalar_unchanged(self):
        assert _decode_value("tld", "ch") == "ch"

    def test_serp_gl_unchanged(self):
        assert _decode_value("gl", "de") == "de"


# ── Build rows ───────────────────────────────────────────────────────────────

class TestBuildRows:

    def test_header_row_first(self):
        rows = _build_rows(_MINIMAL_HINTS, _MINIMAL_SERP)
        assert rows[0] == ["country", "key", "value"]

    def test_data_rows_for_full_hints(self):
        rows = _build_rows(COUNTRY_SEARCH_HINTS, COUNTRY_SERP_PARAMS)
        n_countries = len(COUNTRY_SEARCH_HINTS)
        # 1 header + n_countries × 9 keys
        assert len(rows) == 1 + n_countries * 9

    def test_list_encoded_with_newlines(self):
        rows = _build_rows(_MINIMAL_HINTS, _MINIMAL_SERP)
        cities_row = next(r for r in rows if r[1] == "cities" and r[0] == "CH")
        assert "\n" in cities_row[2]
        assert "Zürich" in cities_row[2]

    def test_int_encoded_as_string(self):
        rows = _build_rows(_MINIMAL_HINTS, _MINIMAL_SERP)
        row = next(r for r in rows if r[1] == "min_searches" and r[0] == "CH")
        assert row[2] == "10"

    def test_serp_gl_encoded(self):
        rows = _build_rows(_MINIMAL_HINTS, _MINIMAL_SERP)
        row = next(r for r in rows if r[1] == "gl" and r[0] == "CH")
        assert row[2] == "ch"


# ── Parse rows ───────────────────────────────────────────────────────────────

class TestParseRows:

    def test_empty_rows_returns_defaults(self):
        hints, serp = _parse_rows([], COUNTRY_SEARCH_HINTS, COUNTRY_SERP_PARAMS)
        assert hints["CH"]["min_searches"] == COUNTRY_SEARCH_HINTS["CH"]["min_searches"]

    def test_min_searches_override(self):
        hints, _ = _parse_rows([["CH", "min_searches", "99"]], COUNTRY_SEARCH_HINTS, COUNTRY_SERP_PARAMS)
        assert hints["CH"]["min_searches"] == 99

    def test_missing_key_falls_back_to_default(self):
        hints, _ = _parse_rows([["CH", "min_searches", "99"]], COUNTRY_SEARCH_HINTS, COUNTRY_SERP_PARAMS)
        assert hints["CH"]["target_companies"] == COUNTRY_SEARCH_HINTS["CH"]["target_companies"]

    def test_list_key_decoded(self):
        hints, _ = _parse_rows([["CH", "cities", "Zürich\nBasel"]], COUNTRY_SEARCH_HINTS, COUNTRY_SERP_PARAMS)
        assert hints["CH"]["cities"] == ["Zürich", "Basel"]

    def test_serp_key_goes_to_serp_not_hints(self):
        hints, serp = _parse_rows([["CH", "gl", "xx"]], COUNTRY_SEARCH_HINTS, COUNTRY_SERP_PARAMS)
        assert serp["CH"]["gl"] == "xx"
        assert "gl" not in hints["CH"]

    def test_unknown_country_ignored(self):
        hints, serp = _parse_rows([["XX", "min_searches", "5"]], COUNTRY_SEARCH_HINTS, COUNTRY_SERP_PARAMS)
        assert "XX" not in hints

    def test_tld_override(self):
        hints, _ = _parse_rows([["DE", "tld", "com"]], COUNTRY_SEARCH_HINTS, COUNTRY_SERP_PARAMS)
        assert hints["DE"]["tld"] == "com"

    def test_invalid_int_keeps_default(self):
        hints, _ = _parse_rows([["CH", "min_searches", "not_a_number"]], COUNTRY_SEARCH_HINTS, COUNTRY_SERP_PARAMS)
        assert hints["CH"]["min_searches"] == COUNTRY_SEARCH_HINTS["CH"]["min_searches"]

    def test_country_lowercased_in_sheet_normalised(self):
        hints, _ = _parse_rows([["ch", "min_searches", "77"]], COUNTRY_SEARCH_HINTS, COUNTRY_SERP_PARAMS)
        assert hints["CH"]["min_searches"] == 77


# ── load_sheet_config integration ────────────────────────────────────────────

class TestLoadSheetConfig:

    def test_returns_defaults_when_tab_empty(self):
        hints, serp = _call_load(rows=[["country", "key", "value"]])
        assert hints["CH"]["min_searches"] == COUNTRY_SEARCH_HINTS["CH"]["min_searches"]

    def test_override_applied(self):
        rows = [
            ["country", "key", "value"],
            ["CH", "min_searches", "77"],
        ]
        hints, _ = _call_load(rows)
        assert hints["CH"]["min_searches"] == 77

    def test_unrelated_country_unchanged(self):
        rows = [["country", "key", "value"], ["CH", "min_searches", "77"]]
        hints, _ = _call_load(rows)
        assert hints["DE"]["min_searches"] == COUNTRY_SEARCH_HINTS["DE"]["min_searches"]

    def test_auto_create_when_tab_missing(self, capsys):
        svc = _make_service(rows=[], tab_exists=False)
        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("agents.sheet_config.Credentials.from_service_account_file"),
            patch("agents.sheet_config.build", return_value=svc),
        ):
            hints, serp = load_sheet_config(
                "SHEET_ID", "creds.json", COUNTRY_SEARCH_HINTS, COUNTRY_SERP_PARAMS
            )
        # batchUpdate (addSheet) must have been called
        svc.spreadsheets.return_value.batchUpdate.assert_called_once()
        # values().update() must have been called to write defaults
        svc.spreadsheets.return_value.values.return_value.update.assert_called_once()
        # returned hints equal defaults
        assert hints["CH"]["tld"] == "ch"

    def test_auto_create_writes_28_rows(self):
        svc = _make_service(rows=[], tab_exists=False)
        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("agents.sheet_config.Credentials.from_service_account_file"),
            patch("agents.sheet_config.build", return_value=svc),
        ):
            load_sheet_config("SHEET_ID", "creds.json", COUNTRY_SEARCH_HINTS, COUNTRY_SERP_PARAMS)
        call_kwargs = svc.spreadsheets.return_value.values.return_value.update.call_args.kwargs
        written_rows = call_kwargs["body"]["values"]
        # header + n_countries × 9 keys
        from agents.it_search_agent import COUNTRY_SEARCH_HINTS as _H
        assert len(written_rows) == 1 + len(_H) * 9

    def test_auto_create_encodes_lists_with_newlines(self):
        svc = _make_service(rows=[], tab_exists=False)
        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("agents.sheet_config.Credentials.from_service_account_file"),
            patch("agents.sheet_config.build", return_value=svc),
        ):
            load_sheet_config("SHEET_ID", "creds.json", COUNTRY_SEARCH_HINTS, COUNTRY_SERP_PARAMS)
        call_kwargs = svc.spreadsheets.return_value.values.return_value.update.call_args.kwargs
        written_rows = call_kwargs["body"]["values"]
        cities_rows = [r for r in written_rows if r[1] == "cities"]
        assert all("\n" in r[2] or r[2] == "" for r in cities_rows)

    def test_credentials_file_passed_to_google(self):
        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("agents.sheet_config.Credentials.from_service_account_file") as mock_creds,
            patch("agents.sheet_config.build", return_value=_make_service([])),
        ):
            load_sheet_config("SHEET_ID", "my_creds.json", COUNTRY_SEARCH_HINTS, COUNTRY_SERP_PARAMS)
        mock_creds.assert_called_once_with("my_creds.json", scopes=["https://www.googleapis.com/auth/spreadsheets"])

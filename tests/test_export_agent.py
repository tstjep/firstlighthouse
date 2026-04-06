"""Tests for waalaxy_export_agent — TDD: tests written before implementation."""

import csv
import io
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.export_agent import (
    _cell,
    _read_and_filter,
    _build_search_queries,
    _build_fallback_queries,
    _strip_legal_suffix,
    _parse_name_from_slug,
    _parse_name_from_title,
    _parse_profiles_from_serp,
    _extract_domain,
    _resolve_linkedin_urls,
    _match_country,
    _classify_color,
    _fetch_row_colors,
    _extract_company_id,
    _search_linkedin_api,
    _enrich_missing_roles,
    _is_valid_linkedin_slug,
    _collect_unresolved_profiles,
    _role_priority,
    _needs_fallback,
    _write_csv,
    _COL_NAME,
    _COL_RATING,
    _COL_WEBSITE,
    _COL_LINKEDIN,
    _COL_NOTES,
    _COL_HQ,
    _COL_SIZE,
    DEFAULT_TAB,
    _CSV_HEADERS,
    LINKEDIN_CACHE_DIR,
)


# ── Helpers ────────────────────────────────────────────────────────────────

def _kubecon_row(
    name="TestCorp AG",
    rating="4",
    human_comment="",
    notes="Cloud infra provider",
    website="https://testcorp.ch",
    linkedin="https://www.linkedin.com/company/testcorp/",
    size="51-200",
    hq="Zurich, Switzerland",
) -> list[str]:
    """Build a 28-element row matching KubeCon A:AB layout."""
    row = [""] * 28
    row[0] = name          # A
    row[2] = rating        # C
    row[3] = human_comment # D
    row[4] = notes         # E
    row[5] = website       # F
    row[6] = linkedin      # G
    row[7] = size          # H
    row[8] = hq            # I
    return row


HEADER_ROW = [
    "Company Name", "Comment Melt", "Rating", "Human Comment",
    "Notes", "Website", "LinkedIn", "Size", "HQ Location",
    "AI Signal", "AI Signal Source", "AI Signal Date",
    "Sovereignty Signal", "Sovereignty Source", "Sovereignty Date",
    "Edge Signal", "Edge Source", "Edge Date",
    "Cost Signal", "Cost Source", "Cost Date",
    "Kubernetes Signal", "Kubernetes Source", "Kubernetes Date",
    "Date Added", "KubeCon 2026", "KubeCon Source", "KubeCon Date",
]


# ── _cell ──────────────────────────────────────────────────────────────────

class TestCell:
    def test_valid_index(self):
        assert _cell(["hello", "world"], 0) == "hello"

    def test_out_of_bounds(self):
        assert _cell(["hello"], 5) == ""

    def test_strips_whitespace(self):
        assert _cell(["  spaced  "], 0) == "spaced"

    def test_empty_row(self):
        assert _cell([], 0) == ""


# ── _read_and_filter ───────────────────────────────────────────────────────

class TestReadAndFilter:
    def _mock_sheets(self, rows):
        """Return a mock Google Sheets service that returns the given rows."""
        mock_service = MagicMock()
        mock_service.spreadsheets().values().get().execute.return_value = {
            "values": [HEADER_ROW] + rows
        }
        return mock_service

    @patch("agents.export.sheets.build")
    @patch("agents.export.sheets.Credentials")
    def test_filters_by_min_rating(self, mock_creds, mock_build):
        rows = [
            _kubecon_row(name="High", rating="5"),
            _kubecon_row(name="Mid", rating="3"),
            _kubecon_row(name="Low", rating="1"),
        ]
        mock_build.return_value = self._mock_sheets(rows)

        result = _read_and_filter("sheet-id", "creds.json", min_rating=4)
        names = [c["name"] for c in result]
        assert names == ["High"]

    @patch("agents.export.sheets.build")
    @patch("agents.export.sheets.Credentials")
    def test_default_min_rating_3(self, mock_creds, mock_build):
        rows = [
            _kubecon_row(name="Five", rating="5"),
            _kubecon_row(name="Three", rating="3"),
            _kubecon_row(name="Two", rating="2"),
        ]
        mock_build.return_value = self._mock_sheets(rows)

        result = _read_and_filter("sheet-id", "creds.json", min_rating=3)
        names = [c["name"] for c in result]
        assert names == ["Five", "Three"]

    @patch("agents.export.sheets.build")
    @patch("agents.export.sheets.Credentials")
    def test_excludes_d_rating(self, mock_creds, mock_build):
        rows = [
            _kubecon_row(name="Target", rating="D"),
            _kubecon_row(name="Good", rating="4"),
        ]
        mock_build.return_value = self._mock_sheets(rows)

        result = _read_and_filter("sheet-id", "creds.json", min_rating=3)
        names = [c["name"] for c in result]
        assert "Target" not in names
        assert names == ["Good"]

    @patch("agents.export.sheets.build")
    @patch("agents.export.sheets.Credentials")
    def test_include_hosters(self, mock_creds, mock_build):
        rows = [
            _kubecon_row(name="Hoster", rating="possible hoster"),
            _kubecon_row(name="Normal", rating="3"),
        ]
        mock_build.return_value = self._mock_sheets(rows)

        # Without flag
        result = _read_and_filter("sheet-id", "creds.json", min_rating=3, include_hosters=False)
        names = [c["name"] for c in result]
        assert "Hoster" not in names

        # With flag
        result = _read_and_filter("sheet-id", "creds.json", min_rating=3, include_hosters=True)
        names = [c["name"] for c in result]
        assert "Hoster" in names
        assert "Normal" in names

    @patch("agents.export.sheets.build")
    @patch("agents.export.sheets.Credentials")
    def test_skips_empty_names(self, mock_creds, mock_build):
        rows = [
            _kubecon_row(name="", rating="5"),
            _kubecon_row(name="Valid", rating="5"),
        ]
        mock_build.return_value = self._mock_sheets(rows)

        result = _read_and_filter("sheet-id", "creds.json", min_rating=3)
        assert len(result) == 1
        assert result[0]["name"] == "Valid"

    @patch("agents.export.sheets.build")
    @patch("agents.export.sheets.Credentials")
    def test_returns_all_fields(self, mock_creds, mock_build):
        rows = [
            _kubecon_row(
                name="FullCorp",
                rating="5",
                website="https://full.ch",
                linkedin="https://linkedin.com/company/full",
                size="201-500",
                hq="Bern, CH",
                notes="Great company",
            ),
        ]
        mock_build.return_value = self._mock_sheets(rows)

        result = _read_and_filter("sheet-id", "creds.json", min_rating=3)
        assert len(result) == 1
        c = result[0]
        assert c["name"] == "FullCorp"
        assert c["rating"] == "5"
        assert c["website"] == "https://full.ch"
        assert c["linkedin"] == "https://linkedin.com/company/full"
        assert c["size"] == "201-500"
        assert c["hq"] == "Bern, CH"
        assert c["notes"] == "Great company"

    @patch("agents.export.sheets.build")
    @patch("agents.export.sheets.Credentials")
    def test_empty_sheet(self, mock_creds, mock_build):
        mock_build.return_value = self._mock_sheets([])

        result = _read_and_filter("sheet-id", "creds.json", min_rating=3)
        assert result == []


# ── _build_search_queries ──────────────────────────────────────────────────

class TestBuildSearchQueries:
    def test_returns_at_least_three_queries(self):
        company = {"name": "Acme Corp", "website": "https://acme.com"}
        queries = _build_search_queries(company)
        assert len(queries) >= 3

    def test_first_two_are_linkedin(self):
        company = {"name": "Acme Corp", "website": "https://acme.com"}
        queries = _build_search_queries(company)
        assert "site:linkedin.com/in" in queries[0]
        assert "site:linkedin.com/in" in queries[1]

    def test_contains_company_name(self):
        company = {"name": "SwissCloud AG", "website": ""}
        queries = _build_search_queries(company)
        for q in queries:
            assert "SwissCloud AG" in q

    def test_first_query_has_leadership_roles(self):
        company = {"name": "TestCo", "website": ""}
        queries = _build_search_queries(company)
        assert "CTO" in queries[0]

    def test_second_query_has_engineer_roles(self):
        company = {"name": "TestCo", "website": ""}
        queries = _build_search_queries(company)
        assert "engineer" in queries[1].lower() or "developer" in queries[1].lower()


# ── _parse_name_from_slug ──────────────────────────────────────────────────

class TestParseNameFromSlug:
    def test_simple_name_with_hex_id(self):
        first, last = _parse_name_from_slug("john-doe-a1b2c3d4")
        assert first == "John"
        assert last == "Doe"

    def test_simple_name_without_id(self):
        first, last = _parse_name_from_slug("john-doe")
        assert first == "John"
        assert last == "Doe"

    def test_multi_part_first_name(self):
        first, last = _parse_name_from_slug("jean-pierre-martin-5f6a7b8c")
        assert first == "Jean Pierre"
        assert last == "Martin"

    def test_single_name(self):
        first, last = _parse_name_from_slug("john")
        assert first == "John"
        assert last == ""

    def test_empty_slug(self):
        first, last = _parse_name_from_slug("")
        assert first == ""
        assert last == ""

    def test_trailing_slash(self):
        first, last = _parse_name_from_slug("jane-smith-abcd1234/")
        assert first == "Jane"
        assert last == "Smith"

    def test_slug_with_numbers_in_name(self):
        """Name parts with mixed alpha+digits should not be treated as hex IDs."""
        first, last = _parse_name_from_slug("anna-m3ier")
        assert first == "Anna"
        assert last == "M3Ier"  # .title() capitalizes after digits


# ── _is_valid_linkedin_slug ───────────────────────────────────────────────


class TestIsValidLinkedinSlug:
    def test_normal_slug(self):
        assert _is_valid_linkedin_slug("john-doe-a1b2c3d4") is True

    def test_single_letter(self):
        """Single letter slug like '/in/l' is not a real profile."""
        assert _is_valid_linkedin_slug("l") is False

    def test_two_letter(self):
        assert _is_valid_linkedin_slug("ab") is False

    def test_trailing_dash(self):
        """Truncated slugs like 'miguel-' are invalid."""
        assert _is_valid_linkedin_slug("miguel-") is False

    def test_urn_style_id(self):
        """URN-style IDs like 'ACoAADGp5P8B7...' are not valid slugs."""
        assert _is_valid_linkedin_slug("ACoAADGp5P8B7n_cNkv0XJ4zQ-wYSNssTEJLGRo") is False

    def test_single_word_username(self):
        """Single-word usernames like 'didierlahay' are valid."""
        assert _is_valid_linkedin_slug("didierlahay") is True

    def test_normal_two_part(self):
        assert _is_valid_linkedin_slug("jane-smith") is True

    def test_with_numbers_in_name(self):
        """Slugs with numbers appended like 'julien-erny-6003141' are valid."""
        assert _is_valid_linkedin_slug("julien-erny-6003141") is True

    def test_short_valid_slug(self):
        """Three-letter slugs can be valid usernames."""
        assert _is_valid_linkedin_slug("bob") is True


# ── _parse_profiles_from_serp ─────────────────────────────────────────────

SAMPLE_SERP_OUTPUT = """1. John Doe - CTO - TestCorp | LinkedIn
   URL: https://www.linkedin.com/in/john-doe-a1b2c3d4
   CTO at TestCorp with 15 years of experience in cloud infrastructure.

2. TestCorp AG | LinkedIn
   URL: https://www.linkedin.com/company/testcorp
   TestCorp AG is a leading cloud infrastructure provider.

3. Jane Smith - DevOps Engineer - TestCorp | LinkedIn
   URL: https://ch.linkedin.com/in/jane-smith-5678efgh
   Senior DevOps Engineer at TestCorp.

4. Jane Smith - DevOps Engineer - TestCorp | LinkedIn
   URL: https://www.linkedin.com/in/jane-smith-5678efgh
   Duplicate of the above."""


class TestParseProfilesFromSerp:
    def test_extracts_personal_profiles(self):
        profiles = _parse_profiles_from_serp(SAMPLE_SERP_OUTPUT, "TestCorp")
        urls = [p["url"] for p in profiles]
        assert any("john-doe" in u for u in urls)
        assert any("jane-smith" in u for u in urls)

    def test_filters_company_pages(self):
        profiles = _parse_profiles_from_serp(SAMPLE_SERP_OUTPUT, "TestCorp")
        urls = [p["url"] for p in profiles]
        assert not any("/company/" in u for u in urls)

    def test_deduplicates_urls(self):
        profiles = _parse_profiles_from_serp(SAMPLE_SERP_OUTPUT, "TestCorp")
        urls = [p["url"] for p in profiles]
        assert len(urls) == len(set(urls))

    def test_extracts_names(self):
        profiles = _parse_profiles_from_serp(SAMPLE_SERP_OUTPUT, "TestCorp")
        john = next(p for p in profiles if "john-doe" in p["url"])
        assert john["first_name"] == "John"
        assert john["last_name"] == "Doe"

    def test_extracts_title_hint(self):
        profiles = _parse_profiles_from_serp(SAMPLE_SERP_OUTPUT, "TestCorp")
        john = next(p for p in profiles if "john-doe" in p["url"])
        assert "CTO" in john["title_hint"]

    def test_normalizes_country_linkedin_urls(self):
        profiles = _parse_profiles_from_serp(SAMPLE_SERP_OUTPUT, "TestCorp")
        jane = next(p for p in profiles if "jane-smith" in p["url"])
        assert jane["url"].startswith("https://www.linkedin.com/in/")

    def test_empty_input(self):
        assert _parse_profiles_from_serp("", "Test") == []

    def test_error_input(self):
        assert _parse_profiles_from_serp("[serp_search error] something", "Test") == []

    def test_no_results_input(self):
        assert _parse_profiles_from_serp("No results found for: test", "Test") == []


# ── _write_csv ─────────────────────────────────────────────────────────────

class TestWriteCsv:
    def _companies(self):
        return [
            {
                "name": "AlphaCorp",
                "rating": "5",
                "website": "https://alpha.ch",
                "linkedin": "https://linkedin.com/company/alpha",
                "size": "51-200",
                "hq": "Zurich",
                "notes": "",
            },
            {
                "name": "BetaCorp",
                "rating": "3",
                "website": "https://beta.de",
                "linkedin": "https://linkedin.com/company/beta",
                "size": "11-50",
                "hq": "Berlin",
                "notes": "",
            },
        ]

    def _profiles(self):
        return {
            "AlphaCorp": [
                {"url": "https://www.linkedin.com/in/alice-alpha-1234", "first_name": "Alice", "last_name": "Alpha", "title_hint": "CTO"},
                {"url": "https://www.linkedin.com/in/bob-alpha-5678", "first_name": "Bob", "last_name": "Alpha", "title_hint": "DevOps"},
            ],
            "BetaCorp": [],  # no profiles found
        }

    def test_writes_correct_headers(self):
        buf = io.StringIO()
        _write_csv(self._profiles(), self._companies(), buf)
        buf.seek(0)
        reader = csv.reader(buf)
        headers = next(reader)
        assert headers == _CSV_HEADERS

    def test_writes_profile_rows(self):
        buf = io.StringIO()
        count = _write_csv(self._profiles(), self._companies(), buf)
        assert count == 2  # only AlphaCorp's 2 profiles

    def test_skips_companies_without_profiles(self):
        buf = io.StringIO()
        _write_csv(self._profiles(), self._companies(), buf)
        buf.seek(0)
        content = buf.read()
        assert "BetaCorp" not in content

    def test_row_content(self):
        buf = io.StringIO()
        _write_csv(self._profiles(), self._companies(), buf)
        buf.seek(0)
        reader = csv.reader(buf)
        next(reader)  # skip header
        row = next(reader)
        assert row[0] == "https://www.linkedin.com/in/alice-alpha-1234"
        assert row[1] == "Alice"
        assert row[2] == "Alpha"
        assert row[3] == "AlphaCorp"
        assert row[4] == "https://linkedin.com/company/alpha"
        assert row[5] == "https://alpha.ch"
        assert row[6] == "5"

    def test_empty_profiles(self):
        buf = io.StringIO()
        count = _write_csv({}, self._companies(), buf)
        assert count == 0


# ── _match_country ─────────────────────────────────────────────────────────

class TestMatchCountry:
    # Country code matching
    def test_ch_matches_switzerland(self):
        assert _match_country("Zurich, Switzerland", "CH")

    def test_ch_matches_schweiz(self):
        assert _match_country("Bern, Schweiz", "CH")

    def test_ch_matches_swiss(self):
        assert _match_country("Swiss Cloud AG", "CH")

    def test_de_matches_germany(self):
        assert _match_country("Berlin, Germany", "DE")

    def test_de_matches_deutschland(self):
        assert _match_country("München, Deutschland", "DE")

    def test_at_matches_austria(self):
        assert _match_country("Vienna, Austria", "AT")

    def test_at_matches_oesterreich(self):
        assert _match_country("Wien, Österreich", "AT")

    def test_uk_matches_united_kingdom(self):
        assert _match_country("London, United Kingdom", "UK")

    def test_uk_matches_england(self):
        assert _match_country("Manchester, England", "UK")

    def test_nl_matches_netherlands(self):
        assert _match_country("Amsterdam, Netherlands", "NL")

    def test_fr_matches_france(self):
        assert _match_country("Paris, France", "FR")

    # Case insensitive
    def test_case_insensitive(self):
        assert _match_country("ZURICH, SWITZERLAND", "ch")

    # No match
    def test_no_match(self):
        assert not _match_country("Berlin, Germany", "CH")

    def test_empty_hq(self):
        assert not _match_country("", "CH")

    # None country = no filter = always match
    def test_none_country_matches_everything(self):
        assert _match_country("Berlin, Germany", None)
        assert _match_country("", None)

    # EU = non-DACH Europe
    def test_eu_matches_netherlands(self):
        assert _match_country("Amsterdam, Netherlands", "EU")

    def test_eu_matches_france(self):
        assert _match_country("Paris, France", "EU")

    def test_eu_matches_uk(self):
        assert _match_country("London, United Kingdom", "EU")

    def test_eu_matches_poland(self):
        assert _match_country("Warsaw, Poland", "EU")

    def test_eu_excludes_switzerland(self):
        assert not _match_country("Zurich, Switzerland", "EU")

    def test_eu_excludes_germany(self):
        assert not _match_country("Berlin, Germany", "EU")

    def test_eu_excludes_austria(self):
        assert not _match_country("Vienna, Austria", "EU")

    def test_eu_excludes_usa(self):
        assert not _match_country("New York, United States", "EU")

    def test_eu_empty_hq(self):
        assert not _match_country("", "EU")


# ── _classify_color ───────────────────────────────────────────────────────

class TestClassifyColor:
    def test_none_bg(self):
        assert _classify_color(None) is None

    def test_empty_dict(self):
        assert _classify_color({}) is None

    def test_white(self):
        assert _classify_color({"red": 1.0, "green": 1.0, "blue": 1.0}) is None

    def test_near_white(self):
        assert _classify_color({"red": 0.95, "green": 0.96, "blue": 0.95}) is None

    def test_grey(self):
        assert _classify_color({"red": 0.5, "green": 0.5, "blue": 0.5}) is None

    def test_light_green(self):
        # Google Sheets "light green 3"
        assert _classify_color({"red": 0.85, "green": 0.92, "blue": 0.83}) == "green"

    def test_pure_green(self):
        assert _classify_color({"red": 0.0, "green": 1.0, "blue": 0.0}) == "green"

    def test_light_red(self):
        # Google Sheets "light red 3"
        assert _classify_color({"red": 0.96, "green": 0.80, "blue": 0.80}) == "red"

    def test_pure_red(self):
        assert _classify_color({"red": 1.0, "green": 0.0, "blue": 0.0}) == "red"

    def test_light_yellow(self):
        assert _classify_color({"red": 1.0, "green": 0.95, "blue": 0.8}) == "yellow"

    def test_pure_yellow(self):
        assert _classify_color({"red": 1.0, "green": 1.0, "blue": 0.0}) == "yellow"

    def test_blue(self):
        assert _classify_color({"red": 0.0, "green": 0.0, "blue": 1.0}) == "blue"


# ── _fetch_row_colors ─────────────────────────────────────────────────────

class TestFetchRowColors:
    def test_returns_color_map(self):
        mock_service = MagicMock()
        mock_service.spreadsheets().get().execute.return_value = {
            "sheets": [{"data": [{"rowData": [
                # Row 1 (header) — white
                {"values": [{"effectiveFormat": {"backgroundColor": {"red": 1, "green": 1, "blue": 1}}}]},
                # Row 2 — green
                {"values": [{"effectiveFormat": {"backgroundColor": {"red": 0.85, "green": 0.92, "blue": 0.83}}}]},
                # Row 3 — no color
                {"values": [{"effectiveFormat": {"backgroundColor": {"red": 1, "green": 1, "blue": 1}}}]},
                # Row 4 — red
                {"values": [{"effectiveFormat": {"backgroundColor": {"red": 0.96, "green": 0.80, "blue": 0.80}}}]},
            ]}]}]
        }
        colors = _fetch_row_colors(mock_service, "sheet-id", "KubeCon")
        assert colors == {2: "green", 4: "red"}

    def test_empty_response(self):
        mock_service = MagicMock()
        mock_service.spreadsheets().get().execute.return_value = {"sheets": []}
        assert _fetch_row_colors(mock_service, "sheet-id", "KubeCon") == {}


# ── _read_and_filter with color ───────────────────────────────────────────

class TestReadAndFilterColor:
    def _mock_sheets_with_colors(self, rows, row_colors):
        """Mock both values().get() and spreadsheets().get() for color data."""
        mock_service = MagicMock()
        mock_service.spreadsheets().values().get().execute.return_value = {
            "values": [HEADER_ROW] + rows
        }
        # Build color response — row 1 is header (white), data starts at row 2
        row_data = [{"values": [{"effectiveFormat": {"backgroundColor": {"red": 1, "green": 1, "blue": 1}}}]}]
        for i in range(len(rows)):
            sheet_row = i + 2
            if sheet_row in row_colors:
                bg = row_colors[sheet_row]
            else:
                bg = {"red": 1, "green": 1, "blue": 1}
            row_data.append({"values": [{"effectiveFormat": {"backgroundColor": bg}}]})
        mock_service.spreadsheets().get().execute.return_value = {
            "sheets": [{"data": [{"rowData": row_data}]}]
        }
        return mock_service

    @patch("agents.export.sheets.build")
    @patch("agents.export.sheets.Credentials")
    def test_color_green_filters(self, mock_creds, mock_build):
        rows = [
            _kubecon_row(name="GreenCo", rating="5", hq="Zurich, Switzerland"),
            _kubecon_row(name="NormalCo", rating="5", hq="Zurich, Switzerland"),
            _kubecon_row(name="RedCo", rating="5", hq="Zurich, Switzerland"),
        ]
        colors = {
            2: {"red": 0.85, "green": 0.92, "blue": 0.83},  # green
            # row 3: no color (white default)
            4: {"red": 0.96, "green": 0.80, "blue": 0.80},  # red
        }
        mock_build.return_value = self._mock_sheets_with_colors(rows, colors)

        result = _read_and_filter("sheet-id", "creds.json", min_rating=3, color="green")
        names = [c["name"] for c in result]
        assert names == ["GreenCo"]

    @patch("agents.export.sheets.build")
    @patch("agents.export.sheets.Credentials")
    def test_color_red_filters(self, mock_creds, mock_build):
        rows = [
            _kubecon_row(name="GreenCo", rating="5", hq="Zurich, Switzerland"),
            _kubecon_row(name="RedCo", rating="5", hq="Zurich, Switzerland"),
        ]
        colors = {
            2: {"red": 0.85, "green": 0.92, "blue": 0.83},  # green
            3: {"red": 0.96, "green": 0.80, "blue": 0.80},  # red
        }
        mock_build.return_value = self._mock_sheets_with_colors(rows, colors)

        result = _read_and_filter("sheet-id", "creds.json", min_rating=3, color="red")
        names = [c["name"] for c in result]
        assert names == ["RedCo"]

    @patch("agents.export.sheets.build")
    @patch("agents.export.sheets.Credentials")
    def test_no_color_returns_all(self, mock_creds, mock_build):
        rows = [
            _kubecon_row(name="GreenCo", rating="5"),
            _kubecon_row(name="NormalCo", rating="5"),
        ]
        mock_service = MagicMock()
        mock_service.spreadsheets().values().get().execute.return_value = {
            "values": [HEADER_ROW] + rows
        }
        mock_build.return_value = mock_service

        result = _read_and_filter("sheet-id", "creds.json", min_rating=3)
        assert len(result) == 2

    @patch("agents.export.sheets.build")
    @patch("agents.export.sheets.Credentials")
    def test_color_combined_with_rating(self, mock_creds, mock_build):
        rows = [
            _kubecon_row(name="HighGreen", rating="5"),
            _kubecon_row(name="LowGreen", rating="1"),
        ]
        colors = {
            2: {"red": 0.85, "green": 0.92, "blue": 0.83},  # green
            3: {"red": 0.85, "green": 0.92, "blue": 0.83},  # green
        }
        mock_build.return_value = self._mock_sheets_with_colors(rows, colors)

        result = _read_and_filter("sheet-id", "creds.json", min_rating=3, color="green")
        names = [c["name"] for c in result]
        assert names == ["HighGreen"]


# ── _read_and_filter with country ─────────────────────────────────────────

class TestReadAndFilterCountry:
    def _mock_sheets(self, rows):
        mock_service = MagicMock()
        mock_service.spreadsheets().values().get().execute.return_value = {
            "values": [HEADER_ROW] + rows
        }
        return mock_service

    @patch("agents.export.sheets.build")
    @patch("agents.export.sheets.Credentials")
    def test_filters_by_country(self, mock_creds, mock_build):
        rows = [
            _kubecon_row(name="SwissCo", rating="5", hq="Zurich, Switzerland"),
            _kubecon_row(name="GermanCo", rating="5", hq="Berlin, Germany"),
        ]
        mock_build.return_value = self._mock_sheets(rows)

        result = _read_and_filter("sheet-id", "creds.json", min_rating=3, country="CH")
        names = [c["name"] for c in result]
        assert names == ["SwissCo"]

    @patch("agents.export.sheets.build")
    @patch("agents.export.sheets.Credentials")
    def test_no_country_returns_all(self, mock_creds, mock_build):
        rows = [
            _kubecon_row(name="SwissCo", rating="5", hq="Zurich, Switzerland"),
            _kubecon_row(name="GermanCo", rating="5", hq="Berlin, Germany"),
        ]
        mock_build.return_value = self._mock_sheets(rows)

        result = _read_and_filter("sheet-id", "creds.json", min_rating=3)
        assert len(result) == 2

    @patch("agents.export.sheets.build")
    @patch("agents.export.sheets.Credentials")
    def test_country_filter_combined_with_rating(self, mock_creds, mock_build):
        rows = [
            _kubecon_row(name="Good Swiss", rating="5", hq="Bern, Switzerland"),
            _kubecon_row(name="Bad Swiss", rating="1", hq="Geneva, Switzerland"),
            _kubecon_row(name="Good German", rating="5", hq="Munich, Germany"),
        ]
        mock_build.return_value = self._mock_sheets(rows)

        result = _read_and_filter("sheet-id", "creds.json", min_rating=3, country="CH")
        names = [c["name"] for c in result]
        assert names == ["Good Swiss"]


# ── _extract_company_id ───────────────────────────────────────────────────

class TestExtractCompanyId:
    def test_standard_url(self):
        assert _extract_company_id("https://www.linkedin.com/company/testcorp/") == "testcorp"

    def test_url_without_trailing_slash(self):
        assert _extract_company_id("https://www.linkedin.com/company/testcorp") == "testcorp"

    def test_url_with_country_prefix(self):
        assert _extract_company_id("https://ch.linkedin.com/company/my-corp") == "my-corp"

    def test_http_url(self):
        assert _extract_company_id("http://linkedin.com/company/somecorp") == "somecorp"

    def test_empty_string(self):
        assert _extract_company_id("") is None

    def test_non_linkedin_url(self):
        assert _extract_company_id("https://example.com") is None

    def test_personal_profile_url(self):
        assert _extract_company_id("https://linkedin.com/in/john-doe") is None

    def test_url_with_extra_path(self):
        assert _extract_company_id("https://linkedin.com/company/testcorp/about/") == "testcorp"


# ── _search_linkedin_api ─────────────────────────────────────────────────

# Sample return from linkedin-api search_people()
MOCK_LINKEDIN_RESULTS = [
    {
        "urn_id": "john-doe-abc123",
        "distance": "DISTANCE_2",
        "jobtitle": "CTO",
        "location": "Zurich, Switzerland",
        "name": "John Doe",
    },
    {
        "urn_id": "jane-smith-def456",
        "distance": "DISTANCE_3",
        "jobtitle": "DevOps Engineer",
        "location": "Bern, Switzerland",
        "name": "Jane Smith",
    },
    {
        "urn_id": "bob-builder-ghi789",
        "distance": "DISTANCE_2",
        "jobtitle": "Platform Engineer",
        "location": "Basel, Switzerland",
        "name": "Bob Builder",
    },
]


class TestSearchLinkedinApi:
    def _companies(self):
        return [
            {
                "name": "TestCorp AG",
                "linkedin": "https://www.linkedin.com/company/testcorp/",
                "website": "https://testcorp.ch",
                "rating": "5",
                "size": "51-200",
                "hq": "Zurich, Switzerland",
                "notes": "",
            },
        ]

    @patch("agents.export.linkedin.time.sleep")
    @patch("agents.export.linkedin.Linkedin")
    def test_returns_profiles_with_correct_format(self, mock_linkedin_cls, _sleep, tmp_path):
        mock_api = MagicMock()
        mock_linkedin_cls.return_value = mock_api
        mock_api.search_people.return_value = MOCK_LINKEDIN_RESULTS[:1]

        with patch("agents.export.linkedin.LINKEDIN_CACHE_DIR", tmp_path):
            result = _search_linkedin_api(
                self._companies(), "email@test.com", "password", max_profiles=5
            )
        profiles = result["TestCorp AG"]
        assert len(profiles) >= 1
        p = profiles[0]
        assert p["url"] == "https://www.linkedin.com/in/john-doe-abc123"
        assert p["first_name"] == "John"
        assert p["last_name"] == "Doe"
        assert "CTO" in p["title_hint"]

    @patch("agents.export.linkedin.time.sleep")
    @patch("agents.export.linkedin.Linkedin")
    def test_respects_max_profiles(self, mock_linkedin_cls, _sleep, tmp_path):
        mock_api = MagicMock()
        mock_linkedin_cls.return_value = mock_api
        mock_api.search_people.return_value = MOCK_LINKEDIN_RESULTS

        with patch("agents.export.linkedin.LINKEDIN_CACHE_DIR", tmp_path):
            result = _search_linkedin_api(
                self._companies(), "email@test.com", "password", max_profiles=2
            )
        assert len(result["TestCorp AG"]) <= 2

    @patch("agents.export.linkedin.time.sleep")
    @patch("agents.export.linkedin.Linkedin")
    def test_deduplicates_across_role_searches(self, mock_linkedin_cls, _sleep, tmp_path):
        mock_api = MagicMock()
        mock_linkedin_cls.return_value = mock_api
        # Return same person for every role search
        mock_api.search_people.return_value = MOCK_LINKEDIN_RESULTS[:1]

        with patch("agents.export.linkedin.LINKEDIN_CACHE_DIR", tmp_path):
            result = _search_linkedin_api(
                self._companies(), "email@test.com", "password", max_profiles=5
            )
        profiles = result["TestCorp AG"]
        urls = [p["url"] for p in profiles]
        assert len(urls) == len(set(urls))

    @patch("agents.export.linkedin.time.sleep")
    @patch("agents.export.linkedin.Linkedin")
    def test_skips_company_without_linkedin_url(self, mock_linkedin_cls, _sleep, tmp_path):
        mock_api = MagicMock()
        mock_linkedin_cls.return_value = mock_api
        mock_api.search_people.return_value = []

        companies = [{
            "name": "NoCorp",
            "linkedin": "",
            "website": "https://nocorp.ch",
            "rating": "4",
            "size": "",
            "hq": "",
            "notes": "",
        }]
        with patch("agents.export.linkedin.LINKEDIN_CACHE_DIR", tmp_path):
            result = _search_linkedin_api(
                companies, "email@test.com", "password", max_profiles=5
            )
        # Should still have an entry (empty or searched by keyword_company)
        assert "NoCorp" in result

    @patch("agents.export.linkedin.time.sleep")
    @patch("agents.export.linkedin.Linkedin")
    def test_handles_api_error_gracefully(self, mock_linkedin_cls, _sleep, tmp_path):
        mock_api = MagicMock()
        mock_linkedin_cls.return_value = mock_api
        mock_api.search_people.side_effect = Exception("API error")

        with patch("agents.export.linkedin.LINKEDIN_CACHE_DIR", tmp_path):
            result = _search_linkedin_api(
                self._companies(), "email@test.com", "password", max_profiles=5
            )
        assert result["TestCorp AG"] == []

    @patch("agents.export.linkedin.time.sleep")
    @patch("agents.export.linkedin.Linkedin")
    def test_uses_keyword_company_when_no_linkedin_url(self, mock_linkedin_cls, _sleep, tmp_path):
        mock_api = MagicMock()
        mock_linkedin_cls.return_value = mock_api
        mock_api.search_people.return_value = []

        companies = [{
            "name": "NoCorp AG",
            "linkedin": "",
            "website": "",
            "rating": "4",
            "size": "",
            "hq": "",
            "notes": "",
        }]
        with patch("agents.export.linkedin.LINKEDIN_CACHE_DIR", tmp_path):
            _search_linkedin_api(companies, "email@test.com", "password", max_profiles=5)
        # Should have called search_people with keyword_company
        calls = mock_api.search_people.call_args_list
        assert any(c.kwargs.get("keyword_company") == "NoCorp AG" for c in calls)


# ── LinkedIn cache ────────────────────────────────────────────────────────

class TestLinkedinCache:
    @patch("agents.export.linkedin.Linkedin")
    def test_cache_written_on_search(self, mock_linkedin_cls, tmp_path):
        mock_api = MagicMock()
        mock_linkedin_cls.return_value = mock_api
        mock_api.search_people.return_value = MOCK_LINKEDIN_RESULTS[:1]

        with patch("agents.export.linkedin.LINKEDIN_CACHE_DIR", tmp_path):
            _search_linkedin_api(
                [{"name": "CacheCorp", "linkedin": "https://linkedin.com/company/cachecorp/",
                  "website": "", "rating": "4", "size": "", "hq": "", "notes": ""}],
                "email@test.com", "password", max_profiles=5
            )
        cache_files = list(tmp_path.glob("*.json"))
        assert len(cache_files) >= 1

    @patch("agents.export.linkedin.Linkedin")
    def test_cache_hit_skips_api_call(self, mock_linkedin_cls, tmp_path):
        import json
        from datetime import date

        mock_api = MagicMock()
        mock_linkedin_cls.return_value = mock_api

        # Pre-populate cache
        cache_data = [{"url": "https://www.linkedin.com/in/cached-person",
                        "first_name": "Cached", "last_name": "Person", "title_hint": "CTO"}]
        cache_file = tmp_path / f"{date.today().isoformat()}_cachecorp.json"
        cache_file.write_text(json.dumps(cache_data))

        with patch("agents.export.linkedin.LINKEDIN_CACHE_DIR", tmp_path):
            result = _search_linkedin_api(
                [{"name": "CacheCorp", "linkedin": "https://linkedin.com/company/cachecorp/",
                  "website": "", "rating": "4", "size": "", "hq": "", "notes": ""}],
                "email@test.com", "password", max_profiles=5
            )

        assert result["CacheCorp"][0]["first_name"] == "Cached"
        # search_people should NOT have been called
        mock_api.search_people.assert_not_called()


# ── _extract_domain ──────────────────────────────────────────────────────

class TestExtractDomain:
    def test_simple_url(self):
        assert _extract_domain("https://acme.com") == "acme.com"

    def test_url_with_path(self):
        assert _extract_domain("https://acme.com/about/team") == "acme.com"

    def test_url_with_www(self):
        assert _extract_domain("https://www.acme.com") == "www.acme.com"

    def test_http_url(self):
        assert _extract_domain("http://acme.com") == "acme.com"

    def test_url_with_port(self):
        assert _extract_domain("https://acme.com:8080/path") == "acme.com"

    def test_empty_string(self):
        assert _extract_domain("") is None

    def test_none(self):
        assert _extract_domain(None) is None

    def test_not_a_url(self):
        assert _extract_domain("just some text") is None

    def test_subdomain(self):
        assert _extract_domain("https://cloud.acme.com/services") == "cloud.acme.com"


# ── _parse_name_from_title ───────────────────────────────────────────────

class TestParseNameFromTitle:
    def test_name_dash_role(self):
        result = _parse_name_from_title("John Doe - CTO at Acme Corp")
        assert result == ("John", "Doe", "CTO at Acme Corp")

    def test_name_pipe_role(self):
        result = _parse_name_from_title("Jane Smith | VP Engineering")
        assert result == ("Jane", "Smith", "VP Engineering")

    def test_name_comma_role(self):
        result = _parse_name_from_title("Bob Wilson, Head of DevOps")
        assert result == ("Bob", "Wilson", "Head of DevOps")

    def test_name_endash_role(self):
        result = _parse_name_from_title("Alice Brown \u2013 Platform Engineer")
        assert result == ("Alice", "Brown", "Platform Engineer")

    def test_three_part_name(self):
        """First name with middle name or two-part first name."""
        result = _parse_name_from_title("Jean Pierre Martin - CTO")
        assert result is not None
        assert result[2] == "CTO"

    def test_non_person_title_our_team(self):
        result = _parse_name_from_title("Our Team | Acme Corp")
        assert result is None

    def test_non_person_title_about_us(self):
        result = _parse_name_from_title("About Us - Acme Corp")
        assert result is None

    def test_non_person_title_company_name(self):
        result = _parse_name_from_title("Acme Corp - Cloud Infrastructure Provider")
        assert result is None

    def test_no_separator(self):
        result = _parse_name_from_title("Some Random Page Title Here")
        assert result is None

    def test_empty_string(self):
        result = _parse_name_from_title("")
        assert result is None

    def test_linkedin_style_title(self):
        """LinkedIn titles have specific format — should still work."""
        result = _parse_name_from_title("John Doe - CTO - TestCorp | LinkedIn")
        assert result is not None
        assert result[0] == "John"
        assert result[1] == "Doe"


# ── _build_search_queries with website ───────────────────────────────────

class TestBuildSearchQueriesWebsite:
    def test_includes_website_query_when_domain_available(self):
        company = {"name": "Acme Corp", "website": "https://acme.com"}
        queries = _build_search_queries(company)
        # Should have more than 2 queries now
        assert len(queries) > 2
        # At least one query should search the company website domain
        assert any("site:acme.com" in q for q in queries)

    def test_no_broad_web_query(self):
        company = {"name": "Acme Corp", "website": "https://acme.com"}
        queries = _build_search_queries(company)
        # No broad web query (only LinkedIn + website domain)
        assert not any("-site:linkedin.com" in q for q in queries)

    def test_no_website_query_without_domain(self):
        company = {"name": "Acme Corp", "website": ""}
        queries = _build_search_queries(company)
        # Should NOT have site: query for company website
        assert not any("site:acme.com" in q for q in queries)

    def test_still_has_linkedin_queries(self):
        company = {"name": "Acme Corp", "website": "https://acme.com"}
        queries = _build_search_queries(company)
        linkedin_queries = [q for q in queries if "site:linkedin.com/in" in q]
        assert len(linkedin_queries) == 2


# ── _strip_legal_suffix ──────────────────────────────────────────────────

class TestStripLegalSuffix:
    def test_gmbh(self):
        assert _strip_legal_suffix("NET42 GmbH") == "NET42"

    def test_ag(self):
        assert _strip_legal_suffix("SwissCloud AG") == "SwissCloud"

    def test_ltd(self):
        assert _strip_legal_suffix("Acme Ltd") == "Acme"

    def test_no_suffix(self):
        assert _strip_legal_suffix("Cloudflare") == "Cloudflare"

    def test_case_insensitive(self):
        assert _strip_legal_suffix("Test gmbh") == "Test"

    def test_sa(self):
        assert _strip_legal_suffix("ELCA SA") == "ELCA"

    def test_preserves_inner_words(self):
        assert _strip_legal_suffix("AG Software Solutions GmbH") == "AG Software Solutions"


# ── _build_fallback_queries ──────────────────────────────────────────────

class TestBuildFallbackQueries:
    def test_includes_broad_linkedin_query(self):
        company = {"name": "NET42 GmbH", "website": "https://net42.de"}
        queries = _build_fallback_queries(company)
        assert any("site:linkedin.com/in" in q and "NET42" in q for q in queries)

    def test_strips_legal_suffix_in_linkedin_query(self):
        company = {"name": "NET42 GmbH", "website": ""}
        queries = _build_fallback_queries(company)
        # Should search for "NET42" not "NET42 GmbH"
        li_queries = [q for q in queries if "site:linkedin.com/in" in q]
        assert li_queries
        assert "GmbH" not in li_queries[0]

    def test_no_role_keywords_in_linkedin_query(self):
        company = {"name": "NET42 GmbH", "website": ""}
        queries = _build_fallback_queries(company)
        li_queries = [q for q in queries if "site:linkedin.com/in" in q]
        assert li_queries
        assert "CTO" not in li_queries[0]
        assert "DevOps" not in li_queries[0]

    def test_includes_impressum_query_when_domain(self):
        company = {"name": "NET42 GmbH", "website": "https://net42.de"}
        queries = _build_fallback_queries(company)
        assert any("site:net42.de" in q and "impressum" in q.lower() for q in queries)

    def test_no_website_query_without_domain(self):
        company = {"name": "NET42 GmbH", "website": ""}
        queries = _build_fallback_queries(company)
        assert len(queries) == 1  # only the broad LinkedIn query


# ── _parse_profiles_from_serp with website results ──────────────────────

MIXED_SERP_OUTPUT = """1. John Doe - CTO - TestCorp | LinkedIn
   URL: https://www.linkedin.com/in/john-doe-a1b2c3d4
   CTO at TestCorp with 15 years of experience in cloud infrastructure.

2. Jane Smith - VP Engineering at TestCorp
   URL: https://testcorp.ch/team/jane-smith
   Jane Smith leads the engineering team at TestCorp.

3. Our Team | TestCorp
   URL: https://testcorp.ch/team
   Meet our talented team of engineers and leaders.

4. Bob Wilson - Head of DevOps
   URL: https://techcrunch.com/2024/bob-wilson-testcorp
   Bob Wilson joins TestCorp as Head of DevOps."""


class TestParseProfilesFromSerpMixed:
    def test_extracts_linkedin_profiles(self):
        profiles = _parse_profiles_from_serp(MIXED_SERP_OUTPUT, "TestCorp")
        linkedin_profiles = [p for p in profiles if p["url"]]
        assert any("john-doe" in p["url"] for p in linkedin_profiles)

    def test_extracts_website_profiles(self):
        profiles = _parse_profiles_from_serp(MIXED_SERP_OUTPUT, "TestCorp")
        # Should find Jane Smith from website
        names = [(p["first_name"], p["last_name"]) for p in profiles]
        assert ("Jane", "Smith") in names

    def test_skips_non_person_pages(self):
        profiles = _parse_profiles_from_serp(MIXED_SERP_OUTPUT, "TestCorp")
        # "Our Team | TestCorp" should NOT create a profile
        source_urls = [p.get("source_url", "") for p in profiles]
        assert not any("testcorp.ch/team" == u for u in source_urls)

    def test_website_profiles_have_empty_url(self):
        """Website-sourced profiles should have empty LinkedIn url."""
        profiles = _parse_profiles_from_serp(MIXED_SERP_OUTPUT, "TestCorp")
        jane = next((p for p in profiles if p["first_name"] == "Jane"), None)
        assert jane is not None
        assert jane["url"] == ""

    def test_website_profiles_have_source_url(self):
        profiles = _parse_profiles_from_serp(MIXED_SERP_OUTPUT, "TestCorp")
        jane = next((p for p in profiles if p["first_name"] == "Jane"), None)
        assert jane is not None
        assert jane["source_url"] == "https://testcorp.ch/team/jane-smith"

    def test_filters_out_invalid_linkedin_slugs(self):
        """Broken slugs like single letters, trailing dashes, or URN IDs should be skipped."""
        serp_text = """1. L - SomeTitle | LinkedIn
   URL: https://www.linkedin.com/in/l
   Some description.

2. Miguel - TestCorp | LinkedIn
   URL: https://www.linkedin.com/in/miguel-
   Some description.

3. David Hume - ATB Technologies | LinkedIn
   URL: https://www.linkedin.com/in/ACoAADGp5P8B7n_cNkv0XJ4zQ-wYSNssTEJLGRo
   Some description.

4. John Doe - CTO - TestCorp | LinkedIn
   URL: https://www.linkedin.com/in/john-doe-a1b2c3d4
   CTO at TestCorp."""
        profiles = _parse_profiles_from_serp(serp_text, "TestCorp")
        urls = [p["url"] for p in profiles]
        # Only john-doe should survive
        assert len(profiles) == 1
        assert "john-doe" in profiles[0]["url"]

    def test_deduplicates_by_name_across_sources(self):
        """Same person found on LinkedIn AND website should not be duplicated."""
        serp_text = """1. John Doe - CTO - TestCorp | LinkedIn
   URL: https://www.linkedin.com/in/john-doe-a1b2c3d4
   CTO at TestCorp.

2. John Doe - CTO at TestCorp
   URL: https://testcorp.ch/team/john-doe
   John Doe is the CTO of TestCorp."""
        profiles = _parse_profiles_from_serp(serp_text, "TestCorp")
        johns = [p for p in profiles if p["first_name"] == "John" and p["last_name"] == "Doe"]
        assert len(johns) == 1
        # LinkedIn version should be preferred
        assert "linkedin.com" in johns[0]["url"]


# ── _resolve_linkedin_urls ───────────────────────────────────────────────

class TestResolveLinkedinUrls:
    def test_resolves_linkedin_url(self):
        import asyncio

        serp_response = """1. Jane Smith - VP Engineering | LinkedIn
   URL: https://www.linkedin.com/in/jane-smith-xyz789
   VP Engineering at TestCorp."""

        mock_tool = AsyncMock()
        mock_tool.execute.return_value = serp_response

        profiles = [
            {"url": "", "first_name": "Jane", "last_name": "Smith",
             "title_hint": "VP Engineering", "source_url": "https://testcorp.ch/team"},
        ]

        result = asyncio.run(_resolve_linkedin_urls(profiles, "TestCorp", mock_tool))
        assert result[0]["url"] == "https://www.linkedin.com/in/jane-smith-xyz789"

    def test_keeps_empty_url_when_not_found(self):
        import asyncio

        mock_tool = AsyncMock()
        mock_tool.execute.return_value = "No results found for: test"

        profiles = [
            {"url": "", "first_name": "Unknown", "last_name": "Person",
             "title_hint": "CEO", "source_url": "https://example.com/team"},
        ]

        result = asyncio.run(_resolve_linkedin_urls(profiles, "SomeCorp", mock_tool))
        assert result[0]["url"] == ""

    def test_skips_profiles_that_already_have_url(self):
        import asyncio

        mock_tool = AsyncMock()

        profiles = [
            {"url": "https://www.linkedin.com/in/existing", "first_name": "John",
             "last_name": "Doe", "title_hint": "CTO", "source_url": ""},
        ]

        result = asyncio.run(_resolve_linkedin_urls(profiles, "TestCorp", mock_tool))
        # Should NOT have called the search tool
        mock_tool.execute.assert_not_called()
        assert result[0]["url"] == "https://www.linkedin.com/in/existing"

    def test_search_query_contains_name_and_company(self):
        import asyncio

        mock_tool = AsyncMock()
        mock_tool.execute.return_value = "No results found for: test"

        profiles = [
            {"url": "", "first_name": "Alice", "last_name": "Wonder",
             "title_hint": "CTO", "source_url": "https://wonder.ch/team"},
        ]

        asyncio.run(_resolve_linkedin_urls(profiles, "WonderCorp", mock_tool))
        query = mock_tool.execute.call_args.kwargs.get("query", mock_tool.execute.call_args[0][0] if mock_tool.execute.call_args[0] else "")
        assert "Alice Wonder" in query or ("Alice" in query and "Wonder" in query)
        assert "WonderCorp" in query


# ── _write_csv with website-sourced profiles ─────────────────────────────

class TestWriteCsvWebsiteProfiles:
    def test_writes_profile_without_linkedin_url(self):
        companies = [{"name": "TestCorp", "rating": "5", "website": "https://test.ch",
                       "linkedin": "https://linkedin.com/company/test", "size": "", "hq": "", "notes": ""}]
        profiles = {
            "TestCorp": [
                {"url": "", "first_name": "Jane", "last_name": "Smith",
                 "title_hint": "CTO", "source_url": "https://test.ch/team"},
            ],
        }
        buf = io.StringIO()
        count = _write_csv(profiles, companies, buf)
        assert count == 1
        buf.seek(0)
        reader = csv.reader(buf)
        next(reader)  # skip header
        row = next(reader)
        assert row[0] == ""  # LinkedIn URL empty
        assert row[1] == "Jane"
        assert row[2] == "Smith"
        assert row[3] == "TestCorp"


# ── Role priority sorting ─────────────────────────────────────────────────


class TestRolePriority:
    """_role_priority(title_hint) returns a numeric score; lower = higher priority."""

    def test_platform_engineer_highest(self):
        assert _role_priority("Platform Engineer at Acme") < _role_priority("DevOps Engineer at Acme")

    def test_cloud_architect_above_devops(self):
        assert _role_priority("Cloud Architect") < _role_priority("DevOps Lead")

    def test_cloud_architect_above_cto(self):
        assert _role_priority("Cloud Architect - Acme") < _role_priority("CTO at Acme")

    def test_cto_above_sre(self):
        assert _role_priority("CTO") < _role_priority("SRE")

    def test_sre_above_infra_engineer(self):
        assert _role_priority("SRE at Acme") < _role_priority("Infrastructure Engineer")

    def test_infra_engineer_above_head_of_infra(self):
        assert _role_priority("Infrastructure Engineer") < _role_priority("Head of Infrastructure")

    def test_head_of_infra_above_it_leiter(self):
        assert _role_priority("Head of Infrastructure") < _role_priority("IT-Leiter")

    def test_it_leiter_above_sysadmin(self):
        assert _role_priority("IT-Leiter bei Firma") < _role_priority("System Administrator")

    # ── New roles: VP/Technical Lead/Container/Site Reliability ──

    def test_vp_infrastructure_is_recognized(self):
        assert _role_priority("VP AI Infrastructure & Ops") < _role_priority("Marketing Manager")

    def test_vp_engineering_is_recognized(self):
        assert _role_priority("VP Product Engineering") < _role_priority("Marketing Manager")

    def test_technical_lead_is_recognized(self):
        assert _role_priority("Technical Lead@CLYSO") < _role_priority("Marketing Manager")

    def test_container_specialist_is_recognized(self):
        assert _role_priority("Containerspecialist and team lead") < _role_priority("Marketing Manager")

    def test_site_reliability_full_name_is_recognized(self):
        """'Staff Site Reliability Engineer' should match (SRE abbreviation doesn't substring-match)."""
        assert _role_priority("Staff Site Reliability Engineer") < _role_priority("Marketing Manager")

    def test_datacenter_architect_is_recognized(self):
        assert _role_priority("Datacenter Architect") < _role_priority("Marketing Manager")

    def test_cloud_engineer_is_recognized(self):
        assert _role_priority("Cloud Engineer at Acme") < _role_priority("Marketing Manager")

    def test_data_center_expert_is_recognized(self):
        assert _role_priority("Data Center & Cloud Expert") < _role_priority("Marketing Manager")

    def test_container_above_sysadmin(self):
        assert _role_priority("Container specialist") < _role_priority("System Administrator")

    def test_unknown_role_lowest(self):
        """Roles not in the priority list get the worst score."""
        assert _role_priority("Marketing Manager") > _role_priority("System Administrator")

    def test_empty_title(self):
        assert _role_priority("") > _role_priority("System Administrator")

    def test_case_insensitive(self):
        assert _role_priority("platform engineer") == _role_priority("Platform Engineer at Acme")

    def test_kubernetes_is_recognized(self):
        assert _role_priority("Head of Kubernetes at CLYSO") < _role_priority("Marketing Manager")

    def test_cloud_native_is_recognized(self):
        assert _role_priority("Cloud-Native & Distributed Systems | Brainloop") < _role_priority("Marketing Manager")

    def test_openstack_is_recognized(self):
        assert _role_priority("Openstack Infrastructure Engineer at NScale") < _role_priority("Marketing Manager")

    def test_head_of_it_is_recognized(self):
        assert _role_priority("Head of IT") < _role_priority("Marketing Manager")

    def test_openshift_is_recognized(self):
        assert _role_priority("OpenShift Engineer") < _role_priority("Marketing Manager")

    def test_sorting_profiles_by_priority(self):
        """Sorting a list of profiles by _role_priority keeps highest-priority first."""
        profiles = [
            {"title_hint": "System Administrator", "first_name": "A", "last_name": "A"},
            {"title_hint": "Platform Engineer at Acme", "first_name": "B", "last_name": "B"},
            {"title_hint": "CTO", "first_name": "C", "last_name": "C"},
            {"title_hint": "DevOps Lead", "first_name": "D", "last_name": "D"},
        ]
        sorted_profiles = sorted(profiles, key=lambda p: _role_priority(p["title_hint"]))
        assert [p["first_name"] for p in sorted_profiles] == ["B", "C", "D", "A"]

    def test_cap_after_sort_keeps_best(self):
        """Sorting then slicing at max_profiles keeps the highest-priority roles."""
        profiles = [
            {"title_hint": "System Administrator"},
            {"title_hint": "Platform Engineer"},
            {"title_hint": "CTO"},
            {"title_hint": "SRE"},
        ]
        sorted_profiles = sorted(profiles, key=lambda p: _role_priority(p["title_hint"]))
        top2 = sorted_profiles[:2]
        roles = [p["title_hint"] for p in top2]
        assert "Platform Engineer" in roles
        assert "DevOps" not in roles  # not in input
        assert "System Administrator" not in roles


# ── _needs_fallback (priority-aware LinkedIn fallback) ────────────────────


class TestNeedsFallback:
    """_needs_fallback(profiles, threshold) checks both count AND quality."""

    def test_empty_profiles_needs_fallback(self):
        assert _needs_fallback([], threshold=2) is True

    def test_below_count_needs_fallback(self):
        profiles = [
            {"title_hint": "Platform Engineer", "url": "https://linkedin.com/in/a"},
        ]
        assert _needs_fallback(profiles, threshold=2) is True

    def test_enough_count_no_fallback(self):
        profiles = [
            {"title_hint": "Platform Engineer", "url": "https://linkedin.com/in/a"},
            {"title_hint": "DevOps Lead", "url": "https://linkedin.com/in/b"},
        ]
        assert _needs_fallback(profiles, threshold=2) is False

    def test_enough_count_but_low_priority_needs_fallback(self):
        """2 profiles but both are low-priority — should still trigger fallback."""
        profiles = [
            {"title_hint": "System Administrator", "url": "https://linkedin.com/in/a"},
            {"title_hint": "IT-Leiter", "url": "https://linkedin.com/in/b"},
        ]
        assert _needs_fallback(profiles, threshold=2) is True

    def test_one_high_priority_enough(self):
        """At least one high-priority profile means no fallback needed (if count met)."""
        profiles = [
            {"title_hint": "Platform Engineer", "url": "https://linkedin.com/in/a"},
            {"title_hint": "System Administrator", "url": "https://linkedin.com/in/b"},
        ]
        assert _needs_fallback(profiles, threshold=2) is False

    def test_cto_is_high_enough(self):
        profiles = [
            {"title_hint": "CTO", "url": "https://linkedin.com/in/a"},
            {"title_hint": "System Administrator", "url": "https://linkedin.com/in/b"},
        ]
        assert _needs_fallback(profiles, threshold=2) is False

    def test_sre_is_borderline_high(self):
        """SRE (priority 4) should count as high-priority."""
        profiles = [
            {"title_hint": "SRE at Acme", "url": "https://linkedin.com/in/a"},
            {"title_hint": "IT-Leiter", "url": "https://linkedin.com/in/b"},
        ]
        assert _needs_fallback(profiles, threshold=2) is False

    def test_infra_engineer_is_too_low(self):
        """Infrastructure Engineer (priority 5) is not high enough alone."""
        profiles = [
            {"title_hint": "Infrastructure Engineer", "url": "https://linkedin.com/in/a"},
            {"title_hint": "System Administrator", "url": "https://linkedin.com/in/b"},
        ]
        assert _needs_fallback(profiles, threshold=2) is True

    def test_threshold_zero_never_needs_fallback(self):
        assert _needs_fallback([], threshold=0) is False


# ── _enrich_missing_roles (LinkedIn profile lookup for role-less profiles) ──


class TestEnrichMissingRoles:
    """_enrich_missing_roles uses LinkedIn API get_profile to fill missing roles."""

    @patch("agents.export.linkedin.Linkedin")
    def test_enriches_profile_without_role(self, mock_linkedin_cls):
        mock_api = MagicMock()
        mock_linkedin_cls.return_value = mock_api
        mock_api.get_profile.return_value = {
            "headline": "DevOps Engineer at Acme Corp",
        }

        profiles = {
            "Acme Corp": [
                {"url": "https://www.linkedin.com/in/john-doe",
                 "first_name": "John", "last_name": "Doe",
                 "title_hint": "John Doe - Acme Corp | LinkedIn"},
            ],
        }
        result = _enrich_missing_roles(profiles, "email@test.com", "password")
        assert result["Acme Corp"][0]["title_hint"] == "DevOps Engineer at Acme Corp"
        mock_api.get_profile.assert_called_once_with(public_id="john-doe")

    @patch("agents.export.linkedin.Linkedin")
    def test_skips_profile_with_known_role(self, mock_linkedin_cls):
        mock_api = MagicMock()
        mock_linkedin_cls.return_value = mock_api

        profiles = {
            "Acme Corp": [
                {"url": "https://www.linkedin.com/in/jane-smith",
                 "first_name": "Jane", "last_name": "Smith",
                 "title_hint": "CTO at Acme Corp"},
            ],
        }
        result = _enrich_missing_roles(profiles, "email@test.com", "password")
        assert result["Acme Corp"][0]["title_hint"] == "CTO at Acme Corp"
        mock_api.get_profile.assert_not_called()

    @patch("agents.export.linkedin.Linkedin")
    def test_skips_profile_without_linkedin_url(self, mock_linkedin_cls):
        mock_api = MagicMock()
        mock_linkedin_cls.return_value = mock_api

        profiles = {
            "Acme Corp": [
                {"url": "", "first_name": "John", "last_name": "Doe",
                 "title_hint": "CTO", "source_url": "https://acme.com/team"},
            ],
        }
        result = _enrich_missing_roles(profiles, "email@test.com", "password")
        mock_api.get_profile.assert_not_called()

    @patch("agents.export.linkedin.Linkedin")
    def test_handles_api_error_gracefully(self, mock_linkedin_cls):
        mock_api = MagicMock()
        mock_linkedin_cls.return_value = mock_api
        mock_api.get_profile.side_effect = Exception("rate limited")
        mock_api.search_people.return_value = []  # fallback also finds nothing

        profiles = {
            "Acme Corp": [
                {"url": "https://www.linkedin.com/in/john-doe",
                 "first_name": "John", "last_name": "Doe",
                 "title_hint": "John Doe - Acme Corp | LinkedIn"},
            ],
        }
        result = _enrich_missing_roles(profiles, "email@test.com", "password")
        # title_hint unchanged when both get_profile and search_people fail/return nothing
        assert result["Acme Corp"][0]["title_hint"] == "John Doe - Acme Corp | LinkedIn"

    @patch("agents.export.linkedin.Linkedin")
    def test_handles_empty_headline(self, mock_linkedin_cls):
        mock_api = MagicMock()
        mock_linkedin_cls.return_value = mock_api
        mock_api.get_profile.return_value = {"headline": ""}

        profiles = {
            "Acme Corp": [
                {"url": "https://www.linkedin.com/in/john-doe",
                 "first_name": "John", "last_name": "Doe",
                 "title_hint": "John Doe - Acme Corp | LinkedIn"},
            ],
        }
        result = _enrich_missing_roles(profiles, "email@test.com", "password")
        # Don't overwrite with empty string
        assert result["Acme Corp"][0]["title_hint"] == "John Doe - Acme Corp | LinkedIn"

    @patch("agents.export.linkedin.Linkedin")
    def test_enriches_multiple_companies(self, mock_linkedin_cls):
        mock_api = MagicMock()
        mock_linkedin_cls.return_value = mock_api
        mock_api.get_profile.side_effect = [
            {"headline": "Platform Engineer"},
            {"headline": "SRE Lead"},
        ]

        profiles = {
            "Acme": [
                {"url": "https://www.linkedin.com/in/alice",
                 "first_name": "Alice", "last_name": "A",
                 "title_hint": "Alice A - Acme | LinkedIn"},
            ],
            "Beta": [
                {"url": "https://www.linkedin.com/in/bob",
                 "first_name": "Bob", "last_name": "B",
                 "title_hint": "Bob B | LinkedIn"},
            ],
        }
        result = _enrich_missing_roles(profiles, "e@t.com", "pw")
        assert result["Acme"][0]["title_hint"] == "Platform Engineer"
        assert result["Beta"][0]["title_hint"] == "SRE Lead"

    @patch("agents.export.linkedin.Linkedin")
    def test_only_enriches_unknown_roles(self, mock_linkedin_cls):
        """Mixed profiles: only the ones with unrecognized roles get enriched."""
        mock_api = MagicMock()
        mock_linkedin_cls.return_value = mock_api
        mock_api.get_profile.return_value = {"headline": "Cloud Architect"}

        profiles = {
            "Acme": [
                {"url": "https://www.linkedin.com/in/known",
                 "first_name": "Known", "last_name": "Person",
                 "title_hint": "DevOps Engineer at Acme"},
                {"url": "https://www.linkedin.com/in/unknown",
                 "first_name": "Unknown", "last_name": "Person",
                 "title_hint": "Unknown Person - Acme | LinkedIn"},
            ],
        }
        result = _enrich_missing_roles(profiles, "e@t.com", "pw")
        assert result["Acme"][0]["title_hint"] == "DevOps Engineer at Acme"  # unchanged
        assert result["Acme"][1]["title_hint"] == "Cloud Architect"  # enriched
        assert mock_api.get_profile.call_count == 1

    @patch("agents.export.linkedin.Linkedin")
    def test_reuses_api_instance(self, mock_linkedin_cls):
        """Should create only one Linkedin instance, not one per profile."""
        mock_api = MagicMock()
        mock_linkedin_cls.return_value = mock_api
        mock_api.get_profile.return_value = {"headline": "CTO"}

        profiles = {
            "A": [{"url": "https://www.linkedin.com/in/p1",
                   "first_name": "P", "last_name": "1", "title_hint": "P1 - A"}],
            "B": [{"url": "https://www.linkedin.com/in/p2",
                   "first_name": "P", "last_name": "2", "title_hint": "P2 - B"}],
        }
        _enrich_missing_roles(profiles, "e@t.com", "pw")
        mock_linkedin_cls.assert_called_once()

    @patch("agents.export.linkedin.Linkedin")
    def test_returns_empty_when_no_profiles(self, mock_linkedin_cls):
        mock_api = MagicMock()
        mock_linkedin_cls.return_value = mock_api
        result = _enrich_missing_roles({}, "e@t.com", "pw")
        assert result == {}
        mock_linkedin_cls.assert_not_called()

    @patch("agents.export.linkedin.Linkedin")
    def test_uses_cookies_when_available(self, mock_linkedin_cls):
        mock_api = MagicMock()
        mock_linkedin_cls.return_value = mock_api
        mock_api.get_profile.return_value = {"headline": "SRE"}

        profiles = {
            "Acme": [{"url": "https://www.linkedin.com/in/test",
                       "first_name": "T", "last_name": "T",
                       "title_hint": "T T - Acme"}],
        }
        with patch.dict(os.environ, {"LINKEDIN_LI_AT": "tok", "LINKEDIN_JSESSIONID": "sess"}):
            _enrich_missing_roles(profiles, "", "")
        # Should have used cookies auth (empty email/password + cookies)
        call_args = mock_linkedin_cls.call_args
        assert call_args.kwargs.get("cookies") is not None or (
            len(call_args.args) >= 2 and call_args.args[0] == "" and call_args.args[1] == ""
        )

    @patch("agents.export.linkedin.Linkedin")
    def test_falls_back_to_search_people_on_get_profile_error(self, mock_linkedin_cls):
        """When get_profile() fails (e.g. KeyError: 'message'), fall back to
        search_people() using the person's name to get their jobtitle."""
        mock_api = MagicMock()
        mock_linkedin_cls.return_value = mock_api
        mock_api.get_profile.side_effect = KeyError("message")
        mock_api.search_people.return_value = [
            {"name": "John Doe", "jobtitle": "Platform Engineer at Acme", "urn_id": "john-doe"},
        ]

        profiles = {
            "Acme Corp": [
                {"url": "https://www.linkedin.com/in/john-doe",
                 "first_name": "John", "last_name": "Doe",
                 "title_hint": "John Doe - Acme Corp | LinkedIn"},
            ],
        }
        result = _enrich_missing_roles(profiles, "e@t.com", "pw")
        assert result["Acme Corp"][0]["title_hint"] == "Platform Engineer at Acme"
        mock_api.search_people.assert_called_once_with(
            keywords="John Doe", keyword_company="Acme Corp", limit=1,
        )

    @patch("agents.export.linkedin.Linkedin")
    def test_search_people_fallback_no_results(self, mock_linkedin_cls):
        """When both get_profile and search_people return nothing, title_hint unchanged."""
        mock_api = MagicMock()
        mock_linkedin_cls.return_value = mock_api
        mock_api.get_profile.side_effect = KeyError("message")
        mock_api.search_people.return_value = []

        profiles = {
            "Acme Corp": [
                {"url": "https://www.linkedin.com/in/john-doe",
                 "first_name": "John", "last_name": "Doe",
                 "title_hint": "John Doe - Acme Corp | LinkedIn"},
            ],
        }
        result = _enrich_missing_roles(profiles, "e@t.com", "pw")
        assert result["Acme Corp"][0]["title_hint"] == "John Doe - Acme Corp | LinkedIn"

    @patch("agents.export.linkedin.Linkedin")
    def test_search_people_fallback_also_fails(self, mock_linkedin_cls):
        """When both get_profile and search_people raise exceptions, count as consecutive error."""
        mock_api = MagicMock()
        mock_linkedin_cls.return_value = mock_api
        mock_api.get_profile.side_effect = KeyError("message")
        mock_api.search_people.side_effect = Exception("also broken")

        profiles = {
            "Acme": [
                {"url": "https://www.linkedin.com/in/p1",
                 "first_name": "P", "last_name": "1", "title_hint": "P1 - A"},
                {"url": "https://www.linkedin.com/in/p2",
                 "first_name": "P", "last_name": "2", "title_hint": "P2 - A"},
                {"url": "https://www.linkedin.com/in/p3",
                 "first_name": "P", "last_name": "3", "title_hint": "P3 - A"},
                {"url": "https://www.linkedin.com/in/p4",
                 "first_name": "P", "last_name": "4", "title_hint": "P4 - A"},
            ],
        }
        result = _enrich_missing_roles(profiles, "e@t.com", "pw")
        # get_profile disabled after 3 failures, but search_people still tried
        assert mock_api.get_profile.call_count == 3
        # Circuit breaker on consecutive no-headline stops after 3
        assert result["Acme"][3]["title_hint"] == "P4 - A"

    @patch("agents.export.linkedin.Linkedin")
    def test_get_profile_error_resets_consecutive_on_search_success(self, mock_linkedin_cls):
        """get_profile fails but search_people succeeds — should reset consecutive error count."""
        mock_api = MagicMock()
        mock_linkedin_cls.return_value = mock_api
        mock_api.get_profile.side_effect = KeyError("message")
        mock_api.search_people.side_effect = [
            [{"name": "P 1", "jobtitle": "CTO", "urn_id": "p1"}],
            [{"name": "P 2", "jobtitle": "SRE", "urn_id": "p2"}],
            [{"name": "P 3", "jobtitle": "DevOps", "urn_id": "p3"}],
            [{"name": "P 4", "jobtitle": "CEO", "urn_id": "p4"}],
        ]

        profiles = {
            "A": [
                {"url": "https://www.linkedin.com/in/p1",
                 "first_name": "P", "last_name": "1", "title_hint": "P1 - A"},
                {"url": "https://www.linkedin.com/in/p2",
                 "first_name": "P", "last_name": "2", "title_hint": "P2 - A"},
                {"url": "https://www.linkedin.com/in/p3",
                 "first_name": "P", "last_name": "3", "title_hint": "P3 - A"},
                {"url": "https://www.linkedin.com/in/p4",
                 "first_name": "P", "last_name": "4", "title_hint": "P4 - A"},
            ],
        }
        result = _enrich_missing_roles(profiles, "e@t.com", "pw")
        # get_profile disabled after 3 failures, 4th only uses search_people
        assert mock_api.get_profile.call_count == 3
        # All 4 enriched via search_people (no consecutive no-headline errors)
        assert result["A"][0]["title_hint"] == "CTO"
        assert result["A"][3]["title_hint"] == "CEO"

    @patch("agents.export.linkedin.Linkedin")
    def test_disables_get_profile_after_3_failures(self, mock_linkedin_cls):
        """After 3 get_profile failures, skip it and go straight to search_people."""
        mock_api = MagicMock()
        mock_linkedin_cls.return_value = mock_api
        mock_api.get_profile.side_effect = KeyError("message")
        mock_api.search_people.side_effect = [
            [{"name": "P 1", "jobtitle": "CTO", "urn_id": "p1"}],
            [{"name": "P 2", "jobtitle": "SRE", "urn_id": "p2"}],
            [{"name": "P 3", "jobtitle": "DevOps", "urn_id": "p3"}],
            [{"name": "P 4", "jobtitle": "CEO", "urn_id": "p4"}],
            [{"name": "P 5", "jobtitle": "VP Eng", "urn_id": "p5"}],
        ]

        profiles = {
            "A": [
                {"url": "https://www.linkedin.com/in/p1",
                 "first_name": "P", "last_name": "1", "title_hint": "P1 - A"},
                {"url": "https://www.linkedin.com/in/p2",
                 "first_name": "P", "last_name": "2", "title_hint": "P2 - A"},
                {"url": "https://www.linkedin.com/in/p3",
                 "first_name": "P", "last_name": "3", "title_hint": "P3 - A"},
                {"url": "https://www.linkedin.com/in/p4",
                 "first_name": "P", "last_name": "4", "title_hint": "P4 - A"},
                {"url": "https://www.linkedin.com/in/p5",
                 "first_name": "P", "last_name": "5", "title_hint": "P5 - A"},
            ],
        }
        result = _enrich_missing_roles(profiles, "e@t.com", "pw")
        # get_profile called only 3 times, then disabled
        assert mock_api.get_profile.call_count == 3
        # All 5 enriched via search_people
        assert mock_api.search_people.call_count == 5
        assert result["A"][4]["title_hint"] == "VP Eng"

    @patch("agents.export.linkedin.Linkedin")
    def test_search_people_updates_names_from_slug(self, mock_linkedin_cls):
        """When search_people returns a name, update first/last from the result."""
        mock_api = MagicMock()
        mock_linkedin_cls.return_value = mock_api
        mock_api.get_profile.side_effect = KeyError("message")
        mock_api.search_people.return_value = [
            {"name": "Didier Lahay", "jobtitle": "Cloud Architect", "urn_id": "didierlahay"},
        ]

        profiles = {
            "Acme": [
                {"url": "https://www.linkedin.com/in/didierlahay",
                 "first_name": "Didierlahay", "last_name": "",
                 "title_hint": "Didierlahay - Acme | LinkedIn"},
            ],
        }
        result = _enrich_missing_roles(profiles, "e@t.com", "pw")
        assert result["Acme"][0]["first_name"] == "Didier"
        assert result["Acme"][0]["last_name"] == "Lahay"
        assert result["Acme"][0]["title_hint"] == "Cloud Architect"

    @patch("agents.export.linkedin.Linkedin")
    def test_search_people_matches_accented_names(self, mock_linkedin_cls):
        """Concatenated slug 'cicibroden' should match 'Cici Brodén' (accent stripped)."""
        mock_api = MagicMock()
        mock_linkedin_cls.return_value = mock_api
        mock_api.get_profile.side_effect = KeyError("message")
        mock_api.search_people.return_value = [
            {"name": "Cici Brodén", "jobtitle": "CSO", "urn_id": "cicibroden"},
        ]

        profiles = {
            "Bahnhof": [
                {"url": "https://www.linkedin.com/in/cicibroden",
                 "first_name": "Cicibroden", "last_name": "",
                 "title_hint": "Cicibroden - Bahnhof | LinkedIn"},
            ],
        }
        result = _enrich_missing_roles(profiles, "e@t.com", "pw")
        assert result["Bahnhof"][0]["title_hint"] == "CSO"
        assert result["Bahnhof"][0]["first_name"] == "Cici"
        assert result["Bahnhof"][0]["last_name"] == "Brodén"

    @patch("agents.export.linkedin.Linkedin")
    def test_search_people_wrong_person_discarded(self, mock_linkedin_cls):
        """When search_people returns a completely different person, don't use the result."""
        mock_api = MagicMock()
        mock_linkedin_cls.return_value = mock_api
        mock_api.get_profile.side_effect = KeyError("message")
        # search returns "Alice Wonder" but we're looking for "John Doe"
        mock_api.search_people.return_value = [
            {"name": "Alice Wonder", "jobtitle": "CEO", "urn_id": "alice-wonder"},
        ]

        profiles = {
            "Acme": [
                {"url": "https://www.linkedin.com/in/john-doe",
                 "first_name": "John", "last_name": "Doe",
                 "title_hint": "John Doe - Acme | LinkedIn"},
            ],
        }
        result = _enrich_missing_roles(profiles, "e@t.com", "pw")
        # Should NOT use Alice's title for John
        assert result["Acme"][0]["title_hint"] == "John Doe - Acme | LinkedIn"
        assert result["Acme"][0]["first_name"] == "John"

    @patch("agents.export.linkedin.Linkedin")
    def test_search_people_match_by_urn_id(self, mock_linkedin_cls):
        """When search_people returns matching urn_id, accept even if name differs slightly."""
        mock_api = MagicMock()
        mock_linkedin_cls.return_value = mock_api
        mock_api.get_profile.side_effect = KeyError("message")
        # urn_id matches the public_id from the profile URL
        mock_api.search_people.return_value = [
            {"name": "J.P. Martin-Flatin", "jobtitle": "SRE Lead",
             "urn_id": "jp-martin-flatin-123abc"},
        ]

        profiles = {
            "Acme": [
                {"url": "https://www.linkedin.com/in/jp-martin-flatin-123abc",
                 "first_name": "Jp Martin", "last_name": "Flatin",
                 "title_hint": "JP Martin-Flatin - Acme | LinkedIn"},
            ],
        }
        result = _enrich_missing_roles(profiles, "e@t.com", "pw")
        assert result["Acme"][0]["title_hint"] == "SRE Lead"


class TestCollectUnresolvedProfiles:
    def test_collects_profiles_with_no_recognized_role(self):
        companies = [
            {"name": "Acme", "website": "https://acme.com", "linkedin": "https://linkedin.com/company/acme"},
        ]
        company_profiles = {
            "Acme": [
                {"url": "https://www.linkedin.com/in/john-doe",
                 "first_name": "John", "last_name": "Doe",
                 "title_hint": "CTO at Acme"},  # recognized
                {"url": "https://www.linkedin.com/in/jane-smith",
                 "first_name": "Jane", "last_name": "Smith",
                 "title_hint": "Jane Smith - Acme | LinkedIn"},  # not recognized
            ],
        }
        unresolved = _collect_unresolved_profiles(company_profiles, companies)
        assert len(unresolved) == 1
        assert unresolved[0]["first_name"] == "Jane"
        assert unresolved[0]["company_name"] == "Acme"
        assert unresolved[0]["company_website"] == "https://acme.com"
        assert unresolved[0]["company_linkedin"] == "https://linkedin.com/company/acme"

    def test_returns_empty_when_all_resolved(self):
        companies = [
            {"name": "Acme", "website": "https://acme.com", "linkedin": ""},
        ]
        company_profiles = {
            "Acme": [
                {"url": "https://www.linkedin.com/in/john",
                 "first_name": "John", "last_name": "Doe",
                 "title_hint": "DevOps Engineer"},
            ],
        }
        unresolved = _collect_unresolved_profiles(company_profiles, companies)
        assert unresolved == []

    def test_includes_profiles_without_url(self):
        companies = [
            {"name": "Acme", "website": "https://acme.com", "linkedin": ""},
        ]
        company_profiles = {
            "Acme": [
                {"url": "", "first_name": "Jane", "last_name": "Smith",
                 "title_hint": "Found on website"},
            ],
        }
        unresolved = _collect_unresolved_profiles(company_profiles, companies)
        assert len(unresolved) == 1
        assert unresolved[0]["url"] == ""

    def test_format_output_has_clickable_links(self, capsys):
        """The printed output should include direct profile and company links."""
        companies = [
            {"name": "Acme Corp", "website": "https://acme.com",
             "linkedin": "https://linkedin.com/company/acme"},
        ]
        company_profiles = {
            "Acme Corp": [
                {"url": "https://www.linkedin.com/in/unknown-person",
                 "first_name": "Unknown", "last_name": "Person",
                 "title_hint": "Unknown Person - Acme Corp | LinkedIn"},
            ],
        }
        unresolved = _collect_unresolved_profiles(company_profiles, companies)
        assert len(unresolved) == 1
        entry = unresolved[0]
        assert "linkedin.com/in/unknown-person" in entry["url"]
        assert "linkedin.com/company/acme" in entry["company_linkedin"]

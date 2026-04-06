"""Tests for it_search_agent — build_task and COUNTRY_SEARCH_HINTS."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.it_search_agent import (
    COUNTRY_NAMES,
    COUNTRY_SEARCH_HINTS,
    COUNTRY_SERP_PARAMS,
    build_task,
)


def _task(country):
    return build_task(country)


class TestBuildTask:

    @pytest.mark.parametrize("country", ["CH", "DE", "AT"])
    def test_contains_country_name(self, country):
        assert COUNTRY_NAMES[country] in _task(country)

    @pytest.mark.parametrize("country", ["CH", "DE", "AT"])
    def test_contains_tld_queries(self, country):
        tld = COUNTRY_SEARCH_HINTS[country]["tld"]
        assert f"site:.{tld}" in _task(country)

    @pytest.mark.parametrize("country", ["CH", "DE", "AT"])
    def test_contains_min_searches(self, country):
        min_s = COUNTRY_SEARCH_HINTS[country]["min_searches"]
        assert str(min_s) in _task(country)

    @pytest.mark.parametrize("country", ["CH", "DE", "AT"])
    def test_contains_target_companies(self, country):
        target = COUNTRY_SEARCH_HINTS[country]["target_companies"]
        assert str(target) in _task(country)

    @pytest.mark.parametrize("country", ["CH", "DE", "AT"])
    def test_instructs_to_append_new_companies(self, country):
        assert "sheets_append_company" in _task(country)

    @pytest.mark.parametrize("country", ["CH", "DE", "AT"])
    def test_does_not_call_sheets_read_companies(self, country):
        """Dedup is handled by the tool — agent must not call sheets_read_companies."""
        assert "sheets_read_companies" not in _task(country)

    @pytest.mark.parametrize("country", ["CH", "DE", "AT"])
    def test_no_dedup_list_in_prompt(self, country):
        """Company names/domains must not be embedded in the prompt (causes model failure)."""
        task = _task(country)
        assert "existing_names" not in task
        assert "existing_domains" not in task

    @pytest.mark.parametrize("country", ["CH", "DE", "AT"])
    def test_all_cities_mentioned(self, country):
        task = _task(country)
        cities = COUNTRY_SEARCH_HINTS[country]["cities"]
        for city in cities:
            assert city in task, f"City {city!r} missing from {country} task"

    @pytest.mark.parametrize("country", ["CH", "DE", "AT"])
    def test_hints_override_respected(self, country):
        override = {country: dict(COUNTRY_SEARCH_HINTS[country], min_searches=999)}
        task = build_task(country, hints_override=override)
        assert "999" in task


class TestFetchExistingCompanies:

    def _call(self, rows):
        from unittest.mock import MagicMock, patch

        header = ["Company Name", "Rating", "Human Comment", "Notes", "Website"]
        svc = MagicMock()
        (
            svc.spreadsheets.return_value
            .values.return_value
            .get.return_value
            .execute.return_value
        ) = {"values": [header] + rows}

        with (
            patch("agents.it_search_agent.Credentials.from_service_account_file"),
            patch("agents.it_search_agent.build", return_value=svc),
        ):
            from agents.it_search_agent import fetch_existing_companies
            return fetch_existing_companies("SHEET_ID", "creds.json", "CH")

    def test_returns_name_and_domain_sets(self):
        names, domains = self._call([["Acme AG", "", "", "", "https://acme.ch"]])
        assert "acme ag" in names
        assert "acme.ch" in domains

    def test_strips_https_www_trailing_slash(self):
        _, domains = self._call([["X", "", "", "", "https://www.example.com/"]])
        assert "example.com" in domains

    def test_empty_rows_ignored(self):
        names, domains = self._call([["", "", "", "", ""]])
        assert len(names) == 0
        assert len(domains) == 0

    def test_names_are_lowercased(self):
        names, _ = self._call([["ITpoint Systems AG", "", "", "", ""]])
        assert "itpoint systems ag" in names

    def test_empty_sheet_returns_empty_sets(self):
        names, domains = self._call([])
        assert names == set()
        assert domains == set()


class TestCountryConfig:

    def test_all_dach_countries_have_hints(self):
        for country in ("CH", "DE", "AT"):
            assert country in COUNTRY_SEARCH_HINTS

    @pytest.mark.parametrize("country", ["CH", "DE", "AT"])
    def test_required_hint_keys_present(self, country):
        hints = COUNTRY_SEARCH_HINTS[country]
        for key in ("tld", "min_searches", "target_companies", "cities", "tld_queries", "extra_queries"):
            assert key in hints, f"Missing key {key!r} for {country}"

    @pytest.mark.parametrize("country", ["CH", "DE", "AT"])
    def test_tld_queries_start_with_site_operator(self, country):
        for q in COUNTRY_SEARCH_HINTS[country]["tld_queries"]:
            tld = COUNTRY_SEARCH_HINTS[country]["tld"]
            assert q.startswith(f'site:.{tld}'), f"TLD query must start with site:.{tld}: {q!r}"

    @pytest.mark.parametrize("country", ["CH", "DE", "AT"])
    def test_serp_params_have_gl_and_cr(self, country):
        params = COUNTRY_SERP_PARAMS[country]
        assert "gl" in params
        assert "cr" in params

    def test_de_has_more_targets_than_ch(self):
        assert COUNTRY_SEARCH_HINTS["DE"]["target_companies"] > COUNTRY_SEARCH_HINTS["CH"]["target_companies"]

    def test_de_has_more_min_searches_than_at(self):
        assert COUNTRY_SEARCH_HINTS["DE"]["min_searches"] > COUNTRY_SEARCH_HINTS["AT"]["min_searches"]

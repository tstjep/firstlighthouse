"""Tests for immigration_contact_agent — role priorities, search queries, parsing, CSV."""

import csv
import io
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.immigration_contact_agent import (
    _ROLE_PRIORITY,
    _TAB_ROLE_QUERIES,
    _TAB_LINKEDIN_ROLES,
    _CSV_HEADERS,
    _cell,
    _extract_domain,
    _strip_legal_suffix,
    _role_priority,
    _matched_role,
    _is_valid_linkedin_slug,
    _parse_name_from_slug,
    _parse_name_from_title,
    _parse_profiles_from_serp,
    _build_search_queries,
    _build_fallback_queries,
    _read_and_filter,
    _write_csv,
    _linkedin_headers,
    _extract_company_slug,
    _parse_linkedin_people_response,
    _merge_profiles,
)
import config as cfg


# ── Role priority list ────────────────────────────────────────────────────────

class TestRolePriority:

    def test_managing_partner_is_highest_priority(self):
        assert _role_priority("Managing Partner") < _role_priority("Partner")

    def test_head_of_immigration_is_high_priority(self):
        assert _role_priority("Head of Immigration") < _role_priority("Immigration Solicitor")

    def test_unknown_role_gets_worst_score(self):
        assert _role_priority("Receptionist") == len(_ROLE_PRIORITY)

    def test_partner_beats_senior_associate(self):
        assert _role_priority("Partner") < _role_priority("Senior Associate")

    def test_ceo_beats_operations_manager(self):
        assert _role_priority("CEO") < _role_priority("Operations Manager")

    def test_case_insensitive(self):
        assert _role_priority("managing partner") == _role_priority("Managing Partner")

    def test_matched_role_returns_role_keyword(self):
        assert _matched_role("Managing Partner at Kingsley Napley") == "Managing Partner"

    def test_matched_role_empty_for_unknown(self):
        assert _matched_role("Receptionist") == ""


# ── Tab role queries ──────────────────────────────────────────────────────────

class TestTabRoleQueries:

    def test_all_tabs_have_queries(self):
        for tab in cfg.IMMIGRATION_TABS:
            assert tab in _TAB_ROLE_QUERIES, f"Missing queries for tab {tab!r}"

    def test_lawfirms_queries_mention_managing_partner(self):
        all_text = " ".join(
            q for pair in _TAB_ROLE_QUERIES["LawFirms"] for q in pair
        )
        assert "Managing Partner" in all_text

    def test_lawfirms_queries_mention_head_of_immigration(self):
        all_text = " ".join(
            q for pair in _TAB_ROLE_QUERIES["LawFirms"] for q in pair
        )
        assert "Head of Immigration" in all_text

    def test_advisors_queries_mention_director_or_owner(self):
        all_text = " ".join(
            q for pair in _TAB_ROLE_QUERIES["Advisors"] for q in pair
        )
        assert "Director" in all_text or "Owner" in all_text

    def test_charities_queries_mention_ceo_or_director(self):
        all_text = " ".join(
            q for pair in _TAB_ROLE_QUERIES["Charities"] for q in pair
        )
        assert "CEO" in all_text or "Director" in all_text

    def test_legaltechbrokers_queries_mention_consultant_or_director(self):
        all_text = " ".join(
            q for pair in _TAB_ROLE_QUERIES["LegaltechBrokers"] for q in pair
        )
        assert "Consultant" in all_text or "Director" in all_text

    def test_each_tab_has_at_least_one_pair(self):
        for tab in cfg.IMMIGRATION_TABS:
            assert len(_TAB_ROLE_QUERIES[tab]) >= 1


# ── Pure helpers ──────────────────────────────────────────────────────────────

class TestCellHelper:

    def test_returns_value(self):
        assert _cell(["a", "b", "c"], 1) == "b"

    def test_strips_whitespace(self):
        assert _cell(["  hello  "], 0) == "hello"

    def test_returns_empty_for_missing_index(self):
        assert _cell(["a"], 5) == ""


class TestExtractDomain:

    def test_extracts_domain(self):
        assert _extract_domain("https://www.kingsley-napley.co.uk/immigration") == "www.kingsley-napley.co.uk"

    def test_returns_none_for_empty(self):
        assert _extract_domain("") is None
        assert _extract_domain(None) is None

    def test_returns_none_for_invalid(self):
        assert _extract_domain("not-a-url") is None


class TestStripLegalSuffix:

    def test_strips_llp(self):
        assert _strip_legal_suffix("Kingsley Napley LLP") == "Kingsley Napley"

    def test_strips_ltd(self):
        assert _strip_legal_suffix("Acme Ltd") == "Acme"

    def test_strips_limited(self):
        assert _strip_legal_suffix("Bindmans Limited") == "Bindmans"

    def test_no_suffix_unchanged(self):
        assert _strip_legal_suffix("Fragomen") == "Fragomen"


class TestIsValidLinkedinSlug:

    def test_valid_slug(self):
        assert _is_valid_linkedin_slug("john-doe") is True

    def test_too_short(self):
        assert _is_valid_linkedin_slug("ab") is False

    def test_trailing_dash(self):
        assert _is_valid_linkedin_slug("john-doe-") is False

    def test_urn_style(self):
        # URN IDs start with "ACo" and are longer than 20 chars
        assert _is_valid_linkedin_slug("ACoAABCDEFGHIJKLMNOPQRSTUV") is False

    def test_empty(self):
        assert _is_valid_linkedin_slug("") is False


class TestParseNameFromSlug:

    def test_basic_slug(self):
        first, last = _parse_name_from_slug("john-doe")
        assert first == "John"
        assert last == "Doe"

    def test_slug_with_hex_id(self):
        first, last = _parse_name_from_slug("john-doe-a1b2c3d4")
        assert first == "John"
        assert last == "Doe"

    def test_single_part(self):
        first, last = _parse_name_from_slug("johndoe")
        assert first == "Johndoe"
        assert last == ""


class TestParseNameFromTitle:

    def test_standard_format(self):
        result = _parse_name_from_title("Sarah Jones - Managing Partner at Kingsley Napley")
        assert result is not None
        first, last, role = result
        assert first == "Sarah"
        assert last == "Jones"
        assert "Managing Partner" in role

    def test_returns_none_for_non_person_title(self):
        assert _parse_name_from_title("About Us - Kingsley Napley") is None
        assert _parse_name_from_title("Immigration Services | Bindmans") is None

    def test_returns_none_for_empty(self):
        assert _parse_name_from_title("") is None

    def test_returns_none_for_company_suffix(self):
        assert _parse_name_from_title("Bindmans Solicitors - London") is None


# ── Profile parsing ───────────────────────────────────────────────────────────

class TestParseProfilesFromSerp:

    def _linkedin_serp(self, slug: str, title: str, url: str | None = None) -> str:
        profile_url = url or f"https://www.linkedin.com/in/{slug}"
        return f"1. {title}\nURL: {profile_url}\nSnippet: some text"

    def test_parses_linkedin_profile(self):
        text = self._linkedin_serp("sarah-jones-a1b2", "Sarah Jones - Managing Partner")
        profiles = _parse_profiles_from_serp(text, "Acme Law")
        assert len(profiles) == 1
        assert profiles[0]["first_name"] == "Sarah"
        assert profiles[0]["last_name"] == "Jones"

    def test_deduplicates_by_url(self):
        text = (
            "1. Sarah Jones - Partner\nURL: https://www.linkedin.com/in/sarah-jones\nSnippet: x\n\n"
            "2. Sarah Jones - Director\nURL: https://www.linkedin.com/in/sarah-jones\nSnippet: y"
        )
        profiles = _parse_profiles_from_serp(text, "Acme Law")
        assert len(profiles) == 1

    def test_deduplicates_website_profile_already_found_on_linkedin(self):
        """A person found on the company website is dropped if already found on LinkedIn."""
        text = (
            "1. Sarah Jones - Partner\nURL: https://www.linkedin.com/in/sarah-jones-a1b2\nSnippet: x\n\n"
            "2. Sarah Jones - Partner at Acme Law\nURL: https://acmelaw.co.uk/team\nSnippet: y"
        )
        profiles = _parse_profiles_from_serp(text, "Acme Law")
        # Website duplicate of the LinkedIn profile is dropped
        assert len(profiles) == 1
        assert profiles[0]["url"] == "https://www.linkedin.com/in/sarah-jones-a1b2"

    def test_skips_company_pages(self):
        text = "1. Acme Law\nURL: https://www.linkedin.com/company/acme-law\nSnippet: x"
        profiles = _parse_profiles_from_serp(text, "Acme Law")
        assert profiles == []

    def test_returns_empty_on_no_results(self):
        assert _parse_profiles_from_serp("No results found", "Acme") == []
        assert _parse_profiles_from_serp("", "Acme") == []

    def test_skips_invalid_slug(self):
        text = "1. Some Title\nURL: https://www.linkedin.com/in/ab\nSnippet: x"
        profiles = _parse_profiles_from_serp(text, "Acme")
        assert profiles == []


# ── Search query building ─────────────────────────────────────────────────────

class TestBuildSearchQueries:

    def _company(self, name: str, website: str = "") -> dict:
        return {"name": name, "website": website, "linkedin": "", "rating": "7",
                "notes": "", "size": "", "hq": "", "sheet_row": 2}

    def test_queries_target_linkedin_in(self):
        queries = _build_search_queries(self._company("Kingsley Napley"), tab="LawFirms")
        assert all("site:linkedin.com/in" in q for q in queries
                   if "site:" in q and "linkedin" in q)

    def test_queries_contain_company_name(self):
        queries = _build_search_queries(self._company("Kingsley Napley LLP"), tab="LawFirms")
        assert any("Kingsley Napley" in q for q in queries)

    def test_queries_strip_legal_suffix(self):
        queries = _build_search_queries(self._company("Bindmans LLP"), tab="LawFirms")
        # Should use "Bindmans" not "Bindmans LLP" in most queries
        assert any("Bindmans" in q for q in queries)

    def test_adds_website_query_when_domain_known(self):
        c = self._company("Acme Law", website="https://acmelaw.co.uk")
        queries = _build_search_queries(c, tab="LawFirms")
        website_queries = [q for q in queries if "site:acmelaw.co.uk" in q]
        assert len(website_queries) >= 1

    def test_no_website_query_without_domain(self):
        c = self._company("Acme Law", website="")
        queries = _build_search_queries(c, tab="LawFirms")
        website_queries = [q for q in queries if "site:" in q and "linkedin" not in q]
        assert website_queries == []

    @pytest.mark.parametrize("tab", ["LawFirms", "Advisors", "Charities", "LegaltechBrokers"])
    def test_all_tabs_produce_queries(self, tab):
        c = self._company("Test Firm")
        queries = _build_search_queries(c, tab=tab)
        assert len(queries) >= 2

    def test_queries_differ_by_tab(self):
        c = self._company("Test Firm")
        q_law = _build_search_queries(c, tab="LawFirms")
        q_charity = _build_search_queries(c, tab="Charities")
        assert q_law != q_charity

    def test_lawfirms_queries_mention_immigration_roles(self):
        c = self._company("Kingsley Napley")
        queries = _build_search_queries(c, tab="LawFirms")
        all_text = " ".join(queries)
        assert "Immigration" in all_text or "Partner" in all_text

    def test_charities_queries_mention_ceo_or_director(self):
        c = self._company("Refugee Action")
        queries = _build_search_queries(c, tab="Charities")
        all_text = " ".join(queries)
        assert "CEO" in all_text or "Director" in all_text


class TestBuildFallbackQueries:

    def test_includes_broad_linkedin_query(self):
        company = {"name": "Acme Law LLP", "website": ""}
        queries = _build_fallback_queries(company)
        assert any("site:linkedin.com/in" in q for q in queries)
        assert any("Acme Law" in q for q in queries)

    def test_includes_website_query_when_domain_known(self):
        company = {"name": "Acme Law", "website": "https://acmelaw.co.uk"}
        queries = _build_fallback_queries(company)
        assert any("site:acmelaw.co.uk" in q for q in queries)


# ── Sheet reading ─────────────────────────────────────────────────────────────

class TestReadAndFilter:

    def _make_service_mock(self, rows: list[list[str]]) -> MagicMock:
        mock_service = MagicMock()
        (mock_service.spreadsheets().values().get().execute
         .return_value) = {"values": rows}
        return mock_service

    @patch("agents.immigration_contact_agent.build")
    @patch("agents.immigration_contact_agent.Credentials")
    def test_filters_by_min_rating(self, mock_creds, mock_build):
        rows = [
            ["Name", "", "Rating", "Notes", "Website", "LinkedIn", "Size", "HQ"],
            ["High Rated", "", "7", "desc", "https://a.com", "", "", "London"],
            ["Low Rated",  "", "3", "desc", "https://b.com", "", "", "London"],
        ]
        mock_build.return_value = self._make_service_mock(rows)
        result = _read_and_filter("sheet-id", "creds.json", "LawFirms", min_rating=5)
        names = [r["name"] for r in result]
        assert "High Rated" in names
        assert "Low Rated" not in names

    @patch("agents.immigration_contact_agent.build")
    @patch("agents.immigration_contact_agent.Credentials")
    def test_skips_unrated_rows(self, mock_creds, mock_build):
        rows = [
            ["Name", "", "Rating", "Notes", "Website", "LinkedIn", "Size", "HQ"],
            ["Unrated", "", "", "desc", "https://a.com", "", "", "London"],
            ["Provisional", "", "~6", "desc", "https://b.com", "", "", "London"],
        ]
        mock_build.return_value = self._make_service_mock(rows)
        result = _read_and_filter("sheet-id", "creds.json", "LawFirms", min_rating=5)
        assert result == []

    @patch("agents.immigration_contact_agent.build")
    @patch("agents.immigration_contact_agent.Credentials")
    def test_skips_empty_name_rows(self, mock_creds, mock_build):
        rows = [
            ["Name", "", "Rating", "Notes", "Website", "LinkedIn", "Size", "HQ"],
            ["", "", "8", "", "", "", "", ""],
        ]
        mock_build.return_value = self._make_service_mock(rows)
        result = _read_and_filter("sheet-id", "creds.json", "LawFirms", min_rating=5)
        assert result == []

    @patch("agents.immigration_contact_agent.build")
    @patch("agents.immigration_contact_agent.Credentials")
    def test_returns_all_fields(self, mock_creds, mock_build):
        rows = [
            ["Name", "", "Rating", "Notes", "Website", "LinkedIn", "Size", "HQ"],
            ["Fragomen", "", "9", "Top firm", "https://fragomen.com", "https://linkedin.com/company/fragomen", "1000+", "London"],
        ]
        mock_build.return_value = self._make_service_mock(rows)
        result = _read_and_filter("sheet-id", "creds.json", "LawFirms", min_rating=5)
        assert len(result) == 1
        c = result[0]
        assert c["name"] == "Fragomen"
        assert c["rating"] == "9"
        assert c["website"] == "https://fragomen.com"
        assert c["linkedin"] == "https://linkedin.com/company/fragomen"
        assert c["hq"] == "London"
        assert c["sheet_row"] == 2

    @patch("agents.immigration_contact_agent.build")
    @patch("agents.immigration_contact_agent.Credentials")
    def test_returns_empty_on_header_only(self, mock_creds, mock_build):
        rows = [["Name", "", "Rating", "Notes", "Website", "LinkedIn", "Size", "HQ"]]
        mock_build.return_value = self._make_service_mock(rows)
        result = _read_and_filter("sheet-id", "creds.json", "LawFirms", min_rating=5)
        assert result == []

    @patch("agents.immigration_contact_agent.build")
    @patch("agents.immigration_contact_agent.Credentials")
    def test_exact_min_rating_included(self, mock_creds, mock_build):
        rows = [
            ["Name", "", "Rating", "Notes", "Website", "LinkedIn", "Size", "HQ"],
            ["Exactly Five", "", "5", "desc", "", "", "", ""],
        ]
        mock_build.return_value = self._make_service_mock(rows)
        result = _read_and_filter("sheet-id", "creds.json", "LawFirms", min_rating=5)
        assert len(result) == 1


# ── CSV output ────────────────────────────────────────────────────────────────

class TestWriteCsv:

    def _companies(self) -> list[dict]:
        return [
            {
                "name": "Kingsley Napley",
                "rating": "8",
                "website": "https://kingsleynapley.co.uk",
                "linkedin": "https://linkedin.com/company/kingsley-napley",
            },
        ]

    def _profiles(self) -> dict[str, list[dict]]:
        return {
            "Kingsley Napley": [
                {
                    "url": "https://www.linkedin.com/in/sarah-jones",
                    "first_name": "Sarah",
                    "last_name": "Jones",
                    "title_hint": "Managing Partner at Kingsley Napley",
                    "source_url": "",
                },
            ],
        }

    def test_writes_header_row(self):
        buf = io.StringIO()
        _write_csv({}, [], buf)
        buf.seek(0)
        reader = csv.reader(buf)
        header = next(reader)
        assert header == _CSV_HEADERS

    def test_writes_profile_row(self):
        buf = io.StringIO()
        rows = _write_csv(self._profiles(), self._companies(), buf)
        assert rows == 1
        buf.seek(0)
        reader = csv.reader(buf)
        next(reader)  # header
        row = next(reader)
        assert row[0] == "https://www.linkedin.com/in/sarah-jones"
        assert row[1] == "Sarah"
        assert row[2] == "Jones"
        assert row[3] == "Kingsley Napley"

    def test_includes_company_linkedin_url(self):
        buf = io.StringIO()
        _write_csv(self._profiles(), self._companies(), buf)
        buf.seek(0)
        content = buf.read()
        assert "linkedin.com/company/kingsley-napley" in content

    def test_includes_rating(self):
        buf = io.StringIO()
        _write_csv(self._profiles(), self._companies(), buf)
        buf.seek(0)
        content = buf.read()
        assert "8" in content

    def test_matched_role_in_role_column(self):
        buf = io.StringIO()
        _write_csv(self._profiles(), self._companies(), buf)
        buf.seek(0)
        reader = csv.reader(buf)
        next(reader)
        row = next(reader)
        role_col = _CSV_HEADERS.index("Role")
        assert row[role_col] == "Managing Partner"

    def test_empty_profiles_writes_header_only(self):
        buf = io.StringIO()
        rows = _write_csv({"Acme": []}, [{"name": "Acme"}], buf)
        assert rows == 0
        buf.seek(0)
        reader = csv.reader(buf)
        assert next(reader) == _CSV_HEADERS
        assert list(reader) == []

    def test_multiple_profiles_per_company(self):
        profiles = {
            "Kingsley Napley": [
                {"url": "https://www.linkedin.com/in/a", "first_name": "A", "last_name": "B",
                 "title_hint": "Managing Partner", "source_url": ""},
                {"url": "https://www.linkedin.com/in/c", "first_name": "C", "last_name": "D",
                 "title_hint": "Head of Immigration", "source_url": ""},
            ]
        }
        buf = io.StringIO()
        rows = _write_csv(profiles, self._companies(), buf)
        assert rows == 2

    def test_returns_correct_row_count(self):
        buf = io.StringIO()
        rows = _write_csv(self._profiles(), self._companies(), buf)
        assert rows == 1


# ── LinkedIn API helpers ──────────────────────────────────────────────────────

class TestLinkedInHeaders:

    def test_csrf_token_strips_quotes(self):
        headers = _linkedin_headers("my_li_at", '"my_jsessionid"')
        assert headers["Csrf-Token"] == "my_jsessionid"

    def test_cookie_contains_li_at(self):
        headers = _linkedin_headers("token123", "sess456")
        assert "li_at=token123" in headers["Cookie"]

    def test_cookie_contains_jsessionid(self):
        headers = _linkedin_headers("token123", "sess456")
        assert "JSESSIONID=" in headers["Cookie"]

    def test_restli_protocol_version_set(self):
        headers = _linkedin_headers("a", "b")
        assert headers["X-RestLi-Protocol-Version"] == "2.0.0"


class TestExtractCompanySlug:

    def test_standard_url(self):
        assert _extract_company_slug("https://www.linkedin.com/company/kingsley-napley") == "kingsley-napley"

    def test_trailing_slash(self):
        assert _extract_company_slug("https://linkedin.com/company/fragomen/") == "fragomen"

    def test_url_with_subpath(self):
        assert _extract_company_slug("https://linkedin.com/company/bindmans/about") == "bindmans"

    def test_returns_none_for_invalid(self):
        assert _extract_company_slug("https://example.com") is None
        assert _extract_company_slug("") is None

    def test_profile_url_returns_none(self):
        assert _extract_company_slug("https://linkedin.com/in/sarah-jones") is None


class TestParseLinkedinPeopleResponse:

    def _make_response(self, people: list[dict]) -> dict:
        """Build a minimal Voyager search/blended response."""
        elements = []
        for person in people:
            elements.append({
                "elements": [{
                    "targetUrn": f"urn:li:member:{person.get('id', '123')}",
                    "hitInfo": {
                        "com.linkedin.voyager.search.SearchProfile": {
                            "miniProfile": {
                                "firstName": person.get("first_name", ""),
                                "lastName":  person.get("last_name", ""),
                                "publicIdentifier": person.get("slug", ""),
                                "occupation": person.get("occupation", ""),
                            }
                        }
                    }
                }]
            })
        return {"elements": elements}

    def test_parses_basic_profile(self):
        data = self._make_response([{
            "first_name": "Sarah", "last_name": "Jones",
            "slug": "sarah-jones", "occupation": "Managing Partner",
        }])
        profiles = _parse_linkedin_people_response(data)
        assert len(profiles) == 1
        assert profiles[0]["first_name"] == "Sarah"
        assert profiles[0]["last_name"] == "Jones"
        assert profiles[0]["url"] == "https://www.linkedin.com/in/sarah-jones"
        assert profiles[0]["title_hint"] == "Managing Partner"

    def test_builds_linkedin_url_from_slug(self):
        data = self._make_response([{
            "first_name": "John", "last_name": "Smith",
            "slug": "john-smith-abc", "occupation": "Partner",
        }])
        profiles = _parse_linkedin_people_response(data)
        assert profiles[0]["url"] == "https://www.linkedin.com/in/john-smith-abc"

    def test_skips_empty_names(self):
        data = self._make_response([
            {"first_name": "", "last_name": "", "slug": "nobody", "occupation": ""},
        ])
        profiles = _parse_linkedin_people_response(data)
        assert profiles == []

    def test_empty_response(self):
        assert _parse_linkedin_people_response({}) == []
        assert _parse_linkedin_people_response({"elements": []}) == []

    def test_multiple_profiles(self):
        data = self._make_response([
            {"first_name": "Alice", "last_name": "Brown", "slug": "alice-b", "occupation": "Partner"},
            {"first_name": "Bob",   "last_name": "Smith", "slug": "bob-s",   "occupation": "Director"},
        ])
        profiles = _parse_linkedin_people_response(data)
        assert len(profiles) == 2

    def test_no_slug_gives_empty_url(self):
        data = self._make_response([{
            "first_name": "Jane", "last_name": "Doe", "slug": "", "occupation": "CEO",
        }])
        profiles = _parse_linkedin_people_response(data)
        assert profiles[0]["url"] == ""


class TestMergeProfiles:

    def _p(self, url: str, first: str, last: str, title: str = "") -> dict:
        return {"url": url, "first_name": first, "last_name": last,
                "title_hint": title, "source_url": ""}

    def test_adds_new_profile(self):
        existing = [self._p("https://linkedin.com/in/a", "A", "B", "Managing Partner")]
        new = [self._p("https://linkedin.com/in/c", "C", "D", "Partner")]
        merged = _merge_profiles(existing, new, max_profiles=5)
        assert len(merged) == 2

    def test_deduplicates_by_url(self):
        url = "https://linkedin.com/in/sarah"
        existing = [self._p(url, "Sarah", "Jones", "Managing Partner")]
        new      = [self._p(url, "Sarah", "Jones", "Partner")]
        merged = _merge_profiles(existing, new, max_profiles=5)
        assert len(merged) == 1

    def test_deduplicates_by_name(self):
        existing = [self._p("https://linkedin.com/in/a", "Sarah", "Jones", "Partner")]
        new      = [self._p("https://linkedin.com/in/b", "Sarah", "Jones", "Director")]
        merged = _merge_profiles(existing, new, max_profiles=5)
        assert len(merged) == 1

    def test_respects_max_profiles(self):
        existing = [self._p(f"https://linkedin.com/in/{i}", f"P{i}", "X") for i in range(3)]
        new      = [self._p(f"https://linkedin.com/in/new{i}", f"N{i}", "Y") for i in range(3)]
        merged = _merge_profiles(existing, new, max_profiles=4)
        assert len(merged) == 4

    def test_sorts_by_role_priority(self):
        existing = [self._p("https://linkedin.com/in/a", "A", "B", "Senior Associate")]
        new      = [self._p("https://linkedin.com/in/c", "C", "D", "Managing Partner")]
        merged = _merge_profiles(existing, new, max_profiles=5)
        assert merged[0]["title_hint"] == "Managing Partner"

    def test_empty_existing(self):
        new = [self._p("https://linkedin.com/in/a", "A", "B", "Partner")]
        merged = _merge_profiles([], new, max_profiles=5)
        assert len(merged) == 1

    def test_empty_new(self):
        existing = [self._p("https://linkedin.com/in/a", "A", "B", "Partner")]
        merged = _merge_profiles(existing, [], max_profiles=5)
        assert len(merged) == 1


class TestTabLinkedinRoles:

    def test_all_tabs_have_roles(self):
        for tab in cfg.IMMIGRATION_TABS:
            assert tab in _TAB_LINKEDIN_ROLES, f"Missing LinkedIn roles for tab {tab!r}"

    def test_lawfirms_includes_managing_partner(self):
        assert any("Managing Partner" in r for r in _TAB_LINKEDIN_ROLES["LawFirms"])

    def test_advisors_includes_director_or_owner(self):
        roles = _TAB_LINKEDIN_ROLES["Advisors"]
        assert any("Director" in r or "Owner" in r for r in roles)

    def test_charities_includes_ceo_or_director(self):
        roles = _TAB_LINKEDIN_ROLES["Charities"]
        assert any("CEO" in r or "Director" in r for r in roles)

    def test_each_tab_has_multiple_roles(self):
        for tab, roles in _TAB_LINKEDIN_ROLES.items():
            assert len(roles) >= 2, f"Tab {tab!r} should have at least 2 role keywords"

"""
Tests for store.py — ResultStore, CSV export helpers, _extract_domain.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from store import ResultStore, _extract_domain, to_lemlist_csv, to_waalaxy_csv


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    return ResultStore.__new__(ResultStore) | _make_store(tmp_path, "test-campaign")


def _make_store(tmp_path, campaign_id):
    s = ResultStore(campaign_id)
    s._dir  = tmp_path / campaign_id
    s._path = s._dir / "results.json"
    return s


@pytest.fixture
def fresh(tmp_path):
    return _make_store(tmp_path, "camp")


def _company(name="Acme Ltd", website="https://acme.co.uk", **kw):
    return {"name": name, "website": website, **kw}


# ── _extract_domain ───────────────────────────────────────────────────────────

class TestExtractDomain:
    def test_strips_https(self):
        assert _extract_domain("https://example.co.uk") == "example.co.uk"

    def test_strips_http(self):
        assert _extract_domain("http://example.com") == "example.com"

    def test_strips_www(self):
        assert _extract_domain("https://www.example.com") == "example.com"

    def test_strips_trailing_slash(self):
        assert _extract_domain("https://example.com/") == "example.com"

    def test_strips_path(self):
        assert _extract_domain("https://example.com/about") == "example.com"

    def test_strips_query_string(self):
        assert _extract_domain("https://example.com?foo=bar") == "example.com"

    def test_empty_string(self):
        assert _extract_domain("") == ""

    def test_none_type_safe(self):
        assert _extract_domain(None) == ""  # type: ignore[arg-type]

    def test_non_string_safe(self):
        assert _extract_domain(123) == ""  # type: ignore[arg-type]


# ── append_company ────────────────────────────────────────────────────────────

class TestAppendCompany:
    def test_adds_new_company(self, fresh):
        assert fresh.append_company("LawFirms", _company()) is True
        rows = fresh.get_segment("LawFirms")
        assert len(rows) == 1
        assert rows[0]["name"] == "Acme Ltd"

    def test_assigns_row_index_starting_at_1(self, fresh):
        fresh.append_company("LawFirms", _company())
        assert fresh.get_segment("LawFirms")[0]["row_index"] == 1

    def test_increments_row_index(self, fresh):
        fresh.append_company("LawFirms", _company("A", "https://a.com"))
        fresh.append_company("LawFirms", _company("B", "https://b.com"))
        indices = [r["row_index"] for r in fresh.get_segment("LawFirms")]
        assert indices == [1, 2]

    def test_dedup_by_name(self, fresh):
        fresh.append_company("LawFirms", _company("Acme Ltd"))
        added = fresh.append_company("LawFirms", _company("Acme Ltd", "https://other.com"))
        assert added is False
        assert len(fresh.get_segment("LawFirms")) == 1

    def test_dedup_name_case_insensitive(self, fresh):
        fresh.append_company("LawFirms", _company("Acme Ltd"))
        assert fresh.append_company("LawFirms", _company("ACME LTD")) is False

    def test_dedup_by_domain(self, fresh):
        fresh.append_company("LawFirms", _company("Acme", "https://acme.co.uk"))
        assert fresh.append_company("LawFirms", _company("Acme 2", "https://acme.co.uk/page")) is False

    def test_dedup_across_segments(self, fresh):
        fresh.append_company("LawFirms", _company("Acme Ltd"))
        assert fresh.append_company("Advisors", _company("Acme Ltd")) is False

    def test_empty_name_rejected(self, fresh):
        assert fresh.append_company("LawFirms", {"name": "", "website": "https://x.com"}) is False

    def test_initialises_signals_and_contacts(self, fresh):
        fresh.append_company("LawFirms", _company())
        row = fresh.get_segment("LawFirms")[0]
        assert row["signals"] == {}
        assert row["contacts"] == []

    def test_creates_segment_if_absent(self, fresh):
        fresh.append_company("NewSegment", _company())
        assert "NewSegment" in fresh.all_segments()


# ── update_company ────────────────────────────────────────────────────────────

class TestUpdateCompany:
    def test_updates_field(self, fresh):
        fresh.append_company("S", _company())
        idx = fresh.get_segment("S")[0]["row_index"]
        fresh.update_company("S", idx, {"notes": "Great firm"})
        assert fresh.get_segment("S")[0]["notes"] == "Great firm"

    def test_returns_false_for_missing_row(self, fresh):
        assert fresh.update_company("S", 999, {"notes": "x"}) is False

    def test_cannot_overwrite_row_index(self, fresh):
        fresh.append_company("S", _company())
        idx = fresh.get_segment("S")[0]["row_index"]
        fresh.update_company("S", idx, {"row_index": 999})
        assert fresh.get_segment("S")[0]["row_index"] == idx

    def test_cannot_overwrite_signals(self, fresh):
        fresh.append_company("S", _company())
        idx = fresh.get_segment("S")[0]["row_index"]
        fresh.update_company("S", idx, {"signals": {"k": "v"}})
        assert fresh.get_segment("S")[0]["signals"] == {}


# ── update_signal ─────────────────────────────────────────────────────────────

class TestUpdateSignal:
    def test_writes_yes(self, fresh):
        fresh.append_company("S", _company())
        idx = fresh.get_segment("S")[0]["row_index"]
        fresh.update_signal("S", idx, "corporate", "Yes", "source.com")
        sig = fresh.get_segment("S")[0]["signals"]["corporate"]
        assert sig == {"value": "Yes", "source": "source.com"}

    def test_writes_no(self, fresh):
        fresh.append_company("S", _company())
        idx = fresh.get_segment("S")[0]["row_index"]
        fresh.update_signal("S", idx, "corporate", "No", "not found")
        assert fresh.get_segment("S")[0]["signals"]["corporate"]["value"] == "No"

    def test_rejects_invalid_value(self, fresh):
        fresh.append_company("S", _company())
        idx = fresh.get_segment("S")[0]["row_index"]
        result = fresh.update_signal("S", idx, "corporate", "Maybe", "")
        assert result is False
        assert "corporate" not in fresh.get_segment("S")[0]["signals"]

    def test_returns_false_for_missing_row(self, fresh):
        assert fresh.update_signal("S", 999, "k", "Yes", "") is False


# ── set_contacts ──────────────────────────────────────────────────────────────

class TestSetContacts:
    def test_sets_contacts(self, fresh):
        fresh.append_company("S", _company())
        idx = fresh.get_segment("S")[0]["row_index"]
        fresh.set_contacts("S", idx, ["Alice Smith | Partner | https://li.com/alice"])
        assert len(fresh.get_segment("S")[0]["contacts"]) == 1

    def test_replaces_existing_contacts(self, fresh):
        fresh.append_company("S", _company())
        idx = fresh.get_segment("S")[0]["row_index"]
        fresh.set_contacts("S", idx, ["A | Role | url1"])
        fresh.set_contacts("S", idx, ["B | Role | url2", "C | Role | url3"])
        assert len(fresh.get_segment("S")[0]["contacts"]) == 2

    def test_coerces_non_strings(self, fresh):
        fresh.append_company("S", _company())
        idx = fresh.get_segment("S")[0]["row_index"]
        fresh.set_contacts("S", idx, [123, None])  # type: ignore[list-item]
        assert all(isinstance(c, str) for c in fresh.get_segment("S")[0]["contacts"])


# ── atomic write / corruption resilience ─────────────────────────────────────

class TestAtomicWrite:
    def test_no_tmp_file_left_after_write(self, fresh):
        fresh.append_company("S", _company())
        assert not list(fresh._dir.glob("*.tmp"))

    def test_load_returns_empty_on_corrupt_json(self, fresh):
        fresh._dir.mkdir(parents=True, exist_ok=True)
        fresh._path.write_text("{ not valid json", encoding="utf-8")
        assert fresh.all_segments() == {}


# ── CSV export ────────────────────────────────────────────────────────────────

def _row_with_contacts(**kw):
    return {
        "name": "Firm A", "linkedin": "https://li.com/firm-a",
        "website": "https://firma.co.uk", "rating": "8",
        "contacts": ["Alice Smith | Partner | https://li.com/alice"],
        **kw,
    }


class TestWaalaxyCSV:
    def test_header_row(self):
        csv = to_waalaxy_csv([])
        assert csv.startswith("LinkedIn URL,First Name,Last Name")

    def test_contact_row(self):
        csv = to_waalaxy_csv([_row_with_contacts()])
        assert "Alice" in csv
        assert "Smith" in csv
        assert "Partner" in csv

    def test_empty_rows(self):
        csv = to_waalaxy_csv([])
        lines = csv.strip().splitlines()
        assert len(lines) == 1  # header only

    def test_row_without_contacts_produces_no_data_rows(self):
        csv = to_waalaxy_csv([{"name": "X", "contacts": []}])
        lines = csv.strip().splitlines()
        assert len(lines) == 1

    def test_contact_missing_url_handled(self):
        row = {"name": "X", "rating": "7", "linkedin": "", "website": "",
               "contacts": ["Bob Jones | CEO"]}  # no URL part
        csv = to_waalaxy_csv([row])
        assert "Bob" in csv

    def test_multiple_contacts(self):
        row = _row_with_contacts(contacts=[
            "Alice Smith | Partner | https://li.com/a",
            "Bob Jones | CEO | https://li.com/b",
        ])
        lines = to_waalaxy_csv([row]).strip().splitlines()
        assert len(lines) == 3  # header + 2 contacts


class TestLemlistCSV:
    def test_header_row(self):
        csv = to_lemlist_csv([])
        assert csv.startswith("firstName,lastName")

    def test_domain_extracted_from_website(self):
        csv = to_lemlist_csv([_row_with_contacts()])
        assert "firma.co.uk" in csv

    def test_empty_rows(self):
        lines = to_lemlist_csv([]).strip().splitlines()
        assert len(lines) == 1

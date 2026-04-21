"""
Tests for campaign.py — Pydantic schema, validators, persistence helpers.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from campaign import Campaign, RatingConfig, Region, Signal, SearchConfig, ContactConfig, _resolve_env


# ── _resolve_env ──────────────────────────────────────────────────────────────

class TestResolveEnv:
    def test_plain_value_returned_as_is(self):
        assert _resolve_env("my-token") == "my-token"

    def test_dollar_prefix_resolves_env(self, monkeypatch):
        monkeypatch.setenv("MY_TOKEN", "secret")
        assert _resolve_env("$MY_TOKEN") == "secret"

    def test_missing_env_var_returns_empty(self, monkeypatch):
        monkeypatch.delenv("MISSING_VAR", raising=False)
        assert _resolve_env("$MISSING_VAR") == ""

    def test_empty_string_returns_empty(self):
        assert _resolve_env("") == ""


# ── Signal validator ──────────────────────────────────────────────────────────

class TestSignalValidator:
    def test_valid_signal(self):
        s = Signal(key="corp", name="Corporate", points=3)
        assert s.points == 3

    def test_points_above_10_raises(self):
        with pytest.raises(ValidationError):
            Signal(key="x", name="X", points=11)

    def test_points_below_minus10_raises(self):
        with pytest.raises(ValidationError):
            Signal(key="x", name="X", points=-11)

    def test_negative_points_allowed(self):
        s = Signal(key="x", name="X", points=-3)
        assert s.points == -3


# ── RatingConfig validator ────────────────────────────────────────────────────

class TestRatingConfig:
    def test_threshold_clamped_to_1_min(self):
        r = RatingConfig(contact_threshold=0)
        assert r.contact_threshold == 1

    def test_threshold_clamped_to_10_max(self):
        r = RatingConfig(contact_threshold=15)
        assert r.contact_threshold == 10

    def test_valid_threshold(self):
        assert RatingConfig(contact_threshold=8).contact_threshold == 8


# ── Campaign.id validator ─────────────────────────────────────────────────────

class TestCampaignIdValidator:
    def test_valid_id(self):
        c = Campaign(id="my-campaign", name="Test")
        assert c.id == "my-campaign"

    def test_id_with_underscores(self):
        c = Campaign(id="my_campaign_2", name="Test")
        assert c.id == "my_campaign_2"

    def test_empty_id_raises(self):
        with pytest.raises(ValidationError):
            Campaign(id="", name="Test")

    def test_id_starting_with_hyphen_raises(self):
        with pytest.raises(ValidationError):
            Campaign(id="-bad", name="Test")

    def test_id_with_spaces_raises(self):
        with pytest.raises(ValidationError):
            Campaign(id="has spaces", name="Test")

    def test_id_stripped_of_whitespace(self):
        c = Campaign(id="  mycampaign  ", name="Test")
        assert c.id == "mycampaign"


# ── Campaign.export_format validator ─────────────────────────────────────────

class TestExportFormatValidator:
    def test_valid_waalaxy(self):
        assert Campaign(id="x", name="T", export_format="waalaxy").export_format == "waalaxy"

    def test_valid_lemlist(self):
        assert Campaign(id="x", name="T", export_format="lemlist").export_format == "lemlist"

    def test_invalid_falls_back_to_waalaxy(self):
        assert Campaign(id="x", name="T", export_format="garbage").export_format == "waalaxy"


# ── Campaign search / contact config ─────────────────────────────────────────

class TestCampaignSearchContact:
    def test_search_defaults_empty(self):
        c = Campaign(id="x", name="T")
        assert c.search.tld_queries == []
        assert c.search.extra_queries == []

    def test_contact_defaults_empty(self):
        c = Campaign(id="x", name="T")
        assert c.contact.roles == []

    def test_search_queries_stored(self):
        c = Campaign(id="x", name="T",
                     search=SearchConfig(tld_queries=["immigration law firms"], extra_queries=["visa lawyers UK"]))
        assert c.search.tld_queries == ["immigration law firms"]
        assert c.search.extra_queries == ["visa lawyers UK"]

    def test_contact_roles_stored(self):
        c = Campaign(id="x", name="T", contact=ContactConfig(roles=["Managing Partner", "Head of Immigration"]))
        assert c.contact.roles == ["Managing Partner", "Head of Immigration"]


# ── Campaign derived helpers ──────────────────────────────────────────────────

class TestCampaignHelpers:
    def _campaign(self):
        return Campaign(
            id="test",
            name="Test",
            signals=[
                Signal(key="corp", name="Corporate", points=3),
                Signal(key="growth", name="Growth", points=1),
            ],
        )

    def test_serp_params_returns_gl_and_cr(self):
        c = Campaign(id="x", name="T",
                     region=Region(country_code="gb", country_restrict="countryGB"))
        assert c.serp_params() == {"gl": "gb", "cr": "countryGB"}

    def test_signals_list(self):
        c = self._campaign()
        assert len(c.signals) == 2
        assert c.signals[0].key == "corp"


# ── Campaign persistence ──────────────────────────────────────────────────────

class TestCampaignPersistence:
    def test_save_and_load_roundtrip(self, tmp_path):
        c = Campaign(id="camp1", name="Test Campaign",
                     signals=[Signal(key="s", name="S", points=2)])
        c.save(campaigns_dir=tmp_path)
        loaded = Campaign.load("camp1", campaigns_dir=tmp_path)
        assert loaded.name == "Test Campaign"
        assert loaded.signals[0].key == "s"

    def test_search_config_survives_roundtrip(self, tmp_path):
        c = Campaign(id="c", name="T",
                     search=SearchConfig(tld_queries=["law firms uk"]))
        c.save(campaigns_dir=tmp_path)
        loaded = Campaign.load("c", campaigns_dir=tmp_path)
        assert loaded.search.tld_queries == ["law firms uk"]

    def test_contact_config_survives_roundtrip(self, tmp_path):
        c = Campaign(id="c2", name="T",
                     contact=ContactConfig(roles=["Managing Partner"]))
        c.save(campaigns_dir=tmp_path)
        loaded = Campaign.load("c2", campaigns_dir=tmp_path)
        assert loaded.contact.roles == ["Managing Partner"]

    def test_save_is_atomic_no_tmp_left(self, tmp_path):
        Campaign(id="c", name="T").save(campaigns_dir=tmp_path)
        assert not list(tmp_path.glob("*.tmp"))

    def test_load_missing_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            Campaign.load("nonexistent", campaigns_dir=tmp_path)

    def test_load_corrupt_json_raises_value_error(self, tmp_path):
        (tmp_path / "bad.json").write_text("{ not json }", encoding="utf-8")
        with pytest.raises(ValueError, match="invalid config"):
            Campaign.load("bad", campaigns_dir=tmp_path)

    def test_list_all_skips_corrupt_files(self, tmp_path):
        Campaign(id="good", name="Good").save(campaigns_dir=tmp_path)
        (tmp_path / "bad.json").write_text("{ broken", encoding="utf-8")
        campaigns = Campaign.list_all(campaigns_dir=tmp_path)
        assert len(campaigns) == 1
        assert campaigns[0].id == "good"

    def test_list_all_returns_empty_for_empty_dir(self, tmp_path):
        assert Campaign.list_all(campaigns_dir=tmp_path) == []

    def test_delete(self, tmp_path):
        c = Campaign(id="del", name="T")
        c.save(campaigns_dir=tmp_path)
        assert (tmp_path / "del.json").exists()
        c.delete(campaigns_dir=tmp_path)
        assert not (tmp_path / "del.json").exists()

    def test_delete_missing_is_silent(self, tmp_path):
        Campaign(id="x", name="T").delete(campaigns_dir=tmp_path)  # no error

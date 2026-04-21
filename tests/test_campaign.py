"""
Tests for campaign.py — Pydantic schema, validators, persistence helpers.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from campaign import Campaign, RatingConfig, Region, Segment, Signal, _col_letter, _resolve_env


# ── _col_letter ───────────────────────────────────────────────────────────────

class TestColLetter:
    def test_zero_is_A(self):
        assert _col_letter(0) == "A"

    def test_25_is_Z(self):
        assert _col_letter(25) == "Z"

    def test_26_is_AA(self):
        assert _col_letter(26) == "AA"

    def test_51_is_AZ(self):
        assert _col_letter(51) == "AZ"

    def test_52_is_BA(self):
        assert _col_letter(52) == "BA"


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


# ── Segment validator ─────────────────────────────────────────────────────────

class TestSegmentValidator:
    def test_spaces_replaced_with_underscores(self):
        seg = Segment(name="Law Firms")
        assert seg.name == "Law_Firms"

    def test_leading_trailing_spaces_stripped(self):
        seg = Segment(name="  Advisors  ")
        assert seg.name == "Advisors"


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
            segments=[
                Segment(name="LawFirms"),
                Segment(name="Advisors"),
            ],
        )

    def test_signal_cols_keys(self):
        cols = self._campaign().signal_cols()
        assert set(cols.keys()) == {"corp", "growth"}

    def test_signal_cols_start_at_J(self):
        cols = self._campaign().signal_cols()
        assert cols["corp"][0] == "J"   # index 9 → J
        assert cols["corp"][1] == "K"
        assert cols["growth"][0] == "L"

    def test_contacts_col_after_signals(self):
        c = self._campaign()
        # 2 signals × 2 cols each = 4 cols after index 9 → index 13 → N
        assert c.contacts_col_idx() == 13

    def test_segment_names(self):
        assert self._campaign().segment_names() == ["LawFirms", "Advisors"]

    def test_segment_lookup(self):
        seg = self._campaign().segment("LawFirms")
        assert seg.name == "LawFirms"

    def test_segment_lookup_missing_raises_key_error(self):
        with pytest.raises(KeyError):
            self._campaign().segment("DoesNotExist")

    def test_signal_tab_names_only_enabled(self):
        c = Campaign(id="x", name="T", segments=[
            Segment(name="A", signals_enabled=True),
            Segment(name="B", signals_enabled=False),
        ])
        assert c.signal_tab_names() == {"A"}


# ── Campaign persistence ──────────────────────────────────────────────────────

class TestCampaignPersistence:
    def test_save_and_load_roundtrip(self, tmp_path):
        c = Campaign(id="camp1", name="Test Campaign",
                     signals=[Signal(key="s", name="S", points=2)])
        c.save(campaigns_dir=tmp_path)
        loaded = Campaign.load("camp1", campaigns_dir=tmp_path)
        assert loaded.name == "Test Campaign"
        assert loaded.signals[0].key == "s"

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

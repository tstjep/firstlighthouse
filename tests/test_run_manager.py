"""
Tests for run_manager.py — state I/O, validation, log tailing.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import run_manager as rm


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def patch_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(rm, "DATA_DIR", tmp_path)


def _state(**kw):
    defaults = dict(
        run_id="20240101-120000",
        campaign_id="camp",
        mode="search",
        steps=["search"],
        status="running",
        current_step="search",
        started_at="2024-01-01T12:00:00",
        finished_at="",
        error="",
        log_path="",
    )
    defaults.update(kw)
    return rm.RunState(**defaults)


# ── validate_run_request ──────────────────────────────────────────────────────

class TestValidateRunRequest:
    def test_valid_request(self):
        errors = rm.validate_run_request("camp", "search")
        assert errors == []

    def test_invalid_mode(self):
        errors = rm.validate_run_request("camp", "nonsense")
        assert any("mode" in e.lower() or "nonsense" in e for e in errors)

    def test_blocks_concurrent_run(self, tmp_path):
        state = _state(campaign_id="camp")
        rm._save_state(state)
        errors = rm.validate_run_request("camp", "search")
        assert any("already in progress" in e for e in errors)

    def test_allows_run_when_done(self, tmp_path):
        state = _state(campaign_id="camp", status="done")
        rm._save_state(state)
        errors = rm.validate_run_request("camp", "search")
        assert errors == []

    def test_allows_run_when_error(self, tmp_path):
        state = _state(campaign_id="camp", status="error")
        rm._save_state(state)
        errors = rm.validate_run_request("camp", "search")
        assert errors == []

    def test_all_modes_valid(self):
        for mode in rm.MODES:
            errors = rm.validate_run_request("c", mode)
            assert not any("mode" in e.lower() for e in errors), f"mode {mode!r} unexpectedly failed"


# ── State persistence ─────────────────────────────────────────────────────────

class TestStatePersistence:
    def test_save_and_load_roundtrip(self):
        state = _state()
        rm._save_state(state)
        loaded = rm.load_state("camp")
        assert loaded.run_id == state.run_id
        assert loaded.status == state.status

    def test_load_returns_none_for_missing(self):
        assert rm.load_state("no-such-campaign") is None

    def test_load_returns_none_for_corrupt_json(self, tmp_path):
        path = tmp_path / "corrupt" / "run_state.json"
        path.parent.mkdir()
        path.write_text("{ broken", encoding="utf-8")
        assert rm.load_state("corrupt") is None

    def test_load_handles_partial_fields(self, tmp_path):
        """Missing optional fields should not crash the loader."""
        path = tmp_path / "partial" / "run_state.json"
        path.parent.mkdir()
        path.write_text(json.dumps({
            "run_id": "x", "campaign_id": "partial",
            "mode": "search", "steps": ["search"], "status": "done",
        }), encoding="utf-8")
        state = rm.load_state("partial")
        assert state is not None
        assert state.run_id == "x"

    def test_no_tmp_file_left_after_save(self):
        rm._save_state(_state())
        assert not list((rm.DATA_DIR / "camp").glob("*.tmp"))


# ── is_running ────────────────────────────────────────────────────────────────

class TestIsRunning:
    def test_false_when_no_state(self):
        assert rm.is_running("camp") is False

    def test_true_when_status_running(self):
        rm._save_state(_state(status="running"))
        assert rm.is_running("camp") is True

    def test_false_when_status_done(self):
        rm._save_state(_state(status="done"))
        assert rm.is_running("camp") is False

    def test_false_when_status_error(self):
        rm._save_state(_state(status="error"))
        assert rm.is_running("camp") is False


# ── tail_log ──────────────────────────────────────────────────────────────────

class TestTailLog:
    def test_returns_empty_when_no_log(self):
        assert rm.tail_log("camp", "nonexistent") == []

    def test_returns_lines(self, tmp_path):
        log = tmp_path / "camp" / "logs" / "run.log"
        log.parent.mkdir(parents=True)
        log.write_text("line1\nline2\nline3\n", encoding="utf-8")
        result = rm.tail_log("camp", "run")
        assert result == ["line1", "line2", "line3"]

    def test_respects_line_limit(self, tmp_path):
        log = tmp_path / "camp" / "logs" / "run.log"
        log.parent.mkdir(parents=True)
        log.write_text("\n".join(f"line{i}" for i in range(300)) + "\n", encoding="utf-8")
        result = rm.tail_log("camp", "run", lines=10)
        assert len(result) == 10
        assert result[-1] == "line299"

    def test_handles_encoding_errors(self, tmp_path):
        log = tmp_path / "camp" / "logs" / "run.log"
        log.parent.mkdir(parents=True)
        log.write_bytes(b"valid\xff\xfeinvalid\n")
        result = rm.tail_log("camp", "run")
        assert len(result) >= 1  # doesn't crash


# ── MODES / STEPS sanity ──────────────────────────────────────────────────────

class TestModesConfig:
    def test_all_modes_have_required_keys(self):
        for name, cfg in rm.MODES.items():
            assert "label" in cfg,       f"{name} missing label"
            assert "description" in cfg, f"{name} missing description"
            assert "steps" in cfg,       f"{name} missing steps"
            assert "extra_args" in cfg,  f"{name} missing extra_args"

    def test_all_mode_steps_are_known(self):
        for name, cfg in rm.MODES.items():
            for step in cfg["steps"]:
                assert step in rm.STEPS, f"Mode {name!r} references unknown step {step!r}"

    def test_no_mode_has_empty_steps(self):
        for name, cfg in rm.MODES.items():
            assert cfg["steps"], f"Mode {name!r} has empty steps list"

    def test_full_mode_includes_all_steps(self):
        assert set(rm.MODES["full"]["steps"]) == set(rm.STEPS.keys())

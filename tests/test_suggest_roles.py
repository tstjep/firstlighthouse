"""
Tests for suggest_roles.py

Covers:
  - JSON extraction from LLM output (shared helper behaviour)
  - Role normalisation (type enforcement, placeholder guards, field truncation)
  - suggest() returns empty list when ICP is blank
  - suggest() returns empty list when provider is unavailable
  - suggest() returns buyers first, then users
  - suggest() filters placeholder / invalid roles
  - _signals_block() includes only positive signals
  - _call() returns '' on LLM exception
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import suggest_roles as sr


# ── Helpers ────────────────────────────────────────────────────────────────────

def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def make_provider(buyer_json: str, user_json: str):
    calls = [buyer_json, user_json]
    idx   = {"n": 0}

    async def _chat(**kwargs):
        resp = MagicMock()
        resp.content = calls[idx["n"] % len(calls)]
        idx["n"] += 1
        return resp

    provider = MagicMock()
    provider.chat = _chat
    return provider, "mock-model"


# ── _extract_json_array ────────────────────────────────────────────────────────

class TestExtractJsonArray:
    def test_plain_json(self):
        raw = '[{"role": "CEO", "role_type": "buyer", "rationale": "r"}]'
        assert sr._extract_json_array(raw) == [{"role": "CEO", "role_type": "buyer", "rationale": "r"}]

    def test_markdown_fenced(self):
        raw = '```json\n[{"role": "HR Manager"}]\n```'
        assert sr._extract_json_array(raw) == [{"role": "HR Manager"}]

    def test_empty_array(self):
        assert sr._extract_json_array("[]") == []

    def test_no_array_returns_empty(self):
        assert sr._extract_json_array("No suggestions.") == []

    def test_invalid_json_returns_empty(self):
        assert sr._extract_json_array("[{broken]") == []


# ── _normalise ─────────────────────────────────────────────────────────────────

class TestNormalise:
    def _buyer(self, raw):
        return sr._normalise(raw, expected_type="buyer")

    def _user(self, raw):
        return sr._normalise(raw, expected_type="user")

    def test_valid_buyer(self):
        raw = {"role": "Managing Partner", "role_type": "buyer", "rationale": "Signs contracts."}
        result = self._buyer(raw)
        assert result["role"] == "Managing Partner"
        assert result["role_type"] == "buyer"
        assert result["rationale"] == "Signs contracts."

    def test_valid_user(self):
        raw = {"role": "HR Manager", "role_type": "user", "rationale": "Uses it daily."}
        result = self._user(raw)
        assert result["role_type"] == "user"

    def test_missing_role_returns_none(self):
        assert self._buyer({"role_type": "buyer", "rationale": "r"}) is None

    def test_empty_role_returns_none(self):
        assert self._buyer({"role": "   ", "role_type": "buyer"}) is None

    def test_placeholder_role_discarded(self):
        assert self._buyer({"role": "role", "role_type": "buyer"}) is None
        assert self._buyer({"role": "example", "role_type": "buyer"}) is None
        assert self._buyer({"role": "placeholder", "role_type": "buyer"}) is None
        assert self._buyer({"role": "Job Title", "role_type": "buyer"}) is None

    def test_wrong_role_type_corrected(self):
        """LLM sometimes returns wrong role_type — we enforce expected_type."""
        raw = {"role": "CEO", "role_type": "user", "rationale": "r"}
        result = self._buyer(raw)
        assert result["role_type"] == "buyer"

    def test_missing_role_type_defaults_to_expected(self):
        raw = {"role": "CFO", "rationale": "r"}
        result = self._buyer(raw)
        assert result["role_type"] == "buyer"

    def test_role_truncated_at_80(self):
        raw = {"role": "A" * 200, "role_type": "buyer", "rationale": "r"}
        result = self._buyer(raw)
        assert len(result["role"]) == 80

    def test_rationale_truncated_at_200(self):
        raw = {"role": "CEO", "role_type": "buyer", "rationale": "R" * 300}
        result = self._buyer(raw)
        assert len(result["rationale"]) == 200

    def test_missing_rationale_defaults_to_empty(self):
        raw = {"role": "CEO", "role_type": "buyer"}
        result = self._buyer(raw)
        assert result["rationale"] == ""


# ── _signals_block ─────────────────────────────────────────────────────────────

class TestSignalsBlock:
    def test_empty_signals_returns_empty_string(self):
        assert sr._signals_block([]) == ""

    def test_includes_positive_signals(self):
        signals = [{"key": "hiring", "name": "Active Hiring", "description": "Hiring fast", "points": 2}]
        block = sr._signals_block(signals)
        assert "Active Hiring" in block

    def test_excludes_negative_signals(self):
        signals = [{"key": "too_large", "name": "Too Large", "description": "Skip", "points": -3}]
        block = sr._signals_block(signals)
        assert block == ""

    def test_mixed_only_shows_positive(self):
        signals = [
            {"key": "growth", "name": "Growing", "description": "Expanding", "points": 2},
            {"key": "too_large", "name": "Too Large", "description": "Skip", "points": -3},
        ]
        block = sr._signals_block(signals)
        assert "Growing" in block
        assert "Too Large" not in block

    def test_zero_points_excluded(self):
        signals = [{"key": "neutral", "name": "Neutral", "description": "Neither", "points": 0}]
        assert sr._signals_block(signals) == ""


# ── _call ──────────────────────────────────────────────────────────────────────

class TestCall:
    def test_returns_empty_string_on_exception(self):
        async def boom(**kwargs):
            raise RuntimeError("network error")
        provider = MagicMock()
        provider.chat = boom
        result = run(sr._call(provider, "model", "prompt"))
        assert result == ""


# ── suggest() ─────────────────────────────────────────────────────────────────

class TestSuggest:
    def test_empty_icp_returns_empty(self):
        assert run(sr.suggest("")) == []

    def test_blank_icp_returns_empty(self):
        assert run(sr.suggest("   ")) == []

    def test_provider_unavailable_returns_empty(self):
        with patch("suggest_roles._get_provider", side_effect=SystemExit):
            result = run(sr.suggest("We sell HR software to Swiss SMBs."))
        assert result == []

    def test_returns_buyers_then_users(self):
        buyer_data = [{"role": "CEO", "role_type": "buyer", "rationale": "Signs contracts."}]
        user_data  = [{"role": "HR Manager", "role_type": "user", "rationale": "Uses it daily."}]
        provider, model = make_provider(json.dumps(buyer_data), json.dumps(user_data))
        with patch("suggest_roles._get_provider", return_value=(provider, model)):
            result = run(sr.suggest("We sell HR software."))
        assert len(result) == 2
        assert result[0]["role_type"] == "buyer"
        assert result[1]["role_type"] == "user"

    def test_malformed_llm_output_skipped(self):
        provider, model = make_provider("not json at all", "also not json")
        with patch("suggest_roles._get_provider", return_value=(provider, model)):
            result = run(sr.suggest("We sell CRM software."))
        assert result == []

    def test_partial_malformed_skipped(self):
        good = [{"role": "CFO", "role_type": "buyer", "rationale": "Budget owner."}]
        provider, model = make_provider(json.dumps(good), "bad json")
        with patch("suggest_roles._get_provider", return_value=(provider, model)):
            result = run(sr.suggest("We sell to SMBs."))
        assert len(result) == 1
        assert result[0]["role"] == "CFO"

    def test_placeholder_roles_filtered(self):
        bad_data  = [{"role": "example", "role_type": "buyer", "rationale": "r"}]
        good_data = [{"role": "CEO", "role_type": "buyer", "rationale": "r"}]
        provider, model = make_provider(json.dumps(bad_data), json.dumps(good_data))
        with patch("suggest_roles._get_provider", return_value=(provider, model)):
            result = run(sr.suggest("We sell software."))
        assert all(r["role"] != "example" for r in result)

    def test_signals_passed_to_prompt(self):
        """Positive signals should appear in the buyer prompt."""
        captured = {}

        async def capture(**kwargs):
            messages = kwargs.get("messages", [])
            user_msg = next((m["content"] for m in messages if m.get("role") == "user"), "")
            captured.setdefault("messages", []).append(user_msg)
            resp = MagicMock()
            resp.content = "[]"
            return resp

        provider = MagicMock()
        provider.chat = capture

        signals = [{"key": "hiring", "name": "Active Hiring", "description": "Hiring fast", "points": 2}]
        with patch("suggest_roles._get_provider", return_value=(provider, "model")):
            run(sr.suggest("We sell HR software.", existing_signals=signals))

        all_prompts = " ".join(captured.get("messages", []))
        assert "Active Hiring" in all_prompts

    def test_negative_signals_not_in_prompt(self):
        captured = {}

        async def capture(**kwargs):
            messages = kwargs.get("messages", [])
            user_msg = next((m["content"] for m in messages if m.get("role") == "user"), "")
            captured.setdefault("messages", []).append(user_msg)
            resp = MagicMock()
            resp.content = "[]"
            return resp

        provider = MagicMock()
        provider.chat = capture

        signals = [{"key": "too_large", "name": "Too Large", "description": "Skip", "points": -3}]
        with patch("suggest_roles._get_provider", return_value=(provider, "model")):
            run(sr.suggest("We sell software.", existing_signals=signals))

        all_prompts = " ".join(captured.get("messages", []))
        assert "Too Large" not in all_prompts

    def test_wrong_role_type_corrected_in_output(self):
        """LLM returns buyer role as 'user' — should be corrected to 'buyer'."""
        buyer_data = [{"role": "CEO", "role_type": "user", "rationale": "r"}]  # wrong type
        provider, model = make_provider(json.dumps(buyer_data), "[]")
        with patch("suggest_roles._get_provider", return_value=(provider, model)):
            result = run(sr.suggest("We sell software."))
        assert len(result) == 1
        assert result[0]["role_type"] == "buyer"

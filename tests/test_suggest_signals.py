"""
Tests for suggest_signals.py

Covers:
  - JSON extraction from LLM output (with and without markdown fences)
  - Signal normalisation (sign enforcement, clamping, keyword coercion)
  - suggest() returns empty list when ICP is blank
  - suggest() returns empty list when provider is unavailable
  - Full pipeline with a mocked LLM provider
  - suggest_more() respects existing signal keys (no duplicates)
  - suggest_more() returns empty list when ICP is blank
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import suggest_signals as ss


# ── Helpers ────────────────────────────────────────────────────────────────────

def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def make_provider(pos_json: str, neg_json: str):
    """Return a mock (provider, model) pair whose chat() alternates responses."""
    calls = [pos_json, neg_json]
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
        raw = '[{"name": "A", "points": 2}]'
        assert ss._extract_json_array(raw) == [{"name": "A", "points": 2}]

    def test_markdown_fenced(self):
        raw = "```json\n[{\"name\": \"B\"}]\n```"
        result = ss._extract_json_array(raw)
        assert result == [{"name": "B"}]

    def test_prose_before_array(self):
        raw = "Here are my suggestions:\n[{\"name\": \"C\", \"points\": 1}]"
        assert ss._extract_json_array(raw) == [{"name": "C", "points": 1}]

    def test_empty_array(self):
        assert ss._extract_json_array("[]") == []

    def test_no_array_returns_empty(self):
        assert ss._extract_json_array("No JSON here.") == []

    def test_invalid_json_returns_empty(self):
        assert ss._extract_json_array("[{broken]") == []

    def test_non_array_json_returns_empty(self):
        assert ss._extract_json_array('{"key": "value"}') == []


# ── _normalise ─────────────────────────────────────────────────────────────────

class TestNormalise:
    def _pos(self, raw):
        return ss._normalise(raw, expected_positive=True)

    def _neg(self, raw):
        return ss._normalise(raw, expected_positive=False)

    def test_valid_positive(self):
        raw = {"name": "Growing", "key": "growth", "description": "desc",
               "llm_definition": "defn", "keywords": ["hiring"], "points": 2}
        result = self._pos(raw)
        assert result["points"] == 2
        assert result["name"] == "Growing"
        assert result["key"] == "growth"

    def test_valid_negative(self):
        raw = {"name": "Too Big", "key": "too_big", "description": "d",
               "llm_definition": "l", "keywords": [], "points": -2}
        result = self._neg(raw)
        assert result["points"] == -2

    def test_positive_enforces_positive_sign(self):
        raw = {"name": "X", "key": "x", "description": "", "llm_definition": "", "points": -1}
        result = self._pos(raw)
        assert result["points"] == 1

    def test_negative_enforces_negative_sign(self):
        raw = {"name": "X", "key": "x", "description": "", "llm_definition": "", "points": 3}
        result = self._neg(raw)
        assert result["points"] == -1

    def test_points_clamped_to_range(self):
        raw = {"name": "X", "key": "x", "description": "", "llm_definition": "", "points": 99}
        result = self._pos(raw)
        assert result["points"] == 3

    def test_points_clamped_negative(self):
        raw = {"name": "X", "key": "x", "description": "", "llm_definition": "", "points": -99}
        result = self._neg(raw)
        assert result["points"] == -3

    def test_keywords_as_string_split(self):
        raw = {"name": "X", "key": "x", "description": "", "llm_definition": "",
               "keywords": "hiring, growth, SaaS", "points": 1}
        result = self._pos(raw)
        assert result["keywords"] == ["hiring", "growth", "SaaS"]

    def test_missing_name_returns_none(self):
        assert self._pos({"key": "x", "points": 1}) is None

    def test_missing_key_returns_none(self):
        assert self._pos({"name": "X", "points": 1}) is None

    def test_empty_name_returns_none(self):
        assert self._pos({"name": "  ", "key": "x", "points": 1}) is None

    def test_invalid_points_type_uses_default(self):
        raw = {"name": "X", "key": "x", "description": "", "llm_definition": "", "points": "bad"}
        result = self._pos(raw)
        assert result["points"] == 1

    def test_key_lowercased(self):
        raw = {"name": "X", "key": "MySignal", "description": "", "llm_definition": "", "points": 1}
        result = self._pos(raw)
        assert result["key"] == "mysignal"

    # ── AI pitfall guards ──────────────────────────────────────────────────────

    def test_placeholder_name_discarded(self):
        assert self._pos({"name": "signal", "key": "s", "points": 1}) is None
        assert self._pos({"name": "example", "key": "e", "points": 1}) is None

    def test_placeholder_key_discarded(self):
        assert self._pos({"name": "My Signal", "key": "placeholder", "points": 1}) is None

    def test_key_special_chars_sanitised(self):
        raw = {"name": "X", "key": "my signal (new)!", "points": 1, "description": "", "llm_definition": ""}
        result = self._pos(raw)
        assert result["key"] == "my_signal_new"

    def test_key_all_special_chars_returns_none(self):
        raw = {"name": "X", "key": "!!!", "points": 1, "description": "", "llm_definition": ""}
        assert self._pos(raw) is None

    def test_keywords_capped_at_10(self):
        raw = {"name": "X", "key": "x", "points": 1, "description": "", "llm_definition": "",
               "keywords": [f"kw{i}" for i in range(20)]}
        result = self._pos(raw)
        assert len(result["keywords"]) == 10

    def test_name_truncated_at_80_chars(self):
        raw = {"name": "A" * 200, "key": "x", "points": 1, "description": "", "llm_definition": ""}
        result = self._pos(raw)
        assert len(result["name"]) == 80

    def test_llm_definition_truncated_at_800_chars(self):
        raw = {"name": "X", "key": "x", "points": 1, "description": "", "llm_definition": "D" * 1000}
        result = self._pos(raw)
        assert len(result["llm_definition"]) == 800

    def test_description_truncated_at_200_chars(self):
        raw = {"name": "X", "key": "x", "points": 1, "llm_definition": "",
               "description": "D" * 300}
        result = self._pos(raw)
        assert len(result["description"]) == 200


# ── suggest() ─────────────────────────────────────────────────────────────────

class TestSuggest:
    def test_empty_icp_returns_empty(self):
        result = run(ss.suggest(""))
        assert result == []

    def test_blank_icp_returns_empty(self):
        result = run(ss.suggest("   "))
        assert result == []

    def test_provider_unavailable_returns_empty(self):
        with patch("suggest_signals._get_provider", side_effect=SystemExit):
            result = run(ss.suggest("We sell software to law firms."))
        assert result == []

    def test_llm_exception_returns_empty_string(self):
        """_call returns '' on any LLM exception."""
        async def boom(**kwargs):
            raise RuntimeError("network error")
        provider = MagicMock()
        provider.chat = boom
        result = run(ss._call(provider, "model", "prompt"))
        assert result == ""

    def test_returns_positive_then_negative(self):
        pos_data = [{"name": "Growing", "key": "growth", "description": "d",
                     "llm_definition": "l", "keywords": ["hiring"], "points": 2}]
        neg_data = [{"name": "Too Big", "key": "too_big", "description": "d",
                     "llm_definition": "l", "keywords": ["enterprise"], "points": -2}]

        provider, model = make_provider(json.dumps(pos_data), json.dumps(neg_data))
        with patch("suggest_signals._get_provider", return_value=(provider, model)):
            result = run(ss.suggest("We sell SaaS to SMB law firms."))

        assert len(result) == 2
        assert result[0]["points"] > 0   # positive first
        assert result[1]["points"] < 0   # negative second

    def test_malformed_llm_output_skipped(self):
        provider, model = make_provider("not json at all", "also not json")
        with patch("suggest_signals._get_provider", return_value=(provider, model)):
            result = run(ss.suggest("We sell CRM software."))
        assert result == []

    def test_partial_malformed_skipped(self):
        good = [{"name": "A", "key": "a", "description": "d", "llm_definition": "l", "points": 1}]
        bad  = "not json"
        provider, model = make_provider(json.dumps(good), bad)
        with patch("suggest_signals._get_provider", return_value=(provider, model)):
            result = run(ss.suggest("We sell to recruiters."))
        assert len(result) == 1
        assert result[0]["name"] == "A"


# ── suggest_more() ────────────────────────────────────────────────────────────

class TestSuggestMore:
    """Tests for the 'suggest additional signals given existing ones' feature."""

    def test_empty_icp_returns_empty(self):
        result = run(ss.suggest_more("", existing_signals=[]))
        assert result == []

    def test_returns_signals_not_in_existing_keys(self):
        existing = [{"key": "growth"}, {"key": "tech_stack"}]
        new_pos  = [{"name": "Active Hiring", "key": "hiring", "description": "d",
                     "llm_definition": "l", "keywords": [], "points": 2}]
        new_neg  = [{"name": "Avoid Enterprise", "key": "avoid_enterprise", "description": "d",
                     "llm_definition": "l", "keywords": [], "points": -1}]

        provider, model = make_provider(json.dumps(new_pos), json.dumps(new_neg))
        with patch("suggest_signals._get_provider", return_value=(provider, model)):
            result = run(ss.suggest_more("We sell CRM to SMBs.", existing_signals=existing))

        keys = {s["key"] for s in result}
        assert "growth" not in keys
        assert "tech_stack" not in keys
        assert "hiring" in keys or "avoid_enterprise" in keys

    def test_deduplicates_returned_keys_matching_existing(self):
        """Even if the LLM returns a key that matches an existing signal, it is filtered out."""
        existing = [{"key": "growth"}]
        llm_returns = [{"name": "Growth", "key": "growth", "description": "d",
                        "llm_definition": "l", "keywords": [], "points": 2}]
        provider, model = make_provider(json.dumps(llm_returns), "[]")
        with patch("suggest_signals._get_provider", return_value=(provider, model)):
            result = run(ss.suggest_more("We sell to law firms.", existing_signals=existing))
        assert all(s["key"] != "growth" for s in result)

    def test_existing_signals_context_in_prompt(self):
        """The prompt sent to the LLM should mention existing signal names."""
        captured = {}

        async def capture_chat(**kwargs):
            captured["messages"] = kwargs.get("messages", [])
            resp = MagicMock()
            resp.content = "[]"
            return resp

        provider = MagicMock()
        provider.chat = capture_chat
        with patch("suggest_signals._get_provider", return_value=(provider, "model")):
            run(ss.suggest_more(
                "We sell to recruiters.",
                existing_signals=[{"key": "growth", "name": "Growing"}],
            ))

        # The existing signal name should appear somewhere in the prompt
        user_msg = next((m["content"] for m in captured.get("messages", [])
                         if m.get("role") == "user"), "")
        assert "Growing" in user_msg or "growth" in user_msg

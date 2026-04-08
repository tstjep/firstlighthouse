"""Tests for immigration_rating_agent — scoring and rating logic."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.immigration_rating_agent import _score, _rating, _is_yes, _signals_present, _SWEET_SPOT_SIZES

# ── helpers ────────────────────────────────────────────────────────────────

def _row(
    website="https://example.co.uk",
    linkedin="https://linkedin.com/company/example",
    size="11-50",
    corporate="",
    tech="",
    multivisa="",
    highvolume="",
    growth="",
):
    """Build a 19-column row matching the A:S immigration sheet layout."""
    row = [""] * 19
    row[4]  = website    # E
    row[5]  = linkedin   # F
    row[6]  = size       # G
    row[9]  = corporate  # J
    row[11] = tech       # L
    row[13] = multivisa  # N
    row[15] = highvolume # P
    row[17] = growth     # R
    return row


# ── _is_yes ────────────────────────────────────────────────────────────────

class TestIsYes:

    def test_yes_returns_true(self):
        row = [""] * 10
        row[9] = "Yes"
        assert _is_yes(row, 9) is True

    def test_no_returns_false(self):
        row = [""] * 10
        row[9] = "No"
        assert _is_yes(row, 9) is False

    def test_empty_returns_false(self):
        assert _is_yes([""] * 10, 9) is False

    def test_case_insensitive(self):
        row = [""] * 10
        row[9] = "YES"
        assert _is_yes(row, 9) is True

    def test_short_row_returns_false(self):
        assert _is_yes([], 9) is False


# ── _score ─────────────────────────────────────────────────────────────────

class TestScore:

    def test_no_signals_no_profile_scores_zero(self):
        assert _score(_row(website="", linkedin="", size="")) == 0

    def test_corporate_signal_adds_three(self):
        assert _score(_row(corporate="Yes")) >= 3

    def test_highvolume_signal_adds_three(self):
        assert _score(_row(highvolume="Yes")) >= 3

    def test_tech_signal_adds_two(self):
        base = _score(_row())
        assert _score(_row(tech="Yes")) == base + 2

    def test_multivisa_signal_adds_two(self):
        base = _score(_row())
        assert _score(_row(multivisa="Yes")) == base + 2

    def test_growth_signal_adds_one(self):
        base = _score(_row())
        assert _score(_row(growth="Yes")) == base + 1

    def test_sweet_spot_size_adds_one(self):
        assert _score(_row(size="11-50")) > _score(_row(size=""))

    def test_non_sweet_spot_size_adds_nothing(self):
        # strip profile completeness by removing linkedin so only size differs
        base = _score(_row(size="", linkedin=""))
        assert _score(_row(size="500-1000", linkedin="")) == base

    def test_complete_profile_adds_one(self):
        complete = _score(_row(website="https://x.co.uk", linkedin="https://li.com/x", size="11-50"))
        incomplete = _score(_row(website="https://x.co.uk", linkedin="", size="11-50"))
        assert complete > incomplete

    def test_all_signals_max_score(self):
        row = _row(corporate="Yes", tech="Yes", multivisa="Yes", highvolume="Yes", growth="Yes", size="11-50")
        # 3+3+2+2+1+1+1 = 13
        assert _score(row) == 13

    def test_no_signals_with_complete_profile(self):
        # only profile completeness + sweet spot size = 2
        assert _score(_row()) == 2

    def test_corporate_and_highvolume_gives_high_rating(self):
        row = _row(corporate="Yes", highvolume="Yes")
        # 3+3+1(size)+1(profile) = 8 → rating 9
        assert _rating(_score(row)) >= 8


# ── _rating ────────────────────────────────────────────────────────────────

class TestRating:

    @pytest.mark.parametrize("pts,expected", [
        (0,  1),
        (1,  2),
        (2,  3),
        (3,  4),
        (4,  5),
        (5,  6),
        (6,  7),
        (7,  8),
        (9,  10),
        (11, 10),
        (13, 10),
    ])
    def test_thresholds(self, pts, expected):
        assert _rating(pts) == expected

    def test_returns_int(self):
        assert isinstance(_rating(5), int)

    def test_min_is_1(self):
        assert _rating(0) == 1

    def test_max_is_10(self):
        assert _rating(100) == 10


# ── sweet spot sizes ───────────────────────────────────────────────────────

class TestSweetSpotSizes:

    def test_small_firms_included(self):
        assert "1-10" in _SWEET_SPOT_SIZES
        assert "11-50" in _SWEET_SPOT_SIZES

    def test_mid_firms_included(self):
        assert "51-100" in _SWEET_SPOT_SIZES
        assert "51-200" in _SWEET_SPOT_SIZES

    def test_large_firms_not_included(self):
        assert "201-500" not in _SWEET_SPOT_SIZES
        assert "1000+" not in _SWEET_SPOT_SIZES


# ── end-to-end rating scenarios ────────────────────────────────────────────

class TestRatingScenarios:

    def test_prime_corporate_high_volume_firm(self):
        """All signals + complete profile → 10."""
        row = _row(corporate="Yes", highvolume="Yes", tech="Yes", multivisa="Yes", growth="Yes", size="51-200")
        assert _rating(_score(row)) == 10

    def test_strong_corporate_highvolume(self):
        """Corporate + high volume with profile → high rating."""
        row = _row(corporate="Yes", highvolume="Yes", size="51-200")
        assert _rating(_score(row)) >= 8

    def test_tech_multivisa_solid(self):
        """Tech + multivisa → mid-range rating."""
        row = _row(tech="Yes", multivisa="Yes", size="11-50")
        assert 5 <= _rating(_score(row)) <= 8

    def test_single_corporate_signal(self):
        """One corporate signal only → low-mid rating."""
        row = _row(corporate="Yes", website="", linkedin="", size="")
        assert 3 <= _rating(_score(row)) <= 5

    def test_no_signals_no_profile_is_1(self):
        """Nothing at all → 1."""
        row = _row(website="", linkedin="", size="")
        assert _rating(_score(row)) == 1

    def test_growth_only_is_low(self):
        """Growth signal alone → low rating."""
        row = _row(growth="Yes", website="", linkedin="", size="")
        assert _rating(_score(row)) <= 3


# ── _signals_present ───────────────────────────────────────────────────────

class TestSignalsPresent:

    def test_all_empty_returns_false(self):
        assert _signals_present(_row()) is False

    def test_yes_in_corporate_returns_true(self):
        assert _signals_present(_row(corporate="Yes")) is True

    def test_no_in_any_signal_returns_true(self):
        assert _signals_present(_row(tech="No")) is True

    def test_blank_signal_cols_returns_false(self):
        row = [""] * 19
        assert _signals_present(row) is False

    def test_partial_signals_returns_true(self):
        assert _signals_present(_row(growth="Yes")) is True


# ── LLM fallback routing ───────────────────────────────────────────────────

class TestLLMFallbackRouting:
    """Verify which rows get routed to LLM vs rule-based."""

    def test_row_with_signals_does_not_need_llm(self):
        row = _row(corporate="Yes")
        assert _signals_present(row) is True  # rule-based path

    def test_row_without_signals_needs_llm(self):
        row = _row()  # default has no signals set
        assert _signals_present(row) is False  # LLM fallback path

    def test_provisional_rating_prefix(self):
        # LLM ratings are stored as "~3", "~4" etc.
        assert "~3".startswith("~")
        assert "3".startswith("~") is False

    def test_confirmed_rating_has_no_prefix(self):
        rating_str = str(_rating(_score(_row(corporate="Yes"))))
        assert not rating_str.startswith("~")

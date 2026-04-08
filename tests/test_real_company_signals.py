"""Integration-style tests based on known real UK immigration companies.

These tests verify that the signal detection logic produces sensible results
for companies with well-known public profiles. They do NOT call SerpAPI or
the LLM — they test the scoring and signal consistency logic against
hand-coded facts about real companies.

Companies used (all real, UK-based, publicly verifiable):
  - Fragomen          — global corporate immigration, 5000+ staff
  - Latitude Law      — tech-forward immigration firm, client portal
  - Smith Stone Walters — corporate immigration, client portal
  - Refugee Council   — UK charity, immigration advice, not commercial
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.immigration_rating_agent import _score, _rating, _signals_present


def _row_from_signals(
    corporate="", tech="", multivisa="", highvolume="", growth="",
    size="11-50", website="https://example.co.uk", linkedin="https://li.com/x",
):
    """Build a 19-column row (A:S layout) from named signal values."""
    row = [""] * 19
    row[4]  = website
    row[5]  = linkedin
    row[6]  = size
    row[9]  = corporate   # J
    row[11] = tech        # L
    row[13] = multivisa   # N
    row[15] = highvolume  # P
    row[17] = growth      # R
    return row


# ── Fragomen ──────────────────────────────────────────────────────────────────
# World's largest corporate immigration firm. Signal agent detects:
#   corporate=Yes, highvolume=Yes, multivisa=Yes, growth=Yes, tech=No

class TestFragomen:

    def test_fragomen_corporate_signal_gives_high_score(self):
        """Corporate + highvolume + multivisa + growth → score ≥ 9."""
        row = _row_from_signals(
            corporate="Yes", highvolume="Yes", multivisa="Yes", growth="Yes",
            size="1001-5000", linkedin="https://linkedin.com/company/fragomen",
        )
        assert _score(row) >= 9

    def test_fragomen_rates_highly(self):
        row = _row_from_signals(
            corporate="Yes", highvolume="Yes", multivisa="Yes", growth="Yes",
            size="1001-5000", linkedin="https://linkedin.com/company/fragomen",
        )
        assert _rating(_score(row)) >= 9

    def test_fragomen_signals_present(self):
        row = _row_from_signals(corporate="Yes", highvolume="Yes")
        assert _signals_present(row) is True


# ── Latitude Law ──────────────────────────────────────────────────────────────
# Mid-size firm with bespoke client portal. Signal agent detects:
#   corporate=Yes, tech=Yes, multivisa=Yes, highvolume=Yes

class TestLatitudeLaw:

    def test_latitude_law_tech_signal_contributes(self):
        row = _row_from_signals(
            corporate="Yes", tech="Yes", multivisa="Yes", highvolume="Yes",
            size="11-50", linkedin="https://linkedin.com/company/latitude-law",
        )
        base = _score(_row_from_signals(
            corporate="Yes", multivisa="Yes", highvolume="Yes",
            size="11-50", linkedin="https://linkedin.com/company/latitude-law",
        ))
        assert _score(row) == base + 2  # tech adds 2 pts

    def test_latitude_law_rates_8_or_higher(self):
        row = _row_from_signals(
            corporate="Yes", tech="Yes", multivisa="Yes", highvolume="Yes",
            size="11-50", linkedin="https://linkedin.com/company/latitude-law",
        )
        assert _rating(_score(row)) >= 8


# ── Smith Stone Walters ───────────────────────────────────────────────────────
# Corporate immigration, client portal, growth (expanding globally). All 5 signals Yes.

class TestSmithStoneWalters:

    def test_all_signals_yes_max_score(self):
        row = _row_from_signals(
            corporate="Yes", tech="Yes", multivisa="Yes", highvolume="Yes", growth="Yes",
            size="51-200", linkedin="https://linkedin.com/company/smith-stone-walters",
        )
        # 3+2+2+3+1+1+1 = 13 → rating 10
        assert _score(row) == 13
        assert _rating(_score(row)) == 10

    def test_five_signals_all_yes_present(self):
        row = _row_from_signals(
            corporate="Yes", tech="Yes", multivisa="Yes", highvolume="Yes", growth="Yes",
        )
        assert _signals_present(row) is True


# ── Refugee Council ───────────────────────────────────────────────────────────
# UK charity — not a commercial prospect for LawFairy case management software.
# Charities score low: no corporate immigration, no high volume clients.

class TestRefugeeCouncil:

    def test_charity_no_corporate_signal_scores_low(self):
        """Charities typically lack corporate/highvolume signals → low score."""
        row = _row_from_signals(
            corporate="No", tech="No", multivisa="Yes", highvolume="No", growth="No",
            size="51-200", linkedin="https://linkedin.com/company/refugee-council",
        )
        assert _score(row) <= 5

    def test_charity_rates_below_5(self):
        row = _row_from_signals(
            corporate="No", tech="No", multivisa="Yes", highvolume="No", growth="No",
            size="51-200", linkedin="https://linkedin.com/company/refugee-council",
        )
        assert _rating(_score(row)) <= 5

    def test_no_signals_no_profile_scores_1(self):
        """Empty charity with no detectable signals → rating 1."""
        row = _row_from_signals(
            corporate="No", tech="No", multivisa="No", highvolume="No", growth="No",
            size="", website="", linkedin="",
        )
        assert _rating(_score(row)) == 1


# ── Score ordering sanity ─────────────────────────────────────────────────────

class TestScoringOrdering:

    def test_fragomen_scores_higher_than_small_firm(self):
        fragomen = _row_from_signals(
            corporate="Yes", highvolume="Yes", multivisa="Yes", growth="Yes",
            size="1001-5000", linkedin="https://li.com/x",
        )
        small = _row_from_signals(
            corporate="No", highvolume="No", multivisa="No", growth="No",
            size="1-10", website="", linkedin="",
        )
        assert _score(fragomen) > _score(small)

    def test_corporate_firms_outscore_charities(self):
        corporate_firm = _row_from_signals(
            corporate="Yes", highvolume="Yes", tech="Yes",
            size="51-200", linkedin="https://li.com/x",
        )
        charity = _row_from_signals(
            corporate="No", highvolume="No", multivisa="Yes",
            size="11-50", linkedin="https://li.com/x",
        )
        assert _score(corporate_firm) > _score(charity)

    def test_tech_forward_firm_scores_higher_than_non_tech(self):
        with_tech = _row_from_signals(corporate="Yes", tech="Yes", size="11-50", linkedin="https://li.com/x")
        without_tech = _row_from_signals(corporate="Yes", tech="No", size="11-50", linkedin="https://li.com/x")
        assert _score(with_tech) > _score(without_tech)

    def test_all_signals_no_gives_minimum_meaningful_score(self):
        """All signals explicitly No (ran but not detected) → only profile pts."""
        row = _row_from_signals(
            corporate="No", tech="No", multivisa="No", highvolume="No", growth="No",
            size="11-50", linkedin="https://li.com/x",
        )
        # profile complete (website+linkedin+size) + sweet spot = 2 pts → rating 3
        assert _score(row) == 2
        assert _rating(_score(row)) == 3

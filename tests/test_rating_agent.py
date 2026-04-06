"""Tests for rating_agent — score_company and rate_sheet."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.rating_agent import (
    _COL_AI,
    _COL_COST,
    _COL_EDGE,
    _COL_HQ,
    _COL_KUBERNETES,
    _COL_LINKEDIN,
    _COL_NAME,
    _COL_NOTES,
    _COL_RATING,
    _COL_SIZE,
    _COL_SOVEREIGNTY,
    _COL_WEBSITE,
    rate_sheet,
    score_company,
)


# ── Helpers ────────────────────────────────────────────────────────────────

def _row(
    name="Acme IT AG",
    notes="",
    hq="Zürich, Switzerland",
    website="https://acme.ch",
    linkedin="https://linkedin.com/company/acme",
    size="11-50",
    ai="",
    sovereignty="",
    edge="",
    cost="",
    kubernetes="",
) -> list[str]:
    """Build a 27-element row list matching the DACH A:AA column layout."""
    row = [""] * 27
    row[_COL_NAME]       = name
    row[_COL_RATING]     = ""
    row[_COL_NOTES]      = notes
    row[_COL_WEBSITE]    = website
    row[_COL_LINKEDIN]   = linkedin
    row[_COL_SIZE]       = size
    row[_COL_HQ]         = hq
    row[_COL_AI]         = ai
    row[_COL_SOVEREIGNTY] = sovereignty
    row[_COL_EDGE]       = edge
    row[_COL_COST]       = cost
    row[_COL_KUBERNETES] = kubernetes
    return row


# ── Score thresholds ───────────────────────────────────────────────────────

class TestScoreThresholds:

    def test_no_signals_scores_1(self):
        row = _row(notes="", website="", linkedin="", size="", ai="", sovereignty="")
        score, _ = score_company(row)
        assert score == 1

    def test_cloud_keyword_alone_scores_at_least_2(self):
        row = _row(notes="Managed IT services and cloud hosting")
        score, _ = score_company(row)
        assert score >= 2

    def test_cloud_keyword_plus_complete_profile_scores_3(self):
        row = _row(notes="Cloud infrastructure provider")
        score, _ = score_company(row)
        assert score >= 3

    def test_sovereignty_signal_alone_scores_4(self):
        row = _row(sovereignty="Yes")
        score, _ = score_company(row)
        assert score >= 4

    def test_sovereignty_signal_plus_keywords_scores_5(self):
        """safeswisscloud-like: sovereignty signal + swiss cloud notes + complete."""
        row = _row(
            notes="Swiss cloud provider, data sovereignty, GDPR compliant hosting",
            sovereignty="Yes",
        )
        score, _ = score_company(row)
        assert score == 5

    def test_sovereignty_plus_ai_plus_cloud_scores_5(self):
        row = _row(
            notes="Managed cloud infrastructure",
            sovereignty="Yes",
            ai="Yes",
        )
        score, _ = score_company(row)
        assert score == 5

    def test_ai_plus_kubernetes_scores_4(self):
        row = _row(ai="Yes", kubernetes="Yes")
        score, _ = score_company(row)
        assert score >= 4

    def test_all_signals_scores_5(self):
        row = _row(sovereignty="Yes", ai="Yes", kubernetes="Yes", edge="Yes", cost="Yes")
        score, _ = score_company(row)
        assert score == 5


# ── safeswisscloud / itpoint archetypes ───────────────────────────────────

class TestArchetypes:

    def test_safeswisscloud_archetype_scores_5(self):
        """Swiss cloud, sovereignty focus, managed hosting."""
        row = _row(
            name="SafeSwissCloud AG",
            notes="Swiss cloud infrastructure provider. Data sovereignty, GDPR compliant. "
                  "Private cloud and colocation. Managed hosting.",
            sovereignty="Yes",
            kubernetes="Yes",
        )
        score, reason = score_company(row)
        assert score == 5
        assert "sovereignty" in reason

    def test_itpoint_archetype_scores_high(self):
        """IT managed services, cloud, Swiss focus."""
        row = _row(
            name="ITpoint Systems AG",
            notes="Swiss IT managed service provider. Cloud infrastructure, "
                  "on-premise and hybrid deployments.",
            sovereignty="Yes",
        )
        score, _ = score_company(row)
        assert score >= 4

    def test_generic_it_reseller_scores_low(self):
        """Hardware reseller, no cloud/sovereignty focus."""
        row = _row(
            name="PC Shop AG",
            notes="Computer hardware sales and repair services.",
            website="https://pcshop.ch",
            linkedin="",
            size="1-10",
        )
        score, _ = score_company(row)
        assert score <= 2


# ── Signal weighting ───────────────────────────────────────────────────────

class TestSignalWeights:

    def test_sovereignty_outweighs_ai(self):
        """A sovereignty-only company scores higher than AI-only."""
        sovereignty_row = _row(sovereignty="Yes", website="", linkedin="", size="")
        ai_row = _row(ai="Yes", website="", linkedin="", size="")
        s_score, _ = score_company(sovereignty_row)
        a_score, _ = score_company(ai_row)
        assert s_score >= a_score

    def test_sovereignty_keyword_in_notes_boosts_score(self):
        base = _row(notes="cloud hosting")
        boosted = _row(notes="cloud hosting, DSGVO compliant, swiss cloud")
        base_score, _ = score_company(base)
        boosted_score, _ = score_company(boosted)
        assert boosted_score > base_score

    def test_complete_profile_adds_point(self):
        incomplete = _row(website="", linkedin="", size="", notes="cloud")
        complete = _row(website="https://x.ch", linkedin="https://li.com/x", size="11-50", notes="cloud")
        s_incomplete, _ = score_company(incomplete)
        s_complete, _ = score_company(complete)
        assert s_complete > s_incomplete

    def test_yes_case_insensitive(self):
        """Signal matching is case-insensitive."""
        row_lower = _row(sovereignty="yes")
        row_upper = _row(sovereignty="Yes")
        assert score_company(row_lower)[0] == score_company(row_upper)[0]


# ── Reason strings ─────────────────────────────────────────────────────────

class TestReasons:

    def test_reason_includes_sovereignty(self):
        _, reason = score_company(_row(sovereignty="Yes"))
        assert "sovereignty" in reason

    def test_reason_includes_ai(self):
        _, reason = score_company(_row(ai="Yes"))
        assert "AI" in reason

    def test_reason_includes_kubernetes(self):
        _, reason = score_company(_row(kubernetes="Yes"))
        assert "kubernetes" in reason

    def test_no_signals_reason_is_no_signals(self):
        row = _row(notes="", website="", linkedin="", size="")
        _, reason = score_company(row)
        assert reason == "no signals"

    def test_cloud_keyword_in_reason(self):
        _, reason = score_company(_row(notes="managed cloud infrastructure"))
        assert "cloud" in reason


# ── rate_sheet (mocked Sheets API) ────────────────────────────────────────

class TestRateSheet:

    def _make_service(self, rows: list[list[str]]) -> MagicMock:
        svc = MagicMock()
        header = ["Company Name", "Comment Melt", "Rating", "Notes",
                  "Website", "LinkedIn", "Size", "HQ"] + [""] * 19
        (
            svc.spreadsheets.return_value
            .values.return_value
            .get.return_value
            .execute.return_value
        ) = {"values": [header] + rows}
        return svc

    def _run(self, rows, force=False):
        svc = self._make_service(rows)
        with (
            patch("agents.rating_agent.Credentials.from_service_account_file"),
            patch("agents.rating_agent.build", return_value=svc),
        ):
            rate_sheet("SHEET_ID", "creds.json", "CH", force=force)
        return svc

    def test_batchupdate_called_for_unrated_rows(self):
        svc = self._run([_row(name="Acme AG"), _row(name="Beta AG")])
        svc.spreadsheets.return_value.values.return_value.batchUpdate.assert_called_once()
        data = svc.spreadsheets.return_value.values.return_value.batchUpdate.call_args.kwargs["body"]["data"]
        assert len(data) == 2

    def test_already_rated_rows_skipped_by_default(self):
        rated_row = _row(name="Acme AG")
        rated_row[_COL_RATING] = "4"
        svc = self._run([rated_row])
        svc.spreadsheets.return_value.values.return_value.batchUpdate.assert_not_called()

    def test_force_overwrites_existing_rating(self):
        rated_row = _row(name="Acme AG")
        rated_row[_COL_RATING] = "4"
        svc = self._run([rated_row], force=True)
        svc.spreadsheets.return_value.values.return_value.batchUpdate.assert_called_once()

    def test_empty_name_rows_skipped(self):
        svc = self._run([[""] * 27])
        svc.spreadsheets.return_value.values.return_value.batchUpdate.assert_not_called()

    def test_correct_range_written(self):
        svc = self._run([_row(name="Acme AG")])
        data = svc.spreadsheets.return_value.values.return_value.batchUpdate.call_args.kwargs["body"]["data"]
        assert data[0]["range"] == "CH!C2"

    def test_scores_written_as_integers(self):
        svc = self._run([_row(name="Acme AG", sovereignty="Yes")])
        data = svc.spreadsheets.return_value.values.return_value.batchUpdate.call_args.kwargs["body"]["data"]
        written_value = data[0]["values"][0][0]
        assert isinstance(written_value, int)
        assert 1 <= written_value <= 5

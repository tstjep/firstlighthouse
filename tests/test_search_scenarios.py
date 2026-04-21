"""
Unit tests for the two reference ICP scenarios.

Covers:
  - Campaign config loads and validates correctly
  - build_task() output contains the right structural elements
  - Search queries reference the correct TLD and region
  - ICP text flows through to the agent task prompt
  - Signal definitions are internally consistent (sign, keys, points range)

No SerpAPI calls — all assertions are against config and prompt structure.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from campaign import Campaign
from agents.search_agent import build_task

CAMPAIGNS_DIR = Path(__file__).resolve().parent.parent / "campaigns"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def hr_ch() -> Campaign:
    return Campaign.load("hr-saas-ch", campaigns_dir=CAMPAIGNS_DIR)


@pytest.fixture(scope="module")
def sales_uk() -> Campaign:
    return Campaign.load("sales-tools-uk", campaigns_dir=CAMPAIGNS_DIR)


# ── Campaign loads & basic shape ──────────────────────────────────────────────

class TestHRCampaignLoads:
    def test_id(self, hr_ch):
        assert hr_ch.id == "hr-saas-ch"

    def test_has_two_segments(self, hr_ch):
        assert len(hr_ch.segments) == 2

    def test_segment_names(self, hr_ch):
        assert hr_ch.segment_names() == ["ProfServices", "LightManufacturing"]

    def test_region_is_switzerland(self, hr_ch):
        assert hr_ch.region.country_code == "ch"
        assert hr_ch.region.tld == "ch"

    def test_product_context_not_empty(self, hr_ch):
        assert len(hr_ch.product_context.strip()) > 50

    def test_has_three_signals(self, hr_ch):
        assert len(hr_ch.signals) == 3

    def test_signal_keys_unique(self, hr_ch):
        keys = [s.key for s in hr_ch.signals]
        assert len(keys) == len(set(keys))

    def test_signals_have_positive_and_negative(self, hr_ch):
        positives = [s for s in hr_ch.signals if s.points > 0]
        negatives = [s for s in hr_ch.signals if s.points < 0]
        assert len(positives) >= 1
        assert len(negatives) >= 1

    def test_negative_signal_points_negative(self, hr_ch):
        neg = next(s for s in hr_ch.signals if s.key == "too_large")
        assert neg.points < 0


class TestSalesUKCampaignLoads:
    def test_id(self, sales_uk):
        assert sales_uk.id == "sales-tools-uk"

    def test_has_two_segments(self, sales_uk):
        assert len(sales_uk.segments) == 2

    def test_segment_names(self, sales_uk):
        assert sales_uk.segment_names() == ["UKSaaS", "UKFintech"]

    def test_region_is_uk(self, sales_uk):
        assert sales_uk.region.country_code == "gb"
        assert sales_uk.region.tld == "co.uk"

    def test_product_context_mentions_buyer(self, sales_uk):
        ctx = sales_uk.product_context.lower()
        assert "saas" in ctx or "sales" in ctx

    def test_has_four_signals(self, sales_uk):
        assert len(sales_uk.signals) == 4

    def test_signal_keys_unique(self, sales_uk):
        keys = [s.key for s in sales_uk.signals]
        assert len(keys) == len(set(keys))

    def test_signals_have_positive_and_negative(self, sales_uk):
        assert any(s.points > 0 for s in sales_uk.signals)
        assert any(s.points < 0 for s in sales_uk.signals)

    def test_negative_signal_is_enterprise(self, sales_uk):
        neg = next(s for s in sales_uk.signals if s.key == "enterprise_revops")
        assert neg.points < 0


# ── Search query structure ────────────────────────────────────────────────────

class TestHRSearchQueries:
    def test_each_segment_has_tld_queries(self, hr_ch):
        for seg in hr_ch.segments:
            assert len(seg.search.tld_queries) >= 1, f"{seg.name} has no tld_queries"

    def test_each_segment_has_extra_queries(self, hr_ch):
        for seg in hr_ch.segments:
            assert len(seg.search.extra_queries) >= 1, f"{seg.name} has no extra_queries"

    def test_tld_queries_use_ch_tld(self, hr_ch):
        for seg in hr_ch.segments:
            for q in seg.search.tld_queries:
                assert "site:.ch" in q, f"TLD query missing site:.ch: {q!r}"

    def test_extra_queries_mention_schweiz_or_swiss(self, hr_ch):
        for seg in hr_ch.segments:
            combined = " ".join(seg.search.extra_queries).lower()
            assert "schweiz" in combined or "swiss" in combined, (
                f"{seg.name} extra_queries don't mention Schweiz/Swiss"
            )

    def test_contact_roles_not_empty(self, hr_ch):
        for seg in hr_ch.segments:
            assert seg.contact.roles, f"{seg.name} has no contact roles"


class TestSalesUKSearchQueries:
    def test_each_segment_has_tld_queries(self, sales_uk):
        for seg in sales_uk.segments:
            assert len(seg.search.tld_queries) >= 1

    def test_tld_queries_use_co_uk(self, sales_uk):
        for seg in sales_uk.segments:
            for q in seg.search.tld_queries:
                assert "site:.co.uk" in q, f"TLD query missing site:.co.uk: {q!r}"

    def test_extra_queries_mention_uk_or_london(self, sales_uk):
        for seg in sales_uk.segments:
            combined = " ".join(seg.search.extra_queries).lower()
            assert "uk" in combined or "london" in combined, (
                f"{seg.name} extra_queries don't mention UK/London"
            )

    def test_contact_roles_include_sales_leadership(self, sales_uk):
        for seg in sales_uk.segments:
            roles_lower = [r.lower() for r in seg.contact.roles]
            has_sales_role = any(
                "sales" in r or "revenue" in r or "growth" in r or "founder" in r
                for r in roles_lower
            )
            assert has_sales_role, f"{seg.name} contact roles don't include any sales/revenue role"


# ── build_task() prompt quality ───────────────────────────────────────────────

class TestBuildTaskHR:
    def test_task_contains_segment_description(self, hr_ch):
        task = build_task(hr_ch, "ProfServices")
        assert "professional" in task.lower() or "profservices" in task.lower()

    def test_task_contains_tld(self, hr_ch):
        task = build_task(hr_ch, "ProfServices")
        assert ".ch" in task

    def test_task_contains_tld_queries(self, hr_ch):
        task = build_task(hr_ch, "LightManufacturing")
        seg = hr_ch.segment("LightManufacturing")
        for q in seg.search.tld_queries:
            assert q in task, f"TLD query missing from task: {q!r}"

    def test_task_contains_extra_queries(self, hr_ch):
        task = build_task(hr_ch, "LightManufacturing")
        seg = hr_ch.segment("LightManufacturing")
        for q in seg.search.extra_queries:
            assert q in task, f"Extra query missing from task: {q!r}"

    def test_task_instructs_to_record_companies(self, hr_ch):
        task = build_task(hr_ch, "ProfServices")
        # Assert on intent (tool call to record companies) not a specific tool name
        assert "record_company" in task

    def test_task_mentions_region(self, hr_ch):
        task = build_task(hr_ch, "ProfServices")
        assert "Switzerland" in task or "switzerland" in task.lower() or "swiss" in task.lower()


class TestBuildTaskSalesUK:
    def test_task_contains_co_uk(self, sales_uk):
        task = build_task(sales_uk, "UKSaaS")
        assert ".co.uk" in task

    def test_task_contains_segment_description(self, sales_uk):
        task = build_task(sales_uk, "UKSaaS")
        assert "SaaS" in task or "saas" in task.lower()

    def test_task_contains_fintech_queries(self, sales_uk):
        task = build_task(sales_uk, "UKFintech")
        seg = sales_uk.segment("UKFintech")
        for q in seg.search.tld_queries:
            assert q in task

    def test_task_mentions_uk_region(self, sales_uk):
        task = build_task(sales_uk, "UKSaaS")
        assert "United Kingdom" in task or "UK" in task


# ── Signal LLM definitions quality ───────────────────────────────────────────

class TestSignalDefinitions:
    """LLM definitions must be specific enough to be actionable."""

    @pytest.mark.parametrize("campaign_fixture,signal_key", [
        ("hr_ch",     "active_hiring"),
        ("hr_ch",     "no_hr_system"),
        ("hr_ch",     "too_large"),
        ("sales_uk",  "recent_funding"),
        ("sales_uk",  "sales_hiring"),
        ("sales_uk",  "no_crm_outbound"),
        ("sales_uk",  "enterprise_revops"),
    ])
    def test_llm_definition_mentions_yes_and_no(self, request, campaign_fixture, signal_key):
        campaign = request.getfixturevalue(campaign_fixture)
        sig = next((s for s in campaign.signals if s.key == signal_key), None)
        assert sig is not None, f"Signal {signal_key!r} not found"
        defn = sig.llm_definition.lower()
        assert "yes" in defn, f"{signal_key}: llm_definition should mention 'Yes'"
        assert "no" in defn,  f"{signal_key}: llm_definition should mention 'No'"

    @pytest.mark.parametrize("campaign_fixture,signal_key", [
        ("hr_ch",     "active_hiring"),
        ("hr_ch",     "no_hr_system"),
        ("sales_uk",  "recent_funding"),
        ("sales_uk",  "sales_hiring"),
    ])
    def test_positive_signals_have_positive_points(self, request, campaign_fixture, signal_key):
        campaign = request.getfixturevalue(campaign_fixture)
        sig = next(s for s in campaign.signals if s.key == signal_key)
        assert sig.points > 0, f"{signal_key} should have positive points"

    @pytest.mark.parametrize("campaign_fixture,signal_key", [
        ("hr_ch",    "too_large"),
        ("sales_uk", "enterprise_revops"),
    ])
    def test_exclusion_signals_have_negative_points(self, request, campaign_fixture, signal_key):
        campaign = request.getfixturevalue(campaign_fixture)
        sig = next(s for s in campaign.signals if s.key == signal_key)
        assert sig.points < 0, f"{signal_key} should have negative points"

    @pytest.mark.parametrize("campaign_fixture", ["hr_ch", "sales_uk"])
    def test_all_signals_have_keywords(self, request, campaign_fixture):
        campaign = request.getfixturevalue(campaign_fixture)
        for sig in campaign.signals:
            assert sig.keywords, f"Signal {sig.key!r} has no keywords"

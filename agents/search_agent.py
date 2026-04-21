#!/usr/bin/env python3
"""
Company Search Agent
====================
Discovers companies in the target market and records them in the local JSON store.

Usage:
    python agents/search_agent.py --campaign hr-saas-ch --tab ProfServices
    python agents/search_agent.py --campaign sales-tools-uk --tab UKSaaS
"""

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config as cfg
from campaign import Campaign
from agents.provider import build_provider
from store import ResultStore
from tools.serp_tool import SerpSearchTool
from tools.json_append_tool import JsonAppendTool

from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus


def build_task(campaign: Campaign, tab: str) -> str:
    seg = campaign.segment(tab)
    tld_block   = "\n".join(f"  - {q}" for q in seg.search.tld_queries)
    extra_block = "\n".join(f"  - {q}" for q in seg.search.extra_queries)
    tld = campaign.region.tld

    return f"""
You are a lead-generation researcher. Find companies that match this profile: {seg.description}.
Record every real company you find using the record_company tool.

Many companies in this category don't use .{tld} — they may use .com, .io, .org, or others.
Use all search strategies below to avoid missing them.

Instructions:

1. START with site:.{tld} searches — these surface companies with a local domain:
{tld_block}

2. Then run these supplementary queries (city-based, LinkedIn, directories):
{extra_block}

3. Beyond the queries above, generate your own additional searches to catch companies
   that might be missed. Useful angles:
   - Named city or region combinations not yet tried
   - Specific service types or niches not yet covered
   - Professional directories and review sites
   - LinkedIn company search for firms that don't rank well on Google

4. For EVERY real company found, immediately call record_company.
   The tool automatically skips duplicates.
   Only skip: companies clearly outside the target region, or not matching: {seg.description}.

5. After every 5 searches, keep going with new angles you haven't tried yet.

6. When done, print a summary: total searches run, total companies added.

Be exhaustive. Cover the whole {campaign.region.label} — not just major cities.
""".strip()


async def main(campaign: Campaign, tab: str) -> None:
    errors = []
    if not cfg.SERPAPI_KEY:
        errors.append("SERPAPI_KEY is not set in config.py")
    if tab not in campaign.all_tab_names():
        errors.append(f"Tab '{tab}' not found in campaign. Available: {campaign.all_tab_names()}")
    if errors:
        print("[startup] Configuration errors — cannot continue:")
        for msg in errors:
            print(f"  ✗ {msg}")
        sys.exit(1)

    seg   = campaign.segment(tab)
    store = ResultStore(campaign.id)

    provider, model = build_provider()

    print(f"\nStarting search agent")
    print(f"Campaign: {campaign.name}")
    print(f"Tab:      {tab} — {seg.description}")
    print(f"Model:    {model}")
    print(f"Store:    data/{campaign.id}/results.json\n")

    min_searches = len(seg.search.tld_queries) + len(seg.search.extra_queries)
    max_iter = min_searches * 8 + 50

    bus = MessageBus()
    agent = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=PROJECT_ROOT,
        model=model,
        max_iterations=max_iter,
    )

    agent.tools.register(SerpSearchTool(api_key=cfg.SERPAPI_KEY, **campaign.serp_params()))
    agent.tools.register(JsonAppendTool(store=store, segment=tab))

    print("Agent running — streaming progress below:\n" + "-" * 60)

    async def on_progress(text: str, **_) -> None:
        if text:
            print(f"[agent] {text}")

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    result = await agent.process_direct(
        content=build_task(campaign, tab),
        session_key=f"search:{campaign.id}:{tab}:{run_id}",
        channel="cli",
        chat_id=f"search_{campaign.id}_{tab}_{run_id}",
        on_progress=on_progress,
    )

    print("-" * 60)
    content = result.content if result else ""
    if content and content.strip() != "I've completed processing but have no response to give.":
        print("\nFinal response:")
        print(content)

    await agent.close_mcp()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Company search agent")
    parser.add_argument("--campaign", required=True, help="Campaign ID")
    parser.add_argument("--tab",      default=None,  help="Segment / tab name")
    args = parser.parse_args()

    campaign = Campaign.load(args.campaign)
    tab = args.tab or campaign.segments[0].name

    asyncio.run(main(campaign, tab))

#!/usr/bin/env python3
"""
Company Search Agent
====================
Discovers companies in the target market and records them in the Google Sheet.

Usage:
    python agents/search_agent.py --campaign immigration-uk --tab LawFirms
    python agents/search_agent.py --campaign immigration-uk --tab Advisors
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
from tools.serp_tool import SerpSearchTool
from tools.sheets_tool import SheetsAppendTool

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def fetch_existing_companies(
    spreadsheet_id: str, credentials_file: str, tab: str
) -> tuple[set[str], set[str]]:
    """Return (names, domains) for one tab."""
    try:
        creds = Credentials.from_service_account_file(credentials_file, scopes=_SCOPES)
        service = build("sheets", "v4", credentials=creds)
        result = (
            service.spreadsheets().values()
            .get(spreadsheetId=spreadsheet_id, range=f"{tab}!A:E")
            .execute()
        )
    except Exception as exc:
        print(f"[warning] Could not fetch existing companies from '{tab}': {exc}")
        return set(), set()

    rows = result.get("values", [])
    names: set[str] = set()
    domains: set[str] = set()
    for row in rows[1:]:
        name    = row[0].strip().lower() if len(row) > 0 else ""
        website = row[4].strip()         if len(row) > 4 else ""
        if name:
            names.add(name)
        if website:
            domain = website.lower()
            for prefix in ("https://", "http://", "www."):
                domain = domain.removeprefix(prefix)
            domain = domain.rstrip("/").split("/")[0]
            if domain:
                domains.add(domain)
    return names, domains


def fetch_all_existing_companies(
    campaign: Campaign, credentials_file: str
) -> tuple[set[str], set[str]]:
    """Return (names, domains) across ALL segments for cross-tab dedup."""
    all_names: set[str] = set()
    all_domains: set[str] = set()
    for seg in campaign.segments:
        names, domains = fetch_existing_companies(
            campaign.spreadsheet_id, credentials_file, seg.name
        )
        all_names |= names
        all_domains |= domains
    return all_names, all_domains


def build_task(campaign: Campaign, tab: str) -> str:
    seg = campaign.segment(tab)
    tld_block   = "\n".join(f"  - {q}" for q in seg.search.tld_queries)
    extra_block = "\n".join(f"  - {q}" for q in seg.search.extra_queries)
    tld = campaign.region.tld

    return f"""
You are a lead-generation researcher. Find companies that match this profile: {seg.description}.
Record every real company you find using the sheets_append_company tool.

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

4. For EVERY real company found, immediately call sheets_append_company.
   The tool automatically skips duplicates.
   Only skip: companies clearly outside the target region, or not matching: {seg.description}.

5. After every 5 searches, keep going with new angles you haven't tried yet.

6. When done, print a summary: total searches run, total companies added.

Be exhaustive. Cover the whole {campaign.region.label} — not just major cities.
""".strip()


async def main(campaign: Campaign, tab: str) -> None:
    credentials_file = str(PROJECT_ROOT / campaign.credentials_file)

    errors = []
    if not campaign.spreadsheet_id:
        errors.append("spreadsheet_id is not set in campaign config")
    if not cfg.SERPAPI_KEY:
        errors.append("SERPAPI_KEY is not set in config.py")
    if not Path(credentials_file).exists():
        errors.append(f"Credentials file not found: {credentials_file}")
    if tab not in campaign.all_tab_names():
        errors.append(f"Tab '{tab}' not found in campaign. Available: {campaign.all_tab_names()}")
    if errors:
        print("[startup] Configuration errors — cannot continue:")
        for msg in errors:
            print(f"  ✗ {msg}")
        sys.exit(1)

    seg = campaign.segment(tab)
    existing_names, existing_domains = fetch_all_existing_companies(campaign, credentials_file)
    print(f"Existing companies across all tabs: {len(existing_names)} (cross-tab dedup active)")

    provider, model = build_provider()

    print(f"\nStarting search agent")
    print(f"Campaign: {campaign.name}")
    print(f"Tab:      {tab} — {seg.description}")
    print(f"Model:    {model}")
    print(f"Sheet:    {campaign.spreadsheet_id}\n")

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
    agent.tools.register(SheetsAppendTool(
        spreadsheet_id=campaign.spreadsheet_id,
        credentials_file=credentials_file,
        sheet_name=tab,
        existing_names=existing_names,
        existing_domains=existing_domains,
    ))

    print("Agent running — streaming progress below:\n" + "-" * 60)

    async def on_progress(text: str, **kwargs) -> None:
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
    parser.add_argument("--campaign", default="immigration-uk", help="Campaign ID")
    parser.add_argument("--tab",      default=None,             help="Segment / sheet tab name")
    args = parser.parse_args()

    campaign = Campaign.load(args.campaign)
    tab = args.tab or campaign.segments[0].name

    asyncio.run(main(campaign, tab))

#!/usr/bin/env python3
"""
UK Immigration Company Search Agent
=====================================
Discovers UK immigration law firms, advisors, consultants, and legaltech brokers
and records them in the corresponding Google Sheet tab.

Usage:
    python agents/immigration_search_agent.py                      # uses DEFAULT_TAB
    python agents/immigration_search_agent.py --tab LawFirms
    python agents/immigration_search_agent.py --tab Advisors
    python agents/immigration_search_agent.py --tab Consultants
    python agents/immigration_search_agent.py --tab LegaltechBrokers
"""

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config as cfg
from agents.provider import build_provider
from tools.serp_tool import SerpSearchTool
from tools.sheets_tool import SheetsAppendTool

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

SERP_PARAMS = {"gl": "gb", "cr": "countryGB"}

# Search hints per tab (company type)
TAB_SEARCH_HINTS = {
    "LawFirms": {
        "description": "UK immigration law firms and solicitors (SRA regulated)",
        "tld_queries": [
            'site:.co.uk "immigration solicitor"',
            'site:.co.uk "immigration law firm"',
            'site:.co.uk "immigration barrister"',
            'site:.co.uk "immigration lawyer"',
            'site:.co.uk "skilled worker visa" solicitor',
            'site:.co.uk "family visa" solicitor',
            'site:.co.uk "spouse visa" solicitor',
            'site:.co.uk "immigration law" "SRA regulated"',
            'site:.co.uk "points-based system" solicitor',
            'site:.co.uk "visa appeals" solicitor',
        ],
        "extra_queries": [
            '"immigration solicitor" London',
            '"immigration solicitor" Manchester OR Birmingham',
            '"immigration solicitor" Leeds OR Sheffield',
            '"immigration solicitor" Bristol OR Cardiff',
            '"immigration solicitor" Glasgow OR Edinburgh',
            '"immigration law firm" UK',
            '"SRA regulated" "immigration" solicitor UK',
            '"immigration barrister" chambers UK',
            'clutch.co "immigration law" UK',
            'site:solicitors.lawsociety.org.uk immigration',
            'site:linkedin.com/company "immigration solicitors" UK',
            '"skilled worker visa" "law firm" UK',
            '"family visa" solicitor UK',
            '"spouse visa" solicitor UK',
            '"immigration appeals" solicitor UK',
            '"indefinite leave to remain" solicitor UK',
            '"British citizenship" solicitor UK',
            '"sponsor licence" solicitor UK',
        ],
    },
    "Advisors": {
        "description": "OISC-regulated UK immigration advisers",
        "tld_queries": [
            'site:.co.uk "OISC regulated" adviser',
            'site:.co.uk "immigration adviser" OISC',
            'site:.co.uk "OISC" "immigration advice"',
            'site:.co.uk "regulated immigration adviser"',
            'site:.co.uk "immigration advice" "Level 3"',
            'site:.co.uk "immigration advice" "Level 2"',
            'site:.co.uk "immigration advice" "Level 1"',
            'site:.co.uk "OISC accredited"',
        ],
        "extra_queries": [
            '"OISC regulated" adviser London',
            '"OISC regulated" adviser Manchester OR Birmingham',
            '"OISC regulated" adviser Leeds OR Sheffield',
            '"OISC regulated" adviser Bristol OR Cardiff',
            '"immigration adviser" OISC UK',
            '"regulated immigration adviser" UK',
            'site:oisc.gov.uk adviser register',
            'site:linkedin.com/company "immigration adviser" UK',
            '"OISC" "Level 3" adviser UK',
            '"immigration advice" charity UK OISC',
            '"asylum" adviser OISC UK',
            '"refugee" immigration adviser UK',
        ],
    },
    "Consultants": {
        "description": "UK immigration consultants — corporate, HR, and global mobility",
        "tld_queries": [
            'site:.co.uk "immigration consultant"',
            'site:.co.uk "global mobility" immigration',
            'site:.co.uk "corporate immigration" consultant',
            'site:.co.uk "HR immigration" services',
            'site:.co.uk "sponsor licence" consultant',
            'site:.co.uk "right to work" consultant',
            'site:.co.uk "visa sponsorship" consultant',
            'site:.co.uk "skilled worker" immigration consultant',
        ],
        "extra_queries": [
            '"immigration consultant" London',
            '"immigration consultant" Manchester OR Birmingham',
            '"immigration consultant" Leeds OR Sheffield',
            '"global mobility" immigration consultant UK',
            '"corporate immigration" consultant UK',
            '"HR immigration" services UK',
            '"sponsor licence" consultant UK',
            '"right to work" compliance consultant UK',
            '"skilled worker visa" consultant UK',
            '"visa sponsorship" management UK',
            'site:linkedin.com/company "immigration consultant" "United Kingdom"',
            '"Tier 2" OR "skilled worker" immigration consultant UK',
            '"global mobility" services provider UK',
            '"expatriate" immigration services UK',
            '"relocation" immigration consultant UK',
        ],
    },
    "LegaltechBrokers": {
        "description": "UK immigration legaltech vendors, software resellers, and process outsourcers",
        "tld_queries": [
            'site:.co.uk "immigration case management" software',
            'site:.co.uk "immigration software"',
            'site:.co.uk "immigration technology"',
            'site:.co.uk "visa management system"',
            'site:.co.uk "immigration management" platform',
            'site:.co.uk "legal technology" immigration',
            'site:.co.uk "immigration outsourcing"',
        ],
        "extra_queries": [
            '"immigration case management software" UK',
            '"immigration software" vendor UK',
            '"visa management system" UK',
            '"immigration technology" provider UK',
            '"legal technology" immigration UK',
            '"immigration management" platform UK',
            '"immigration outsourcing" services UK',
            '"immigration process" automation UK',
            'site:linkedin.com/company "immigration software" UK',
            'site:linkedin.com/company "immigration technology" UK',
            '"legaltech" immigration UK',
            '"RegTech" immigration compliance UK',
            '"immigration workflow" software UK',
            '"matter management" immigration UK',
            '"client portal" immigration software UK',
        ],
    },
}


def _validate_startup(credentials_file: str) -> None:
    errors: list[str] = []
    if not cfg.SPREADSHEET_ID:
        errors.append("SPREADSHEET_ID is not set in config.py")
    if not cfg.SERPAPI_KEY:
        errors.append("SERPAPI_KEY is not set in config.py")
    if not Path(credentials_file).exists():
        errors.append(f"Service account credentials not found: {credentials_file}")
    if errors:
        print("[startup] Configuration errors — cannot continue:")
        for msg in errors:
            print(f"  ✗ {msg}")
        sys.exit(1)


def fetch_existing_companies(spreadsheet_id: str, credentials_file: str, tab: str) -> tuple[set[str], set[str]]:
    """Return (existing_names, existing_domains) for deduplication."""
    try:
        creds = Credentials.from_service_account_file(credentials_file, scopes=_SCOPES)
        service = build("sheets", "v4", credentials=creds)
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=f"{tab}!A:E")
            .execute()
        )
    except Exception as exc:
        print(f"[warning] Could not fetch existing companies from sheet: {exc}")
        return set(), set()

    rows = result.get("values", [])
    existing_names: set[str] = set()
    existing_domains: set[str] = set()

    for row in rows[1:]:  # skip header
        name    = row[0].strip().lower() if len(row) > 0 else ""
        website = row[4].strip()         if len(row) > 4 else ""
        if name:
            existing_names.add(name)
        if website:
            domain = website.lower()
            for prefix in ("https://", "http://", "www."):
                domain = domain.removeprefix(prefix)
            domain = domain.rstrip("/").split("/")[0]
            if domain:
                existing_domains.add(domain)

    return existing_names, existing_domains


def build_task(tab: str) -> str:
    hints = TAB_SEARCH_HINTS[tab]
    description = hints["description"]
    tld_block   = "\n".join(f"  - {q}" for q in hints["tld_queries"])
    extra_block = "\n".join(f"  - {q}" for q in hints["extra_queries"])

    return f"""
You are a lead-generation researcher. Find UK companies that are: {description}.
Record every real company you find using the sheets_append_company tool.

Instructions:

1. START with TLD searches (site:.co.uk) — these surface UK-based companies directly:
{tld_block}

2. Then fan out with supplementary queries:
{extra_block}

3. For EVERY real company found, immediately call sheets_append_company.
   The tool automatically skips duplicates already in the sheet.
   Only skip: companies clearly not based in the UK, or companies that do not
   provide the target service type ({description}).

4. After every 5 searches, continue with new query angles you haven't tried yet.
   Vary by city (London, Manchester, Birmingham, Leeds, Bristol, Glasgow, Edinburgh,
   Cardiff, Sheffield, Liverpool, Newcastle, Nottingham, Leicester, Reading, Brighton),
   by service specialism, and by size.

5. When done, print a summary: total searches run, total companies added.

Be exhaustive — cover the whole of the UK across different cities and service niches.
""".strip()


async def main(tab: str) -> None:
    credentials_file = str(PROJECT_ROOT / cfg.CREDENTIALS_FILE)
    _validate_startup(credentials_file)

    existing_names, existing_domains = fetch_existing_companies(
        cfg.SPREADSHEET_ID, credentials_file, tab
    )
    print(f"Existing companies in '{tab}' tab: {len(existing_names)} (will skip duplicates)")

    provider, model = build_provider()

    hints = TAB_SEARCH_HINTS[tab]
    print(f"\nStarting immigration search agent")
    print(f"Tab:   {tab} — {hints['description']}")
    print(f"Model: {model}")
    print(f"Sheet: {cfg.SPREADSHEET_ID}\n")

    min_searches = len(hints["tld_queries"]) + len(hints["extra_queries"])
    max_iter = min_searches * 8 + 50

    bus = MessageBus()
    agent = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=PROJECT_ROOT,
        model=model,
        temperature=0.2,
        max_tokens=32768,
        max_iterations=max_iter,
        memory_window=80,
    )

    agent.tools.register(SerpSearchTool(api_key=cfg.SERPAPI_KEY, **SERP_PARAMS))
    agent.tools.register(SheetsAppendTool(
        spreadsheet_id=cfg.SPREADSHEET_ID,
        credentials_file=credentials_file,
        sheet_name=tab,
        existing_names=existing_names,
        existing_domains=existing_domains,
    ))

    print("Agent running — streaming progress below:\n" + "-" * 60)

    async def on_progress(text: str) -> None:
        if text:
            print(f"[agent] {text}")

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    result = await agent.process_direct(
        content=build_task(tab),
        session_key=f"immigration_search:{tab}:{run_id}",
        channel="cli",
        chat_id=f"immigration_search_{tab}_{run_id}",
        on_progress=on_progress,
    )

    print("-" * 60)
    if result and result.strip() != "I've completed processing but have no response to give.":
        print("\nFinal response:")
        print(result)
    else:
        print("\n[warning] Agent returned no content. Check logs above.")

    await agent.close_mcp()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Discover UK immigration companies by type.")
    parser.add_argument(
        "--tab",
        choices=cfg.IMMIGRATION_TABS,
        default=cfg.DEFAULT_TAB,
        help=f"Sheet tab / company type to search (default: {cfg.DEFAULT_TAB})",
    )
    args = parser.parse_args()
    asyncio.run(main(args.tab))

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
        # site:.co.uk — catches firms with a UK domain (most established firms)
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
            'site:.co.uk "sponsor licence" solicitor',
            'site:.co.uk "indefinite leave to remain" solicitor',
        ],
        "extra_queries": [
            # City-based — catches firms on .com/.io/.law that don't appear in TLD searches
            '"immigration solicitor" London',
            '"immigration solicitor" Manchester OR Birmingham',
            '"immigration solicitor" Leeds OR Sheffield',
            '"immigration solicitor" Bristol OR Cardiff',
            '"immigration solicitor" Glasgow OR Edinburgh',
            '"immigration solicitor" Liverpool OR Newcastle',
            '"immigration solicitor" Nottingham OR Leicester',
            '"immigration solicitor" Reading OR Brighton OR Oxford',
            # Service-type queries — firms that describe themselves differently
            '"immigration law firm" UK',
            '"immigration barrister" chambers UK',
            '"SRA regulated" "immigration" solicitor UK',
            '"skilled worker visa" "law firm" UK',
            '"family visa" solicitor UK',
            '"sponsor licence" solicitor UK',
            '"visa appeals" solicitor UK',
            '"British citizenship" solicitor UK',
            '"indefinite leave to remain" solicitor UK',
            # Directory and professional body searches — authoritative source lists
            'site:solicitors.lawsociety.org.uk immigration',
            'site:chambersandpartners.com "immigration" UK law firm',
            'site:legal500.com "immigration" UK solicitors',
            'site:trustpilot.com "immigration solicitor" UK',
            # LinkedIn — firms that lead with LinkedIn over a website
            'site:linkedin.com/company "immigration solicitors" "United Kingdom"',
            'site:linkedin.com/company "immigration law" solicitor UK',
            # Aggregators
            'site:lawsociety.org.uk "immigration law" firm',
            'site:yell.com "immigration solicitor"',
        ],
    },
    "Advisors": {
        "description": "OISC/IAA-regulated UK immigration advisers and corporate immigration consultants",
        "tld_queries": [
            'site:.co.uk "OISC regulated" adviser',
            'site:.co.uk "immigration adviser" OISC',
            'site:.co.uk "OISC" "immigration advice"',
            'site:.co.uk "regulated immigration adviser"',
            'site:.co.uk "immigration advice" "Level 3"',
            'site:.co.uk "immigration advice" "Level 2"',
            'site:.co.uk "immigration advice" "Level 1"',
            'site:.co.uk "OISC accredited"',
            'site:.co.uk "OISC registered"',
        ],
        "extra_queries": [
            # City-based
            '"OISC regulated" adviser London',
            '"OISC regulated" adviser Manchester OR Birmingham',
            '"OISC regulated" adviser Leeds OR Sheffield',
            '"OISC regulated" adviser Bristol OR Cardiff',
            '"OISC regulated" adviser Glasgow OR Edinburgh',
            '"OISC regulated" adviser Liverpool OR Newcastle',
            # OISC/IAA service-type
            '"immigration adviser" OISC UK',
            '"regulated immigration adviser" UK',
            '"OISC" "Level 3" adviser UK',
            '"immigration advice" OISC UK',
            # Corporate consultants on non-.co.uk domains
            '"corporate immigration" consultant UK',
            '"global mobility" immigration consultant UK',
            '"sponsor licence" consultant UK',
            '"right to work" compliance consultant UK',
            '"business immigration" services UK',
            '"global mobility" consultant UK',
            # LinkedIn
            'site:linkedin.com/company "immigration adviser" "United Kingdom"',
            'site:linkedin.com/company "immigration consultant" "United Kingdom"',
            'site:linkedin.com/company "global mobility" immigration UK',
            # Aggregators
            'site:yell.com "immigration adviser" UK',
            'site:trustpilot.com "immigration adviser" UK',
        ],
    },
    "LegaltechBrokers": {
        "description": "UK legaltech consultants, resellers, and integration partners who help law firms adopt immigration software — potential channel partners for LawFairy",
        "tld_queries": [
            'site:.co.uk "immigration case management" software',
            'site:.co.uk "immigration software"',
            'site:.co.uk "immigration technology"',
            'site:.co.uk "visa management system"',
            'site:.co.uk "immigration management" platform',
            'site:.co.uk "legal technology" immigration',
            'site:.co.uk "immigration outsourcing"',
            'site:.co.uk "immigration workflow" software',
        ],
        "extra_queries": [
            # Legaltech consultants and market advisors
            '"legal technology" consultant UK',
            '"legaltech" consultant UK',
            '"legal software" consultant UK',
            '"legal tech" advisor UK',
            '"legal technology" advisor "law firm" UK',
            # Resellers and implementation partners
            '"legal software" reseller UK',
            '"legal software" implementation partner UK',
            '"practice management" implementation partner UK',
            '"LEAP" certified partner UK',
            '"Actionstep" implementation partner UK',
            # Integration / channel partners
            '"immigration software" reseller UK',
            '"immigration case management" partner UK',
            '"legal IT" consultant UK',
            '"legal IT" consultancy UK',
            # Notable named consultants
            '"legaltech" consultancy UK site:linkedin.com',
            'site:linkedin.com/company "legal technology" consultant "United Kingdom"',
            'site:linkedin.com/company "legaltech" consultant UK',
            # Directories
            'site:legalitprofessionals.com consultancy',
            'site:legaltechnology.com consultant UK',
        ],
    },
    "Charities": {
        "description": "UK charities, NGOs and non-profits providing immigration advice or support services",
        "tld_queries": [
            'site:.org.uk "immigration advice" charity',
            'site:.org.uk "immigration support" charity',
            'site:.org.uk "asylum seeker" support',
            'site:.org.uk "refugee" immigration advice',
            'site:.org.uk "OISC" charity OR "IAA" charity',
            'site:.org.uk "immigration" "free advice"',
            'site:.org.uk "immigration" "legal aid"',
            'site:.org.uk "migrant" support charity',
            'site:.org.uk "right to remain" support',
        ],
        "extra_queries": [
            # National charities
            '"immigration advice" charity London',
            '"immigration support" charity Manchester OR Birmingham',
            '"refugee support" charity UK',
            '"asylum seeker support" charity UK',
            '"immigration legal aid" charity UK',
            '"migrant advice" charity UK',
            # City-based
            '"immigration advice" charity Leeds OR Sheffield',
            '"immigration advice" charity Bristol OR Cardiff',
            '"immigration advice" charity Glasgow OR Edinburgh',
            # Type-specific
            '"community" "immigration advice" charity UK',
            '"faith-based" "immigration" support UK',
            '"immigration" "drop-in" advice charity UK',
            '"immigration" caseworker charity UK',
            # LinkedIn
            'site:linkedin.com/company "immigration" charity "United Kingdom"',
            'site:linkedin.com/company "refugee" charity UK',
            # Directories
            'site:charitychoice.co.uk "immigration" advice',
            'site:gov.uk "immigration advice" "registered charity"',
            'site:oisc.gov.uk charity registered',
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


def fetch_all_existing_companies(spreadsheet_id: str, credentials_file: str) -> tuple[set[str], set[str]]:
    """Return (names, domains) across ALL tabs — for cross-tab deduplication."""
    all_names: set[str] = set()
    all_domains: set[str] = set()
    import config as _cfg
    for tab in _cfg.IMMIGRATION_TABS:
        names, domains = fetch_existing_companies(spreadsheet_id, credentials_file, tab)
        all_names |= names
        all_domains |= domains
    return all_names, all_domains


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

Important: many companies in this category use .com, .io, .law, or other domains —
not just .co.uk. Use all search strategies below to avoid missing them.

Instructions:

1. START with site:.co.uk searches — these surface companies with a UK domain:
{tld_block}

2. Then run these supplementary queries (city, service-type, LinkedIn, directories):
{extra_block}

3. Beyond the queries above, generate your own additional searches to catch companies
   that might be missed. Useful angles:
   - Named city combinations you haven't tried yet (e.g. "immigration solicitor" Bristol)
   - Specific visa types not yet covered (e.g. "investor visa", "student visa", "spouse visa")
   - Professional directories (Chambers, Legal 500, Trustpilot, Yell, Checkatrade)
   - LinkedIn company search for firms that don't rank well on Google
   - Review sites (Google Maps, Trustpilot) for "immigration solicitor near [city]"

4. For EVERY real company found, immediately call sheets_append_company.
   The tool automatically skips duplicates.
   Only skip: companies clearly not based in the UK, or not in this category ({description}).

5. After every 5 searches, keep going with new angles you haven't tried yet.

6. When done, print a summary: total searches run, total companies added.

Be exhaustive. A company on a .com domain is just as valid as one on .co.uk.
Cover the whole UK — not just London.
""".strip()


async def main(tab: str) -> None:
    credentials_file = str(PROJECT_ROOT / cfg.CREDENTIALS_FILE)
    _validate_startup(credentials_file)

    existing_names, existing_domains = fetch_all_existing_companies(
        cfg.SPREADSHEET_ID, credentials_file
    )
    print(f"Existing companies across all tabs: {len(existing_names)} (cross-tab dedup active)")

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
        max_iterations=max_iter,
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

    async def on_progress(text: str, **kwargs) -> None:
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
    content = result.content if result else ""
    if content and content.strip() != "I've completed processing but have no response to give.":
        print("\nFinal response:")
        print(content)
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

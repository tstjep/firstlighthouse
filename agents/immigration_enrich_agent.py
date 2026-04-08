#!/usr/bin/env python3
"""
Company Info Enrichment Agent — Immigration Finder
====================================================
Reads the Google Sheet for a given immigration tab and completes any rows
that have missing information (website, LinkedIn, size, HQ location, notes).

This handles manually-added companies: a human can type a company name
(or name + website) directly into the sheet, and this agent will find and
fill in all remaining fields.

Row selection is done in Python before the agent starts — a row needs
enrichment when:
  - notes is empty  (agent-generated rows always have notes)
  - company_name is non-empty

Usage:
    python agents/immigration_enrich_agent.py             # uses DEFAULT_TAB from config
    python agents/immigration_enrich_agent.py --tab LawFirms
    python agents/immigration_enrich_agent.py --tab Advisors
    python agents/immigration_enrich_agent.py --tab Charities
    python agents/immigration_enrich_agent.py --tab LegaltechBrokers

LLM provider (in priority order):
    1. VERTEX_PROJECT in config.py  (Vertex AI via service account)
    2. ANTHROPIC_API_KEY env var
    3. LLM_API_KEY env var

Override model: LLM_MODEL env var (default: vertex_ai/gemini-2.5-flash)
"""

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config as cfg
from agents.provider import build_provider
from tools.serp_tool import SerpSearchTool
from tools.sheets_update_info_tool import SheetsUpdateInfoTool

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

_PREFETCH_CONCURRENCY = 20  # max parallel SerpAPI requests
_CHUNK_SIZE = 20            # companies per LLM agent call
_LINKEDIN_RE = re.compile(r"linkedin\.com", re.I)
_DOMAIN_RE   = re.compile(r"https?://(?:www\.)?([^/?#]+)")

# Immigration sheet column indices (0-based) — same layout for all tabs
_COL_COMPANY_NAME = 0  # A
_COL_RATING       = 2  # C
_COL_NOTES        = 3  # D
_COL_WEBSITE      = 4  # E
_COL_LINKEDIN     = 5  # F
_COL_SIZE         = 6  # G
_COL_HQ_LOCATION  = 7  # H

# Tab-specific search hints for generating better SerpAPI queries
_TAB_SEARCH_CONTEXT = {
    "LawFirms":          "immigration solicitor law firm UK",
    "Advisors":          "immigration adviser OISC UK",
    "Charities":         "immigration charity UK refugee",
    "LegaltechBrokers":  "legaltech consultant law firm software UK",
}


def _company_queries(company: dict, tab: str = "LawFirms") -> tuple[str | None, str | None]:
    """Return (query_a, query_b) for SerpAPI prefetch. Either may be None."""
    name     = (company.get("company_name") or "").strip()
    website  = (company.get("website")      or "").strip()
    linkedin = (company.get("linkedin")     or "").strip()

    context = _TAB_SEARCH_CONTEXT.get(tab, "immigration UK")

    # Search A — general company info
    if website and not _LINKEDIN_RE.search(website):
        m = _DOMAIN_RE.match(website)
        domain = m.group(1) if m else website
        query_a = f'"{domain}"'
    elif name:
        query_a = f'"{name}" {context}'
    else:
        query_a = None

    # Search B — LinkedIn (skip if already known)
    query_b = f'site:linkedin.com/company "{name}"' if name and not linkedin else None

    return query_a, query_b


async def _prefetch_searches(
    companies: list[dict],
    api_key: str,
    tab: str = "LawFirms",
    concurrency: int = _PREFETCH_CONCURRENCY,
) -> dict[int, dict[str, str]]:
    """Fire all SerpAPI searches in parallel.

    Returns {row_index: {"a": results_text, "b": results_text}}.
    Uses SerpSearchTool so disk caching is applied automatically.
    """
    tool = SerpSearchTool(api_key=api_key)
    sem  = asyncio.Semaphore(concurrency)

    async def bounded(query: str) -> str:
        async with sem:
            return await tool.execute(query=query, num=5)

    jobs: list[tuple[int, str, object]] = []
    for c in companies:
        qa, qb = _company_queries(c, tab=tab)
        if qa:
            jobs.append((c["row_index"], "a", bounded(qa)))
        if qb:
            jobs.append((c["row_index"], "b", bounded(qb)))

    if not jobs:
        return {}

    total = len(jobs)
    print(f"  Pre-fetching {total} SerpAPI searches for {len(companies)} companies "
          f"(concurrency={concurrency}, num=5)…")

    raw = await asyncio.gather(*(coro for _, _, coro in jobs))

    results: dict[int, dict[str, str]] = {}
    for (row_idx, label, _), text in zip(jobs, raw):
        results.setdefault(row_idx, {})[label] = text

    print(f"  Done — {total} searches complete.")
    return results


def _validate_startup(credentials_file: str) -> None:
    """Fail fast with clear messages for common misconfigurations."""
    errors: list[str] = []
    if not cfg.SPREADSHEET_ID:
        errors.append("SPREADSHEET_ID is not set in config.py")
    if not cfg.SERPAPI_KEY:
        errors.append("SERPAPI_KEY is not set in config.py")
    if not Path(credentials_file).exists():
        errors.append(
            f"Service account credentials not found: {credentials_file}\n"
            "  Set CREDENTIALS_FILE in config.py to point to a valid JSON key file."
        )
    if errors:
        print("[startup] Configuration errors — cannot continue:")
        for msg in errors:
            print(f"  ✗ {msg}")
        sys.exit(1)


def fetch_incomplete_rows(
    spreadsheet_id: str,
    credentials_file: str,
    tab: str,
    min_rating: int = 0,
) -> list[dict]:
    """Return rows that need enrichment using plain Python — no AI involved.

    A row needs enrichment when:
      - notes is empty  (agent-generated rows always have notes filled)
      - at least one of company_name / website / linkedin is non-empty
        (something to identify the company with)

    min_rating: if > 0, skip rows with a confirmed numeric rating below this value.
                Rows with no rating or a provisional ~N rating are always included.
    """
    try:
        creds = Credentials.from_service_account_file(credentials_file, scopes=_SCOPES)
        service = build("sheets", "v4", credentials=creds)
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=f"{tab}!A:H")
            .execute()
        )
    except Exception as exc:
        print(f"[warning] fetch_incomplete_rows: Google API error: {exc}")
        return []

    rows = result.get("values", [])
    if len(rows) <= 1:
        return []

    incomplete = []
    for i, row in enumerate(rows[1:], start=2):
        def cell(idx: int) -> str:
            return row[idx].strip() if idx < len(row) else ""

        company_name = cell(_COL_COMPANY_NAME)
        notes        = cell(_COL_NOTES)
        website      = cell(_COL_WEBSITE)
        linkedin     = cell(_COL_LINKEDIN)
        rating_raw   = cell(_COL_RATING)

        if notes:
            continue
        if not company_name and not website and not linkedin:
            continue
        if min_rating > 0:
            try:
                if int(rating_raw) < min_rating:
                    continue
            except (ValueError, TypeError):
                pass  # unrated / provisional ~N — include regardless

        incomplete.append({
            "row_index":    i,
            "company_name": company_name,
            "website":      website,
            "linkedin":     linkedin,
            "size":         cell(_COL_SIZE),
            "hq_location":  cell(_COL_HQ_LOCATION),
        })

    return incomplete


def build_task(
    incomplete_rows: list[dict],
    tab: str = "LawFirms",
    prefetched: dict | None = None,
) -> str:
    """Build the agent task prompt.

    When `prefetched` is supplied the search results are embedded directly in
    the prompt and the agent does NOT need the serp_search tool — it only calls
    sheets_update_company_info.
    """
    tab_context = {
        "LawFirms":         "UK immigration law firms (SRA-regulated solicitors)",
        "Advisors":         "UK immigration advisers (OISC-regulated)",
        "Charities":        "UK immigration charities and NGOs providing immigration advice",
        "LegaltechBrokers": "UK legaltech consultants and law firm software advisers",
    }.get(tab, "UK immigration companies")

    context_note = (
        f"\nCONTEXT: These companies are {tab_context}. "
        "In the notes field, write a one-sentence description of what the company does "
        "and what type of immigration work they focus on (e.g. corporate immigration, "
        "asylum, family visas, OISC advice, software consulting, etc.).\n"
    )

    if prefetched is not None:
        sections: list[str] = []
        for row in incomplete_rows:
            row_idx = row["row_index"]
            name = row.get("company_name") or "(unknown)"
            known = {k: v for k, v in row.items() if v and k != "row_index"}
            lines = [f"### Row {row_idx}: {name}"]
            if known:
                lines.append(f"Known data: {json.dumps(known, ensure_ascii=False)}")
            res = prefetched.get(row_idx, {})
            if res.get("a"):
                lines.append(f"Search results:\n{res['a']}")
            if res.get("b"):
                lines.append(f"LinkedIn search:\n{res['b']}")
            if not res:
                lines.append("(no search results available — use known data only)")
            sections.append("\n".join(lines))

        companies_block = "\n\n---\n\n".join(sections)

        return f"""You are a company-data enrichment agent. Below are {len(incomplete_rows)} companies with pre-fetched search results. Extract information from the results and call sheets_update_company_info for each row.
{context_note}
Instructions:
- From search result titles, URLs, and snippets (do NOT visit URLs), extract:
    company_name  (always required)
    website       (official company URL; skip if already correct in Known data)
    linkedin      (LinkedIn company page URL; skip if already in Known data)
    size          (employee count: "1-10", "11-50", "51-200", "201-500", "501-1000", "1000+")
    hq_location   (city and country, e.g. "London, UK")
    notes         (one-sentence description of what the company does and their immigration focus)
- Call sheets_update_company_info with row_index and the fields you found.
    Always pass company_name.
    Do NOT pass fields already listed under "Known data" (don't overwrite).
    If "Known data" website is a linkedin.com URL: pass it as linkedin, omit website.
    Do NOT add new rows.
- After all companies: print total updated and list any where nothing was found.

---

{companies_block}
""".strip()

    # Fallback: original prompt with search tool
    rows_json = json.dumps(incomplete_rows, ensure_ascii=False, separators=(",", ":"))
    return f"""
You are a company-data enrichment agent. Fill in missing information for the
{len(incomplete_rows)} companies listed below.
{context_note}
Companies to enrich (row_index is the sheet row to update):
{rows_json}

Many rows have only a URL in the `website` field and no `company_name`.
Some `website` values may be LinkedIn URLs or directory pages — handle them:
  - If `website` is a linkedin.com URL → move it to `linkedin`, clear `website`,
    then search for the real company website.
  - If `website` is a directory/aggregator page → extract the company name from
    the URL path or search for it, then find the real website.

For each company:

1. Determine what you already know (company_name, website, linkedin from the list).

2. Run 1–2 targeted searches:

   Search A — find company name, real website, size, location, description:
     "<domain or company name>" immigration solicitor OR adviser UK

   Search B — find LinkedIn (only if not already known):
     site:linkedin.com/company "<company name>"

3. From titles and snippets (do NOT visit URLs), extract:
   - Company name               (always required — fill even if currently empty)
   - Official website URL       (skip if already correct in the list)
   - LinkedIn company page URL  (skip if already in the list)
   - Approximate employee count: "1-10", "11-50", "51-200", "201-500", "501-1000", "1000+"
   - HQ city and country (e.g. "London, UK")
   - A one-sentence description of what the company does and their immigration focus

4. Call `sheets_update_company_info` with row_index and all fields found.
   - Always pass company_name if you found it.
   - Only pass other fields you are confident about.
   - Do NOT overwrite a field that already has a correct value.

5. After all companies are processed, print a summary:
   - Total enriched (at least one field added)
   - Any companies where nothing could be found

Rules:
- Do NOT add new rows — only update via sheets_update_company_info.
- At most 2 searches per company.
- Be conservative: only write data clearly supported by the snippets.
""".strip()


async def _run_enrichment_chunk(
    chunk: list[dict],
    prefetched: dict,
    tab: str,
    credentials_file: str,
    provider,
    model: str,
    chunk_num: int,
    total_chunks: int,
) -> str | None:
    """Run one agent call for a slice of companies with pre-fetched search results."""
    max_iterations = len(chunk) * 3 + 10

    bus = MessageBus()
    agent = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=PROJECT_ROOT,
        model=model,
        temperature=0.1,
        max_tokens=32768,
        max_iterations=max_iterations,
        memory_window=60,
    )

    agent.tools.register(SheetsUpdateInfoTool(
        spreadsheet_id=cfg.SPREADSHEET_ID,
        credentials_file=credentials_file,
        sheet_name=tab,
    ))

    async def on_progress(text: str) -> None:
        if text:
            print(f"[chunk {chunk_num}/{total_chunks}] {text}")

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    result = await agent.process_direct(
        content=build_task(chunk, tab=tab, prefetched=prefetched),
        session_key=f"enrich:{tab}:{run_id}:c{chunk_num}",
        channel="cli",
        chat_id=f"enrich_{tab}_{run_id}_c{chunk_num}",
        on_progress=on_progress,
    )
    await agent.close_mcp()
    return result


async def main(tab: str, max_rows: int = 0, min_rating: int = 0) -> None:
    credentials_file = str(PROJECT_ROOT / cfg.CREDENTIALS_FILE)
    _validate_startup(credentials_file)

    print("Starting company info enrichment agent")
    print(f"Tab:   {tab}")
    print(f"Sheet: {cfg.SPREADSHEET_ID}  tab: {tab}")
    if min_rating > 0:
        print(f"Min rating filter: {min_rating}+ (unrated rows always included)")

    incomplete = fetch_incomplete_rows(cfg.SPREADSHEET_ID, credentials_file, tab, min_rating=min_rating)
    if max_rows and len(incomplete) > max_rows:
        print(f"Rows needing enrichment: {len(incomplete)} (capped to {max_rows})")
        incomplete = incomplete[:max_rows]
    else:
        print(f"Rows needing enrichment: {len(incomplete)}")
    if not incomplete:
        print("Nothing to enrich — all rows already have notes filled.")
        return

    for row in incomplete:
        filled  = [k for k in ("website", "linkedin", "size", "hq_location") if row[k]]
        missing = [k for k in ("website", "linkedin", "size", "hq_location") if not row[k]]
        print(f"  row {row['row_index']:>3}  {row['company_name']}"
              + (f"  [has: {', '.join(filled)}]" if filled else "")
              + (f"  [missing: {', '.join(missing)}]" if missing else ""))

    provider, model = build_provider()
    print(f"\nModel: {model}")

    print()
    prefetched = await _prefetch_searches(incomplete, cfg.SERPAPI_KEY, tab=tab)

    chunks = [incomplete[i:i + _CHUNK_SIZE] for i in range(0, len(incomplete), _CHUNK_SIZE)]
    print(f"\nProcessing {len(incomplete)} companies in {len(chunks)} chunk(s) "
          f"of up to {_CHUNK_SIZE}:\n" + "-" * 60)

    for chunk_num, chunk in enumerate(chunks, 1):
        print(f"\n--- Chunk {chunk_num}/{len(chunks)} ({len(chunk)} companies) ---")
        result = await _run_enrichment_chunk(
            chunk=chunk,
            prefetched=prefetched,
            tab=tab,
            credentials_file=credentials_file,
            provider=provider,
            model=model,
            chunk_num=chunk_num,
            total_chunks=len(chunks),
        )
        if result and result.strip() != "I've completed processing but have no response to give.":
            print(f"\n[chunk {chunk_num}] {result}")
        else:
            print(f"\n[chunk {chunk_num}] Done (no summary returned).")

    print("\n" + "-" * 60)
    print(f"Enrichment complete — {len(chunks)} chunk(s) processed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Enrich incomplete company rows in the immigration leads sheet."
    )
    parser.add_argument(
        "--tab",
        default=cfg.DEFAULT_TAB,
        choices=cfg.IMMIGRATION_TABS,
        help=f"Sheet tab to enrich (default: {cfg.DEFAULT_TAB})",
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=0,
        metavar="N",
        help="Cap the number of rows to enrich (0 = no limit)",
    )
    parser.add_argument(
        "--min-rating",
        type=int,
        default=0,
        metavar="N",
        help="Skip rows with a confirmed rating below N (0 = include all, unrated rows always included)",
    )
    args = parser.parse_args()
    asyncio.run(main(args.tab, args.max_rows, min_rating=args.min_rating))

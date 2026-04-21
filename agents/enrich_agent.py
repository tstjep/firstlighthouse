#!/usr/bin/env python3
"""
Company Info Enrichment Agent
==============================
Reads the local JSON store for a given segment and fills in any rows with missing
information (website, LinkedIn, size, HQ location, notes).

Handles manually-added companies — a company name alone is enough to start.

Usage:
    python agents/enrich_agent.py --campaign hr-saas-ch --tab ProfServices
    python agents/enrich_agent.py --campaign sales-tools-uk --tab UKSaaS --min-rating 8
    python agents/enrich_agent.py --campaign hr-saas-ch --tab ProfServices --max-rows 10
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
from campaign import Campaign
from agents.provider import build_provider
from store import ResultStore
from tools.serp_tool import SerpSearchTool
from tools.json_update_info_tool import JsonUpdateInfoTool

from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus

_PREFETCH_CONCURRENCY = 20
_CHUNK_SIZE = 20
_LINKEDIN_RE = re.compile(r"linkedin\.com", re.I)
_DOMAIN_RE   = re.compile(r"https?://(?:www\.)?([^/?#]+)")


def _company_queries(company: dict, enrich_context: str) -> tuple[str | None, str | None]:
    name     = (company.get("company_name") or "").strip()
    website  = (company.get("website")      or "").strip()
    linkedin = (company.get("linkedin")     or "").strip()

    if website and not _LINKEDIN_RE.search(website):
        m = _DOMAIN_RE.match(website)
        domain = m.group(1) if m else website
        query_a = f'"{domain}"'
    elif name:
        query_a = f'"{name}" {enrich_context}'
    else:
        query_a = None

    query_b = f'site:linkedin.com/company "{name}"' if name and not linkedin else None
    return query_a, query_b


async def _prefetch_searches(
    companies: list[dict],
    api_key: str,
    enrich_context: str,
    concurrency: int = _PREFETCH_CONCURRENCY,
) -> dict[int, dict[str, str]]:
    tool = SerpSearchTool(api_key=api_key)
    sem  = asyncio.Semaphore(concurrency)

    async def bounded(query: str) -> str:
        async with sem:
            return await tool.execute(query=query, num=5)

    jobs: list[tuple[int, str, object]] = []
    for c in companies:
        qa, qb = _company_queries(c, enrich_context=enrich_context)
        if qa:
            jobs.append((c["row_index"], "a", bounded(qa)))
        if qb:
            jobs.append((c["row_index"], "b", bounded(qb)))

    if not jobs:
        return {}

    print(f"  Pre-fetching {len(jobs)} SerpAPI searches for {len(companies)} companies…")
    raw = await asyncio.gather(*(coro for _, _, coro in jobs))

    results: dict[int, dict[str, str]] = {}
    for (row_idx, label, _), text in zip(jobs, raw):
        results.setdefault(row_idx, {})[label] = text

    print(f"  Done — {len(jobs)} searches complete.")
    return results


def fetch_incomplete_rows(
    store: ResultStore,
    min_rating: int = 0,
) -> list[dict]:
    """Return rows from the JSON store that are missing enrichment data (no notes)."""
    rows = store.get_rows()
    incomplete = []
    for row in rows:
        if row.get("notes"):
            continue
        name    = (row.get("name") or "").strip()
        website = (row.get("website") or "").strip()
        linkedin = (row.get("linkedin") or "").strip()
        if not name and not website and not linkedin:
            continue
        if min_rating > 0:
            try:
                if int(row.get("rating") or 0) < min_rating:
                    continue
            except (ValueError, TypeError):
                pass
        incomplete.append({
            "row_index":    row["row_index"],
            "company_name": name,
            "website":      website,
            "linkedin":     linkedin,
            "size":         row.get("size", ""),
            "hq_location":  row.get("hq", ""),
        })
    return incomplete


def build_task(
    campaign: Campaign,
    incomplete_rows: list[dict],
    prefetched: dict | None = None,
) -> str:
    context_note = (
        f"\nCONTEXT: These companies are potential prospects for: {campaign.product_context or campaign.name}. "
        "In the notes field, write a one-sentence description of what the company does "
        "and what specific work they focus on.\n"
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

        return f"""You are a company-data enrichment agent. Below are {len(incomplete_rows)} companies with pre-fetched search results. Extract information from the results and call update_company_info for each row.
{context_note}
Instructions:
- From search result titles, URLs, and snippets (do NOT visit URLs), extract:
    company_name  (always required)
    website       (official company URL; skip if already correct in Known data)
    linkedin      (LinkedIn company page URL; skip if already in Known data)
    size          (employee count: "1-10", "11-50", "51-200", "201-500", "501-1000", "1000+")
    hq_location   (city and country, e.g. "London, UK")
    notes         (one-sentence description of what the company does and their focus area)
- Call update_company_info with row_index and the fields you found.
    Always pass company_name.
    Do NOT pass fields already listed under "Known data" (don't overwrite).
    If "Known data" website is a linkedin.com URL: pass it as linkedin, omit website.
    Do NOT add new rows.
- After all companies: print total updated and list any where nothing was found.

---

{companies_block}
""".strip()

    # Fallback: tool-based prompt (agent runs its own searches)
    rows_json = json.dumps(incomplete_rows, ensure_ascii=False, separators=(",", ":"))
    return f"""
You are a company-data enrichment agent. Fill in missing information for the
{len(incomplete_rows)} companies listed below.
{context_note}
Companies to enrich (row_index is the identifier to update):
{rows_json}

For each company:
1. Run 1–2 targeted searches to find company name, website, LinkedIn, size, location, description.
2. Call update_company_info with all fields you found.
   - Always pass company_name.
   - Do NOT overwrite fields that already have correct values.
   - Do NOT add new rows.
3. After all companies: print a summary.
""".strip()


async def _run_enrichment_chunk(
    chunk: list[dict],
    prefetched: dict,
    campaign: Campaign,
    store: ResultStore,
    provider,
    model: str,
    chunk_num: int,
    total_chunks: int,
) -> str | None:
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

    agent.tools.register(JsonUpdateInfoTool(store=store))

    async def on_progress(text: str) -> None:
        if text:
            print(f"[chunk {chunk_num}/{total_chunks}] {text}")

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    result = await agent.process_direct(
        content=build_task(campaign, chunk, prefetched=prefetched),
        session_key=f"enrich:{campaign.id}:{run_id}:c{chunk_num}",
        channel="cli",
        chat_id=f"enrich_{campaign.id}_{run_id}_c{chunk_num}",
        on_progress=on_progress,
    )
    await agent.close_mcp()
    return result.content if result else None


async def main(campaign: Campaign, max_rows: int = 0, min_rating: int = 0) -> None:
    if not cfg.SERPAPI_KEY:
        print("  ✗ SERPAPI_KEY not set in config.py")
        sys.exit(1)

    store = ResultStore(campaign.id)

    print(f"Starting enrichment agent — Campaign: {campaign.name}")
    if min_rating > 0:
        print(f"Min rating filter: {min_rating}+")

    incomplete = fetch_incomplete_rows(store, min_rating=min_rating)
    if max_rows and len(incomplete) > max_rows:
        print(f"Rows needing enrichment: {len(incomplete)} (capped to {max_rows})")
        incomplete = incomplete[:max_rows]
    else:
        print(f"Rows needing enrichment: {len(incomplete)}")
    if not incomplete:
        print("Nothing to enrich — all rows already have notes filled.")
        return

    provider, model = build_provider()
    print(f"Model: {model}\n")

    prefetched = await _prefetch_searches(
        incomplete, cfg.SERPAPI_KEY, enrich_context=campaign.product_context or campaign.name
    )

    chunks = [incomplete[i:i + _CHUNK_SIZE] for i in range(0, len(incomplete), _CHUNK_SIZE)]
    print(f"\nProcessing {len(incomplete)} companies in {len(chunks)} chunk(s):\n" + "-" * 60)

    for chunk_num, chunk in enumerate(chunks, 1):
        print(f"\n--- Chunk {chunk_num}/{len(chunks)} ({len(chunk)} companies) ---")
        result = await _run_enrichment_chunk(
            chunk=chunk, prefetched=prefetched, campaign=campaign,
            store=store, provider=provider, model=model,
            chunk_num=chunk_num, total_chunks=len(chunks),
        )
        content = result or ""
        if content and content.strip() != "I've completed processing but have no response to give.":
            print(f"\n[chunk {chunk_num}] {content}")

    print(f"\nEnrichment complete — {len(chunks)} chunk(s) processed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Company enrichment agent")
    parser.add_argument("--campaign",   required=True)
    parser.add_argument("--max-rows",   type=int, default=0)
    parser.add_argument("--min-rating", type=int, default=0)
    args = parser.parse_args()

    campaign = Campaign.load(args.campaign)
    asyncio.run(main(campaign, max_rows=args.max_rows, min_rating=args.min_rating))

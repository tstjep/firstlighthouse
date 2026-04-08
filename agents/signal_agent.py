#!/usr/bin/env python3
"""
Immigration Signal Detection Agent
====================================
For each company in the sheet, runs two SerpAPI searches against their website,
then batches all the results to the LLM for signal analysis in one go.
This minimises SerpAPI calls while using the LLM's judgement for everything that
doesn't need a live search.

Signals
-------
  corporate   — sponsor licence / skilled worker / corporate immigration clients
  tech        — client portal / online application / document upload / case tracking
  multivisa   — handles 3+ visa types (family, student, spouse, investor, etc.)
  highvolume  — large team or many clients mentioned
  growth      — hiring / new office / expanding

Strategy
--------
  Phase 1 (SerpAPI): 2 site: searches per company to fetch raw evidence
  Phase 2 (LLM):     batch analysis — up to 20 companies per LLM call
                     LLM reads all snippets and returns Yes/No + source for each signal
  Phase 3 (Sheets):  write results back

Usage
-----
  python agents/signal_agent.py
  python agents/signal_agent.py --tab Advisors
  python agents/signal_agent.py --tab LawFirms --skip-done
  python agents/signal_agent.py --tab LawFirms --dry-run    # print results, don't write
"""

import argparse
import asyncio
import json
import re
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config as cfg
from agents.provider import build_provider
from tools.sheets_update_signal_tool import SheetsUpdateSignalTool, VALID_SIGNALS

import httpx
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# ── Column indices for reading the sheet (0-based, A:S) ───────────────────
_COL_NAME       = 0   # A
_COL_RATING     = 2   # C
_COL_NOTES      = 3   # D
_COL_WEBSITE    = 4   # E
_COL_SIZE       = 6   # G
_COL_SCANNED    = 8   # I  — Date Added / used as "scanned" marker
_COL_CORPORATE  = 9   # J  — first signal column

# Tabs that support signal detection (LegaltechBrokers is partners-only, no signals needed)
_SIGNAL_TABS = {"LawFirms", "Advisors", "Charities"}

# The signal agent writes a date to this column after processing a row.
# This is separate from "Date Added" (col I handled by search agent).
# We repurpose _COL_SCANNED to track whether signal detection was run.
# Practically: after writing signals, the agent writes today's date to col I.
# Rows added by the search agent have col I as Date Added already, but
# signal agent will overwrite with the signal-scan date on first run.
# --skip-done checks this column: if it looks like a scan date, skip the row.


def _tab_supports_signals(tab: str) -> bool:
    return tab in _SIGNAL_TABS


def _is_already_scanned(row: list) -> bool:
    """True if the signal agent has already processed this row.
    After running, col J (corporate signal) always has Yes or No."""
    corporate_val = row[_COL_CORPORATE].strip().lower() if _COL_CORPORATE < len(row) else ""
    return corporate_val in ("yes", "no")


# Signal column indices (0-based) — J, L, N, P, R
_ALL_SIGNAL_COLS = [9, 11, 13, 15, 17]


def _is_all_no(row: list) -> bool:
    """True if every signal column is 'No' — row was scanned but nothing detected."""
    if not _is_already_scanned(row):
        return False
    return all(
        (row[c].strip().lower() if c < len(row) else "") == "no"
        for c in _ALL_SIGNAL_COLS
    )

_SCOPES_READ  = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
_SCOPES_WRITE = ["https://www.googleapis.com/auth/spreadsheets"]

SERP_CACHE_DIR = PROJECT_ROOT / "serp_cache"

# ── TASK prompt used by the LLM in batch analysis ─────────────────────────

TASK = """
You are a signal-detection analyst for LawFairy, a legaltech company selling
immigration case management software to UK immigration firms.

You will receive a JSON array of companies. Each company has:
  - row_index: sheet row number
  - company_name: name of the company
  - search_results: raw titles and snippets from two Google searches on their website

Your job is to detect five buying signals for each company:

  corporate   → sponsor licence / skilled worker / corporate immigration clients
                Keywords: "sponsor licence", "skilled worker", "corporate immigration",
                "business visa", "corporate clients", "employer", "right to work",
                "points-based system", "work permit", "Tier 2"

  tech        → the firm itself offers digital tools to its own clients
                Keywords: "client portal", "document upload", "case tracking",
                "secure upload", "track your case", "our portal", "our app",
                "our software", "our platform", "our system", "our online service"
                IMPORTANT: Do NOT mark Yes just because the firm mentions UKVI's
                online application process or gov.uk forms — that is the government
                system, not the firm's own tool. Only mark Yes if the firm has built
                or subscribes to a portal/system they offer to their own clients.

  multivisa   → handles 3 or more distinct visa types
                Count mentions of: family visa, spouse visa, student visa, investor visa,
                ancestry visa, skilled worker visa, partner visa, fiancé visa,
                EEA family permit, British citizenship, ILR, work visa, visit visa,
                entrepreneur visa, graduate visa, dependent visa.
                Mark Yes only if 3+ types are clearly mentioned.

  highvolume  → large team or high caseload
                Keywords: team of 3+ named solicitors/lawyers/advisers, "award-winning",
                "leading immigration firm", "hundreds of clients",
                "thousands of applications", "established in [year before 2010]"

  growth      → actively growing
                Keywords: "we are hiring", "join our team", "vacancies", "careers",
                "new office", "expanding", "recently opened", "new branch"

Rules:
- Base your judgement ONLY on the provided search_results titles and snippets.
- Be conservative — only mark Yes if the evidence is clear.
- For Yes: source must be the exact title or snippet excerpt containing the keyword,
           followed by the page URL in brackets, e.g. "Skilled Worker Visa services (https://example.co.uk/services)".
- For No:  source must be "not found".
- If search_results is empty or null, mark all signals No with source "no website".

Return a JSON array (one object per company):
[
  {
    "row_index": <int>,
    "signals": {
      "corporate":   {"detected": true/false, "source": "..."},
      "tech":        {"detected": true/false, "source": "..."},
      "multivisa":   {"detected": true/false, "source": "..."},
      "highvolume":  {"detected": true/false, "source": "..."},
      "growth":      {"detected": true/false, "source": "..."}
    }
  },
  ...
]

No other text — just the JSON array.
"""

# ── Website scrape helpers ─────────────────────────────────────────────────

def _extract_text_from_html(html: str) -> str:
    """Strip tags/scripts/styles from raw HTML and return plain text."""
    if not html:
        return ""
    # Remove <script> and <style> blocks entirely
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    # Strip remaining tags
    html = re.sub(r"<[^>]+>", " ", html)
    # Collapse whitespace
    return re.sub(r"\s+", " ", html).strip()


async def _scrape_website(url: str) -> str:
    """Fetch the homepage of url, return extracted plain text (max 8000 chars)."""
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; LawFairyBot/1.0; "
                "+https://lawfairy.io/bot)"
            )
        }
        async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers=headers) as client:
            r = await client.get(url)
            r.raise_for_status()
            return _extract_text_from_html(r.text)[:8000]
    except Exception as exc:
        print(f"  [scrape] Failed to fetch {url}: {exc}")
        return ""


def _scrape_to_results(url: str, text: str) -> list[dict]:
    """Wrap scraped page text into fake SerpAPI result entries for the LLM prompt."""
    if not text:
        return []
    # Split into chunks of ~500 chars so the LLM gets discrete excerpt snippets
    chunk_size = 500
    results = []
    for i in range(0, min(len(text), 4000), chunk_size):
        chunk = text[i:i + chunk_size].strip()
        if chunk:
            results.append({
                "title":   f"[scraped] {url}",
                "snippet": chunk,
                "link":    url,
            })
    return results


def _should_scrape_website(results_a: list, results_b: list) -> bool:
    """Return True when name-based fallback also returned nothing — last resort."""
    return len(results_a) == 0 and len(results_b) == 0


# ── Fallback query helpers ─────────────────────────────────────────────────

def _build_fallback_queries(company_name: str) -> tuple[str, str]:
    """Name-based fallback queries when site: searches return nothing.
    Searches the broader web for the company name combined with signal keywords.
    """
    name = company_name.strip('"')
    query_a = (
        f'"{name}" ("sponsor licence" OR "skilled worker" OR '
        '"corporate immigration" OR "business visa" OR '
        '"immigration solicitor" OR "immigration adviser")'
    )
    query_b = (
        f'"{name}" ("client portal" OR "online application" OR '
        '"document upload" OR "case tracking" OR '
        '"family visa" OR "student visa" OR "spouse visa" OR '
        '"we are hiring" OR "join our team" OR "expanding" OR "new office")'
    )
    return query_a, query_b


def _should_use_fallback(results_a: list, results_b: list) -> bool:
    """Return True when both site: searches returned no results."""
    return len(results_a) == 0 and len(results_b) == 0


# ── SerpAPI helpers ────────────────────────────────────────────────────────

def _domain_from_url(url: str) -> str:
    url = url.strip().lower()
    for prefix in ("https://", "http://", "www."):
        url = url.removeprefix(prefix)
    return url.rstrip("/").split("/")[0]


async def _serp_search(query: str, api_key: str) -> list[dict]:
    """Run one SerpAPI search, return list of {title, snippet, link}."""
    import hashlib
    slug = re.sub(r"[^\w\-]", "_", query)[:60]
    key  = hashlib.md5(query.encode()).hexdigest()[:8]
    cache_file = SERP_CACHE_DIR / f"{date.today().isoformat()}_{slug}_{key}.json"

    SERP_CACHE_DIR.mkdir(exist_ok=True)
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text())
            return data.get("organic_results", [])
        except Exception:
            pass

    params = {
        "q": query, "api_key": api_key,
        "num": 10, "engine": "google",
        "gl": "gb", "cr": "countryGB",
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get("https://serpapi.com/search", params=params)
            r.raise_for_status()
            data = r.json()
            try:
                cache_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
            except OSError:
                pass
            return data.get("organic_results", [])
    except Exception as exc:
        print(f"[serp] Error for query {query!r}: {exc}")
        return []


def _format_results(results: list[dict]) -> str:
    lines = []
    for r in results[:10]:
        title   = r.get("title", "")
        snippet = r.get("snippet", "")
        link    = r.get("link", "")
        lines.append(f"Title: {title}\nSnippet: {snippet}\nURL: {link}")
    return "\n\n".join(lines) if lines else ""


# ── Sheet helpers ──────────────────────────────────────────────────────────

def _read_companies(
    tab: str,
    credentials_file: str,
    skip_done: bool,
    retry_empty: bool = False,
    min_rating: int = 5,
) -> list[dict]:
    """Read all companies from the sheet. Returns list of dicts.

    skip_done:   skip rows already scanned (col J has Yes or No)
    retry_empty: override skip_done for rows where all 5 signals are No
                 (useful when scrape/fallback was unavailable on first run)
    min_rating:  skip rows with a confirmed numeric rating below this value (default 5)
    """
    creds = Credentials.from_service_account_file(credentials_file, scopes=_SCOPES_READ)
    service = build("sheets", "v4", credentials=creds)
    result = (
        service.spreadsheets().values()
        .get(spreadsheetId=cfg.SPREADSHEET_ID, range=f"{tab}!A:S")
        .execute()
    )
    rows = result.get("values", [])
    companies = []
    for i, row in enumerate(rows[1:], start=2):
        def cell(idx, _row=row):
            return _row[idx].strip() if idx < len(_row) else ""

        if skip_done and _is_already_scanned(row):
            if retry_empty and _is_all_no(row):
                pass  # re-process even though scanned — all signals were No
            else:
                continue

        website = cell(_COL_WEBSITE)
        if not website:
            continue  # nothing to search without a website

        if min_rating > 0:
            rating_raw = cell(_COL_RATING)
            try:
                if int(rating_raw) < min_rating:
                    continue
            except (ValueError, TypeError):
                continue  # skip unrated / provisional ~N rows

        companies.append({
            "row_index":    i,
            "company_name": cell(_COL_NAME),
            "notes":        cell(_COL_NOTES),
            "website":      website,
            "size":         cell(_COL_SIZE),
        })
    return companies


# ── LLM analysis — one company at a time ──────────────────────────────────

async def _llm_analyse_one(
    company: dict,   # {row_index, company_name, search_results: str}
    provider,
    model: str,
) -> dict | None:
    """Analyse signals for a single company. Returns one result dict or None on failure."""
    prompt = (
        TASK
        + "\n\nCompany:\n"
        + json.dumps([company], ensure_ascii=False, indent=2)
        + "\n\nReturn a JSON array with exactly one object."
    )
    try:
        response = await provider.chat(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            max_tokens=8192,
            temperature=0.1,
        )
        text = (response.content or "").strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        parsed = json.loads(text.strip())
        if isinstance(parsed, list) and parsed:
            return parsed[0]
        return None
    except Exception as exc:
        print(f"    [llm error] {exc}")
        return None


# ── Write results for one company ─────────────────────────────────────────

async def _write_company_signals(
    company_result: dict,
    tool: SheetsUpdateSignalTool,
    dry_run: bool,
) -> dict[str, bool]:
    """Write all 5 signals for one company immediately. Returns {signal: detected}."""
    row_idx = company_result.get("row_index")
    signals = company_result.get("signals", {})
    written: dict[str, bool] = {}

    for signal_name in VALID_SIGNALS:
        sig      = signals.get(signal_name, {})
        detected = bool(sig.get("detected", False))
        source   = sig.get("source", "not found") or "not found"
        written[signal_name] = detected

        if dry_run:
            val = "Yes" if detected else "No"
            print(f"    [dry-run] {signal_name:<12} = {val}  |  {source[:70]}")
        else:
            try:
                msg = await tool.execute(
                    row_index=row_idx,
                    signal=signal_name,
                    detected=detected,
                    source=source,
                )
                print(f"    {msg}")
            except Exception as exc:
                print(f"    [error] Failed to write {signal_name} for row {row_idx}: {exc}")

    return written


# ── Main ───────────────────────────────────────────────────────────────────

async def main(tab: str, skip_done: bool = False, dry_run: bool = False, retry_empty: bool = False, min_rating: int = 5) -> None:
    credentials_file = str(PROJECT_ROOT / cfg.CREDENTIALS_FILE)

    print("=" * 60)
    print(f"Immigration signal detection agent")
    print(f"Tab:         {tab}")
    print(f"Skip done:   {skip_done}")
    print(f"Retry empty: {retry_empty}")
    print(f"Dry-run:     {dry_run}")
    print("=" * 60)

    if not _tab_supports_signals(tab):
        print(f"\n[skip] Tab '{tab}' does not support signal detection (partners-only tab).")
        return

    # ── Read companies ─────────────────────────────────────────────────────
    print("\n[1/3] Reading companies from sheet...")
    try:
        companies = _read_companies(tab, credentials_file, skip_done, retry_empty=retry_empty, min_rating=min_rating)
    except Exception as exc:
        print(f"[error] Could not read sheet: {exc}")
        return

    total = len(companies)
    skipped_no_website = 0  # already filtered in _read_companies
    print(f"  → {total} companies to process")
    if not total:
        print("  Nothing to do.")
        return

    provider, model = build_provider()
    print(f"  → LLM: {model}\n")

    # ── SerpAPI + LLM in rolling batches of 20 ────────────────────────────
    # We fetch searches for the whole batch first, then analyse, then write —
    # so results hit the sheet after each batch of 20, not only at the end.
    print(f"[2/3] Fetching search results + running LLM analysis (batch size 20)...")

    tool = SheetsUpdateSignalTool(
        spreadsheet_id=cfg.SPREADSHEET_ID,
        credentials_file=credentials_file,
        sheet_name=tab,
    )

    all_results:   list[dict] = []
    serp_errors  = 0
    llm_errors   = 0
    write_errors = 0

    for idx, company in enumerate(companies, 1):
        domain = _domain_from_url(company["website"])
        print(f"\n[{idx}/{total}] {company['company_name']}  (row {company['row_index']}, {domain})")

        # Step 1: SerpAPI — 2 concurrent searches
        query_a = (
            f'site:{domain} "sponsor licence" OR "skilled worker" OR '
            '"corporate immigration" OR "business visa" OR "our team" OR '
            '"our solicitors" OR "meet the team" OR "our lawyers"'
        )
        query_b = (
            f'site:{domain} "client portal" OR "online application" OR '
            '"document upload" OR "case tracking" OR "family visa" OR '
            '"spouse visa" OR "student visa" OR "investor visa" OR '
            '"we are hiring" OR "join our team" OR "new office" OR "expanding"'
        )
        try:
            results_a, results_b = await asyncio.gather(
                _serp_search(query_a, cfg.SERPAPI_KEY),
                _serp_search(query_b, cfg.SERPAPI_KEY),
            )
            print(f"  [serp] {len(results_a)} results (A) + {len(results_b)} results (B)")
        except Exception as exc:
            print(f"  [serp error] {exc}")
            results_a, results_b = [], []
            serp_errors += 1

        # Fallback 1: name-based queries if site: returned nothing
        if _should_use_fallback(results_a, results_b):
            print(f"  [serp] site: returned 0 — trying name-based fallback queries...")
            fb_a, fb_b = _build_fallback_queries(company["company_name"])
            try:
                results_a, results_b = await asyncio.gather(
                    _serp_search(fb_a, cfg.SERPAPI_KEY),
                    _serp_search(fb_b, cfg.SERPAPI_KEY),
                )
                print(f"  [serp fallback] {len(results_a)} results (A) + {len(results_b)} results (B)")
            except Exception as exc:
                print(f"  [serp fallback error] {exc}")
                results_a, results_b = [], []
                serp_errors += 1

        # Fallback 2: scrape the company's own website if name-based also empty
        scrape_results: list[dict] = []
        if _should_scrape_website(results_a, results_b):
            print(f"  [scrape] Both SerpAPI searches empty — scraping {company['website']}...")
            page_text = await _scrape_website(company["website"])
            if page_text:
                scrape_results = _scrape_to_results(company["website"], page_text)
                print(f"  [scrape] Got {len(scrape_results)} text chunks from website")
            else:
                print(f"  [scrape] Could not fetch website")

        search_text_parts = []
        if results_a or results_b:
            search_text_parts.append(
                "=== Search A (corporate/volume) ===\n" + _format_results(results_a) +
                "\n\n=== Search B (tech/multivisa/growth) ===\n" + _format_results(results_b)
            )
        if scrape_results:
            search_text_parts.append(
                "=== Website scrape (own site — highest reliability) ===\n"
                + _format_results(scrape_results)
            )
        search_text = "\n\n".join(search_text_parts).strip() or None

        enriched = {
            "row_index":      company["row_index"],
            "company_name":   company["company_name"],
            "search_results": search_text,
        }

        # Step 2: LLM analysis
        print(f"  [llm] Analysing signals...", end=" ", flush=True)
        result = await _llm_analyse_one(enriched, provider, model)
        if result is None:
            print("failed")
            llm_errors += 1
            continue
        print("done")

        # Step 3: write to sheet immediately
        try:
            written = await _write_company_signals(result, tool, dry_run)
            yes_signals = [s for s, v in written.items() if v]
            print(f"  [sheets] wrote — signals: {', '.join(yes_signals) if yes_signals else 'none'}")
        except Exception as exc:
            print(f"  [sheets error] {exc}")
            write_errors += 1

        all_results.append(result)

    # ── Summary ────────────────────────────────────────────────────────────
    print(f"\n[3/3] Summary")
    print("=" * 60)

    if serp_errors:
        print(f"  SerpAPI errors:  {serp_errors}")
    if llm_errors:
        print(f"  LLM errors:      {llm_errors}")
    if write_errors:
        print(f"  Write errors:    {write_errors}")

    print(f"\n  {'Company':<35} {'corp':>5} {'tech':>5} {'multi':>6} {'hvol':>5} {'grow':>5}")
    print(f"  {'-'*35} {'-'*5} {'-'*5} {'-'*6} {'-'*5} {'-'*5}")
    for r in all_results:
        row_idx = r.get("row_index")
        name    = next((c["company_name"] for c in companies if c["row_index"] == row_idx), "?")
        sigs    = r.get("signals", {})
        def yn(s): return "Yes" if sigs.get(s, {}).get("detected") else "No"
        print(f"  {name[:35]:<35} {yn('corporate'):>5} {yn('tech'):>5} {yn('multivisa'):>6} {yn('highvolume'):>5} {yn('growth'):>5}")

    processed = len(all_results)
    print(f"\n  Processed: {processed}/{total} companies")
    if dry_run:
        print("  (dry-run — nothing written to sheet)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Detect buying signals for immigration companies.")
    parser.add_argument("--tab",          choices=cfg.IMMIGRATION_TABS, default=cfg.DEFAULT_TAB)
    parser.add_argument("--skip-done",    action="store_true", help="Skip rows already scanned")
    parser.add_argument("--retry-empty",  action="store_true", help="Re-scan rows where all signals came back No")
    parser.add_argument("--dry-run",      action="store_true", help="Print results without writing to sheet")
    parser.add_argument("--min-rating",   type=int, default=8, metavar="N",
                        help="Only process companies with rating >= N (default: 8, 0 = no filter)")
    args = parser.parse_args()
    asyncio.run(main(args.tab, skip_done=args.skip_done, dry_run=args.dry_run, retry_empty=args.retry_empty, min_rating=args.min_rating))

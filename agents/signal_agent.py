#!/usr/bin/env python3
"""
Signal Detection Agent
=======================
For each company in the local JSON store, runs SerpAPI searches against their
website, then asks the LLM to detect each buying signal defined in the campaign.

Strategy
--------
  Phase 1 (SerpAPI): 2 site: searches per company using signal keywords
  Phase 2 (LLM):     single-company analysis — reads snippets, returns Yes/No + source
  Phase 3 (Store):   writes results to the local JSON store immediately

Usage
-----
  python agents/signal_agent.py --campaign hr-saas-ch --tab ProfServices
  python agents/signal_agent.py --campaign sales-tools-uk --tab UKSaaS --skip-done
  python agents/signal_agent.py --campaign hr-saas-ch --tab ProfServices --dry-run
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
from campaign import Campaign
from agents.provider import build_provider
from store import ResultStore
from tools.json_update_signal_tool import JsonUpdateSignalTool

import httpx

SERP_CACHE_DIR = PROJECT_ROOT / "serp_cache"


# ── LLM task prompt ────────────────────────────────────────────────────────────

def _build_task_prompt(signals: list[dict]) -> str:
    """Build the system prompt from campaign signal definitions."""
    signal_block = "\n\n".join(
        f"  {s['key']} — {s['name']}\n"
        f"    {s.get('llm_definition', s.get('description', ''))}\n"
        f"    Keywords: {', '.join(s.get('keywords', []))}"
        for s in signals
    )
    signal_names = ", ".join(s["key"] for s in signals)
    return f"""
You are a signal-detection analyst. You will receive a JSON array with one company that has
search results attached. Your job is to detect the following buying signals:

{signal_block}

Rules:
- Base your judgement ONLY on the provided search_results titles and snippets.
- Be conservative — only mark detected=true if the evidence is clear.
- For detected=true:  source must be the exact title or snippet excerpt containing the
  evidence, followed by the page URL in brackets.
- For detected=false: source must be "not found".
- If search_results is empty or null, mark all signals false with source "no website".

Return a JSON array (one object) with this exact shape:
[
  {{
    "row_index": <int>,
    "signals": {{
      {chr(10).join(f'      "{s["key"]}": {{"detected": true/false, "source": "..."}},' for s in signals)}
    }}
  }}
]

No other text — just the JSON array.
""".strip()


# ── Website scrape helpers ─────────────────────────────────────────────────────

def _extract_text_from_html(html: str) -> str:
    if not html:
        return ""
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html).strip()


async def _scrape_website(url: str) -> str:
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; SalesIntelBot/1.0)"}
        async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers=headers) as client:
            r = await client.get(url)
            r.raise_for_status()
            return _extract_text_from_html(r.text)[:8000]
    except Exception as exc:
        print(f"  [scrape] Failed to fetch {url}: {exc}")
        return ""


def _scrape_to_results(url: str, text: str) -> list[dict]:
    if not text:
        return []
    chunk_size = 500
    results = []
    for i in range(0, min(len(text), 4000), chunk_size):
        chunk = text[i:i + chunk_size].strip()
        if chunk:
            results.append({"title": f"[scraped] {url}", "snippet": chunk, "link": url})
    return results


# ── SerpAPI helpers ────────────────────────────────────────────────────────────

def _domain_from_url(url: str) -> str:
    url = url.strip().lower()
    for prefix in ("https://", "http://", "www."):
        url = url.removeprefix(prefix)
    return url.rstrip("/").split("/")[0]


async def _serp_search(query: str, api_key: str, gl: str = "gb", cr: str = "countryGB") -> list[dict]:
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

    params = {"q": query, "api_key": api_key, "num": 10, "engine": "google", "gl": gl, "cr": cr}
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
        lines.append(
            f"Title: {r.get('title', '')}\n"
            f"Snippet: {r.get('snippet', '')}\n"
            f"URL: {r.get('link', '')}"
        )
    return "\n\n".join(lines) if lines else ""


def _build_serp_queries(domain: str, signals: list[dict]) -> tuple[str, str]:
    """Build two site: search queries from campaign signal keywords."""
    all_keywords = []
    for sig in signals:
        all_keywords.extend(sig.get("keywords", []))

    # Split keywords roughly in half across two queries for coverage
    half = max(1, len(all_keywords) // 2)
    kw_a = all_keywords[:half]
    kw_b = all_keywords[half:] or all_keywords[:3]  # fallback if few keywords

    def _kw_clause(kws: list[str]) -> str:
        quoted = [f'"{k}"' if " " in k else k for k in kws[:8]]
        return " OR ".join(quoted)

    query_a = f"site:{domain} {_kw_clause(kw_a)}"
    query_b = f"site:{domain} {_kw_clause(kw_b)}"
    return query_a, query_b


def _build_fallback_queries(company_name: str, signals: list[dict]) -> tuple[str, str]:
    all_kw = []
    for sig in signals:
        all_kw.extend(sig.get("keywords", []))
    half = max(1, len(all_kw) // 2)

    def _kw_clause(kws: list[str]) -> str:
        quoted = [f'"{k}"' if " " in k else k for k in kws[:8]]
        return " OR ".join(quoted)

    name = company_name.strip('"')
    query_a = f'"{name}" {_kw_clause(all_kw[:half])}'
    query_b = f'"{name}" {_kw_clause(all_kw[half:] or all_kw[:3])}'
    return query_a, query_b


# ── JSON store helpers ─────────────────────────────────────────────────────────

def read_companies(
    store: ResultStore,
    skip_done: bool,
    min_rating: int = 0,
) -> list[dict]:
    """Return companies from the store that need signal detection."""
    rows = store.get_rows()
    companies = []
    for row in rows:
        website = (row.get("website") or "").strip()
        if not website:
            continue

        if skip_done and row.get("signals"):
            continue

        if min_rating > 0:
            try:
                if int(row.get("rating") or 0) < min_rating:
                    continue
            except (ValueError, TypeError):
                continue

        companies.append({
            "row_index":    row["row_index"],
            "company_name": row.get("name", ""),
            "website":      website,
        })
    return companies


# ── LLM analysis ───────────────────────────────────────────────────────────────

async def _llm_analyse_one(
    company: dict,
    provider,
    model: str,
    task_prompt: str,
) -> dict | None:
    prompt = (
        task_prompt
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


# ── Write results ──────────────────────────────────────────────────────────────

async def _write_company_signals(
    company_result: dict,
    tool: JsonUpdateSignalTool,
    signal_keys: list[str],
    dry_run: bool,
    only_signals: set[str] | None = None,
) -> dict[str, bool]:
    row_idx = company_result.get("row_index")
    signals = company_result.get("signals", {})
    written: dict[str, bool] = {}

    for key in signal_keys:
        if only_signals and key not in only_signals:
            continue
        sig      = signals.get(key, {})
        detected = bool(sig.get("detected", False))
        source   = sig.get("source", "not found") or "not found"
        written[key] = detected

        if dry_run:
            val = "Yes" if detected else "No"
            print(f"    [dry-run] {key:<20} = {val}  |  {source[:70]}")
        else:
            try:
                msg = await tool.execute(
                    row_index=row_idx,
                    signal=key,
                    detected=detected,
                    source=source,
                )
                print(f"    {msg}")
            except Exception as exc:
                print(f"    [error] Failed to write {key} for row {row_idx}: {exc}")

    return written


# ── Main ───────────────────────────────────────────────────────────────────────

async def main(
    campaign: Campaign,
    skip_done: bool = False,
    dry_run: bool = False,
    min_rating: int = 0,
    max_rows: int = 0,
    only_signals: set[str] | None = None,
) -> None:
    signals     = campaign.signals
    signal_keys = [s.key for s in signals]
    signal_defs = [s.model_dump() for s in signals]

    if not signals:
        print(f"[error] Campaign '{campaign.id}' has no signals defined.")
        sys.exit(1)

    if only_signals:
        invalid = only_signals - set(signal_keys)
        if invalid:
            print(f"[error] Unknown signal(s): {', '.join(sorted(invalid))}. "
                  f"Valid: {', '.join(sorted(signal_keys))}")
            sys.exit(1)

    store = ResultStore(campaign.id)
    serp_params = campaign.serp_params()
    gl = serp_params.get("gl", "gb")
    cr = serp_params.get("cr", "countryGB")

    task_prompt = _build_task_prompt(signal_defs)

    print("=" * 60)
    print(f"Signal detection — Campaign: {campaign.name}")
    print(f"Signals:    {', '.join(signal_keys)}")
    print(f"Skip done:  {skip_done}  |  Dry-run: {dry_run}")
    print("=" * 60)

    companies = read_companies(store, skip_done=skip_done, min_rating=min_rating)
    if max_rows and len(companies) > max_rows:
        print(f"Capping to {max_rows} rows (--max-rows)")
        companies = companies[:max_rows]

    total = len(companies)
    print(f"\n{total} companies to process\n")
    if not total:
        print("Nothing to do.")
        return

    provider, model = build_provider()
    print(f"Model: {model}\n")

    tool = JsonUpdateSignalTool(store=store, valid_signals=signal_keys)

    all_results:  list[dict] = []
    serp_errors = llm_errors = write_errors = 0

    for idx, company in enumerate(companies, 1):
        domain = _domain_from_url(company["website"])
        print(f"\n[{idx}/{total}] {company['company_name']}  (row {company['row_index']}, {domain})")

        # Phase 1: SerpAPI — 2 concurrent searches built from signal keywords
        query_a, query_b = _build_serp_queries(domain, signal_defs)
        try:
            results_a, results_b = await asyncio.gather(
                _serp_search(query_a, cfg.SERPAPI_KEY, gl=gl, cr=cr),
                _serp_search(query_b, cfg.SERPAPI_KEY, gl=gl, cr=cr),
            )
            print(f"  [serp] {len(results_a)} results (A) + {len(results_b)} results (B)")
        except Exception as exc:
            print(f"  [serp error] {exc}")
            results_a, results_b = [], []
            serp_errors += 1

        # Fallback 1: name-based queries if site: returned nothing
        if not results_a and not results_b:
            print("  [serp] site: returned 0 — trying name-based fallback…")
            fb_a, fb_b = _build_fallback_queries(company["company_name"], signal_defs)
            try:
                results_a, results_b = await asyncio.gather(
                    _serp_search(fb_a, cfg.SERPAPI_KEY, gl=gl, cr=cr),
                    _serp_search(fb_b, cfg.SERPAPI_KEY, gl=gl, cr=cr),
                )
                print(f"  [serp fallback] {len(results_a)} (A) + {len(results_b)} (B)")
            except Exception as exc:
                print(f"  [serp fallback error] {exc}")
                results_a, results_b = [], []
                serp_errors += 1

        # Fallback 2: scrape if both searches still empty
        scrape_results: list[dict] = []
        if not results_a and not results_b:
            print(f"  [scrape] Both SerpAPI searches empty — scraping {company['website']}…")
            page_text = await _scrape_website(company["website"])
            if page_text:
                scrape_results = _scrape_to_results(company["website"], page_text)
                print(f"  [scrape] Got {len(scrape_results)} text chunks")

        parts = []
        if results_a or results_b:
            parts.append(
                "=== Search A ===\n" + _format_results(results_a) +
                "\n\n=== Search B ===\n" + _format_results(results_b)
            )
        if scrape_results:
            parts.append("=== Website scrape ===\n" + _format_results(scrape_results))
        search_text = "\n\n".join(parts).strip() or None

        enriched = {
            "row_index":      company["row_index"],
            "company_name":   company["company_name"],
            "search_results": search_text,
        }

        # Phase 2: LLM analysis
        print("  [llm] Analysing signals…", end=" ", flush=True)
        result = await _llm_analyse_one(enriched, provider, model, task_prompt)
        if result is None:
            print("failed")
            llm_errors += 1
            continue
        print("done")

        # Phase 3: write to store immediately
        try:
            written = await _write_company_signals(
                result, tool, signal_keys, dry_run, only_signals=only_signals
            )
            yes_sigs = [s for s, v in written.items() if v]
            print(f"  [store] wrote — signals: {', '.join(yes_sigs) if yes_sigs else 'none'}")
        except Exception as exc:
            print(f"  [store error] {exc}")
            write_errors += 1

        all_results.append(result)

    # Summary
    print(f"\n{'=' * 60}")
    if serp_errors:
        print(f"  SerpAPI errors:  {serp_errors}")
    if llm_errors:
        print(f"  LLM errors:      {llm_errors}")
    if write_errors:
        print(f"  Write errors:    {write_errors}")

    col_w = max(len(k) for k in signal_keys) + 1
    header = f"  {'Company':<35} " + " ".join(f"{k[:col_w]:>{col_w}}" for k in signal_keys)
    print(f"\n{header}")
    print(f"  {'-'*35} " + " ".join("-" * col_w for _ in signal_keys))
    for r in all_results:
        row_idx = r.get("row_index")
        name    = next((c["company_name"] for c in companies if c["row_index"] == row_idx), "?")
        sigs    = r.get("signals", {})
        vals    = " ".join(
            f"{'Yes' if sigs.get(k, {}).get('detected') else 'No':>{col_w}}"
            for k in signal_keys
        )
        print(f"  {name[:35]:<35} {vals}")

    print(f"\n  Processed: {len(all_results)}/{total}")
    if dry_run:
        print("  (dry-run — nothing written to store)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Detect buying signals for companies.")
    parser.add_argument("--campaign",     required=True, help="Campaign ID")
    parser.add_argument("--skip-done",    action="store_true", help="Skip rows that already have signals")
    parser.add_argument("--dry-run",      action="store_true", help="Print results without writing")
    parser.add_argument("--min-rating",   type=int, default=0, metavar="N")
    parser.add_argument("--max-rows",     type=int, default=0, metavar="N")
    parser.add_argument("--only-signals", default=None, metavar="SIGNALS",
                        help="Comma-separated signal keys to (re)write, others untouched")
    args = parser.parse_args()

    campaign = Campaign.load(args.campaign)

    only_signals = None
    if args.only_signals:
        only_signals = {s.strip() for s in args.only_signals.split(",")}

    asyncio.run(main(
        campaign,
        skip_done=args.skip_done,
        dry_run=args.dry_run,
        min_rating=args.min_rating,
        max_rows=args.max_rows,
        only_signals=only_signals,
    ))

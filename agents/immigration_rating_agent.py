#!/usr/bin/env python3
"""
Immigration Lead Rating Agent
==============================
Scores each company 1–5 and writes the score to column C (Rating).

Strategy: rule-based scoring first (fast, free, consistent).
If signals are missing (not yet run) and only notes/size are available,
falls back to an LLM to estimate a provisional rating from the company
description alone. LLM ratings are marked with a "~" prefix (e.g. "~3")
so they can be identified and overwritten once signals are detected.

Score meanings
--------------
  5  Prime   — corporate immigration + high volume
  4  Strong  — strong signals, likely good fit
  3  Solid   — some evidence of fit
  2  Weak    — limited evidence
  1  Unknown — incomplete profile, no signals

Rule scoring factors
--------------------
  +3  CorporateImmigration signal = Yes  (sponsor licence / skilled worker / corporate)
  +3  HighVolume signal = Yes            (large team or many clients)
  +2  Specialist signal = Yes            (immigration is primary/sole practice area)
  +2  MultiVisa signal = Yes             (handles 3+ visa types)
  +1  Growth signal = Yes                (hiring / new office / expanding)
  +1  Size in sweet spot (1–200 staff)
  +1  Profile complete                   (website + linkedin + size all present)

Thresholds: ≥8 → 5  |  ≥5 → 4  |  ≥3 → 3  |  ≥1 → 2  |  0 → 1

LLM fallback triggers when ALL signal columns are empty (signals not yet run).
The LLM rates 1–5 based on notes + size + company name only, prefixed with "~".

Usage
-----
  python agents/immigration_rating_agent.py
  python agents/immigration_rating_agent.py --tab Advisors
  python agents/immigration_rating_agent.py --tab LawFirms --force
  python agents/immigration_rating_agent.py --llm   # also rate rows without signals (provisional ~N)
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config as cfg
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# ── Column indices (0-based, A:S = 19 cols) ────────────────────────────────
_COL_NAME        = 0   # A
_COL_RATING      = 2   # C
_COL_NOTES       = 3   # D
_COL_WEBSITE     = 4   # E
_COL_LINKEDIN    = 5   # F
_COL_SIZE        = 6   # G
_COL_CORPORATE   = 9   # J
_COL_SPECIALIST  = 11  # L
_COL_MULTIVISA   = 13  # N
_COL_HIGHVOLUME  = 15  # P
_COL_GROWTH      = 17  # R

_SIGNAL_COLS = [_COL_CORPORATE, _COL_SPECIALIST, _COL_MULTIVISA, _COL_HIGHVOLUME, _COL_GROWTH]

_SWEET_SPOT_SIZES = {
    "1-10", "2-10", "5-10",
    "11-50", "10-50",
    "51-100", "51-200",
}

# Minimum rule-based points needed to skip LLM fallback
_LLM_FALLBACK_THRESHOLD = 1


def _cell(row: list[str], idx: int) -> str:
    return row[idx].strip().lower() if idx < len(row) else ""


def _is_yes(row: list[str], col: int) -> bool:
    return _cell(row, col) == "yes"


def _signals_present(row: list[str]) -> bool:
    """True if at least one signal column has been filled (Yes or No)."""
    return any(_cell(row, c) in ("yes", "no") for c in _SIGNAL_COLS)


def _score(row: list[str]) -> int:
    pts = 0
    if _is_yes(row, _COL_CORPORATE):  pts += 3
    if _is_yes(row, _COL_HIGHVOLUME): pts += 3
    if _is_yes(row, _COL_SPECIALIST):  pts += 2
    if _is_yes(row, _COL_MULTIVISA):  pts += 2
    if _is_yes(row, _COL_GROWTH):     pts += 1
    if _cell(row, _COL_SIZE) in _SWEET_SPOT_SIZES: pts += 1
    if _cell(row, _COL_WEBSITE) and _cell(row, _COL_LINKEDIN) and _cell(row, _COL_SIZE):
        pts += 1
    return pts


def _rating(pts: int) -> int:
    """Map raw score points to a 1–10 rating.
    Max possible score is 13 (all signals + sweet spot size + complete profile).
    Scale: each point ≈ 0.77 rating steps, clamped 1–10.
    """
    if pts == 0: return 1
    return min(10, max(1, pts + 1))


# ── LLM fallback ───────────────────────────────────────────────────────────

_LLM_PROMPT = """\
You are a sales prioritisation assistant for LawFairy, a legaltech company
selling immigration case management software to UK law firms.

Rate the following company as a sales prospect on a scale of 1–10:
  9-10 = Prime    — corporate immigration focus, high volume, sponsor licence work
  7-8  = Strong   — clear immigration practice, likely handles business visa types
  5-6  = Solid    — general immigration firm, mixed visa types
  3-4  = Weak     — small or niche, limited signals
  1-2  = Unknown  — insufficient information

Company data:
{company_data}

Reply with a single JSON object: {{"rating": <1-5>, "reason": "<one sentence>"}}
Do not include any other text.
"""


async def _llm_rate_batch(
    rows_with_idx: list[tuple[int, list[str]]],
    provider,
    model: str,
) -> dict[int, tuple[int, str]]:
    """Ask the LLM to rate a batch of companies. Returns {row_idx: (rating, reason)}."""
    items = []
    for row_idx, row in rows_with_idx:
        items.append({
            "row_idx": row_idx,
            "company_name": row[_COL_NAME] if _COL_NAME < len(row) else "",
            "notes": row[_COL_NOTES] if _COL_NOTES < len(row) else "",
            "size": row[_COL_SIZE] if _COL_SIZE < len(row) else "",
            "website": row[_COL_WEBSITE] if _COL_WEBSITE < len(row) else "",
        })

    # Process in batches of 20 to avoid context overflow
    results: dict[int, tuple[int, str]] = {}
    batch_size = 20
    for i in range(0, len(items), batch_size):
        batch = items[i:i + batch_size]
        company_data = json.dumps(batch, ensure_ascii=False, indent=2)
        prompt = (
            "Rate each company in the following JSON array as a sales prospect for "
            "immigration case management software. For each item, return its row_idx "
            "and a rating 1–5.\n\n"
            "Scale:\n"
            "  9-10 = Prime    — corporate immigration, sponsor licence, high volume\n"
            "  7-8  = Strong   — clear immigration practice, business visa types\n"
            "  5-6  = Solid    — general immigration firm\n"
            "  3-4  = Weak     — small, niche, or limited information\n"
            "  1-2  = Unknown  — no useful information\n\n"
            f"Companies:\n{company_data}\n\n"
            "Reply with a JSON array: "
            '[{"row_idx": <int>, "rating": <1-5>, "reason": "<one sentence>"}, ...]'
            "\nNo other text."
        )
        try:
            response = await provider.chat(
                messages=[{"role": "user", "content": prompt}],
                model=model,
                max_tokens=8192,
                temperature=0.1,
            )
            text = response.content or ""
            # Strip markdown code fences if present
            text = text.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            parsed = json.loads(text.strip())
            for entry in parsed:
                row_idx = entry.get("row_idx")
                rating  = int(entry.get("rating", 1))
                reason  = entry.get("reason", "")
                if row_idx is not None and 1 <= rating <= 5:
                    results[row_idx] = (rating, reason)
        except Exception as exc:
            print(f"[rating] LLM batch failed: {exc}")

    return results


_RATING_TABS = {"LawFirms", "Advisors", "Charities"}


async def run_async(tab: str, force: bool = False, use_llm: bool = True) -> None:
    if tab not in _RATING_TABS:
        print(f"[rating] Tab '{tab}' does not support ratings (partners-only tab). Skipping.")
        return
    credentials_file = str(PROJECT_ROOT / cfg.CREDENTIALS_FILE)
    creds = Credentials.from_service_account_file(credentials_file, scopes=_SCOPES)
    service = build("sheets", "v4", credentials=creds)

    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=cfg.SPREADSHEET_ID, range=f"{tab}!A:S")
        .execute()
    )
    rows = result.get("values", [])
    if len(rows) <= 1:
        print(f"[rating] No data rows in '{tab}' tab.")
        return

    rule_updates:     list[dict] = []
    llm_candidates:   list[tuple[int, list[str]]] = []
    skipped = 0

    for i, row in enumerate(rows[1:], start=2):
        existing = row[_COL_RATING].strip() if _COL_RATING < len(row) else ""
        # Skip rows with a confirmed rating (no "~") unless --force
        if existing and not existing.startswith("~") and not force:
            skipped += 1
            continue

        if _signals_present(row):
            # Rule-based: signals available → deterministic score
            pts = _score(row)
            rule_updates.append({"range": f"{tab}!C{i}", "values": [[str(_rating(pts))]]})
        else:
            # No signals yet — queue for LLM fallback if enabled
            if use_llm:
                llm_candidates.append((i, row))
            else:
                # Without LLM, assign a provisional rule score from whatever we have
                pts = _score(row)
                rule_updates.append({"range": f"{tab}!C{i}", "values": [[str(_rating(pts))]]})

    # Write rule-based ratings immediately
    if rule_updates:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=cfg.SPREADSHEET_ID,
            body={"valueInputOption": "USER_ENTERED", "data": rule_updates},
        ).execute()
        print(f"[rating] Rule-based: rated {len(rule_updates)} rows.")

    # LLM fallback for rows without signals
    if llm_candidates:
        print(f"[rating] LLM fallback: {len(llm_candidates)} rows without signals...")
        from agents.provider import build_provider
        provider, model = build_provider()

        llm_results = await _llm_rate_batch(llm_candidates, provider, model)

        llm_updates = []
        for row_idx, (rating, reason) in llm_results.items():
            llm_updates.append({
                "range": f"{tab}!C{row_idx}",
                "values": [[f"~{rating}"]],  # "~" marks provisional LLM rating
            })

        if llm_updates:
            service.spreadsheets().values().batchUpdate(
                spreadsheetId=cfg.SPREADSHEET_ID,
                body={"valueInputOption": "USER_ENTERED", "data": llm_updates},
            ).execute()
            print(f"[rating] LLM fallback: rated {len(llm_updates)} rows (marked with ~).")

        unrated = len(llm_candidates) - len(llm_results)
        if unrated:
            print(f"[rating] {unrated} rows could not be rated by LLM (no notes/name).")

    if skipped:
        print(f"[rating] Skipped {skipped} already-rated rows. Use --force to re-rate.")


def run(tab: str, force: bool = False, use_llm: bool = False) -> None:
    asyncio.run(run_async(tab, force=force, use_llm=use_llm))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rate immigration leads 1–10.")
    parser.add_argument(
        "--tab",
        choices=cfg.IMMIGRATION_TABS,
        default=cfg.DEFAULT_TAB,
        help=f"Sheet tab to rate (default: {cfg.DEFAULT_TAB})",
    )
    parser.add_argument("--force", action="store_true", help="Re-rate all rows including confirmed ratings")
    parser.add_argument("--llm",   action="store_true", help="Enable LLM fallback for rows without signals (provisional ~N rating)")
    args = parser.parse_args()
    run(args.tab, force=args.force, use_llm=args.llm)

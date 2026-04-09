# Immigration Finder — UK Immigration Lead Intelligence

Automatically builds a ranked list of UK immigration companies so LawFairy knows who to contact first and who to approach as a channel partner.

---

## Overview

### What this produces

A Google Sheet with four lists of UK immigration companies, each row scored 1–10 so you can sort by best prospect and start at the top.

**Direct sales targets** (rated 1–10):

| List | Who's on it |
|------|------------|
| LawFirms | Immigration solicitors regulated by the SRA |
| Advisors | OISC-regulated advisers and corporate immigration consultants |
| Charities | NGOs providing immigration advice — lower commercial priority but worth outreach for awareness |

**Channel partners** (rated separately):

| List | Who's on it |
|------|------------|
| LegaltechBrokers | Consultants, resellers, and integration partners (e.g. BamLegal, 3kites) who advise law firms on buying software — they would refer or resell LawFairy, not buy directly |

### How companies are scored

Each company is automatically checked for five buying signals. The more signals detected, the higher the rating.

| Signal | What it means for sales |
|--------|------------------------|
| **Corporate** | Does employer/sponsor licence work — the core use case for LawFairy |
| **Specialist** | Immigration is the firm's primary or sole practice area (not one department in a general firm) |
| **MultiVisa** | Handles many visa types — more complexity, more to gain from case management |
| **HighVolume** | Large team or high caseload — more cases = more pain without good software |
| **Growth** | Hiring, opening offices, expanding — actively investing in the business |

**Rating guide:**

| Rating | What it means |
|--------|--------------|
| 8–10 | Prime — contact first, strong fit for LawFairy |
| 6–7 | Strong — worth a call, clear immigration practice |
| 4–5 | Solid — general immigration work, lower urgency |
| 2–3 | Weak — small, niche, or limited information |
| 1 | Unknown — not enough data yet |

### Sheet columns

| Column | What it shows |
|--------|--------------|
| A | Company name |
| B | LawFairy internal comment |
| C | Rating 1–10 |
| D | Description / notes |
| E | Website |
| F | LinkedIn |
| G | Company size |
| H | HQ location |
| I | Date added |
| J–K | Corporate signal (Yes/No) + evidence URL |
| L–M | Specialist signal + evidence URL |
| N–O | MultiVisa signal + evidence URL |
| P–Q | HighVolume signal + evidence URL |
| R–S | Growth signal + evidence URL |
| T | Contacts — found decision-makers, one per line: `First Last \| Role \| linkedin_url` |

---

## How it works

Five automated steps build and maintain the sheet:

**1. Find companies**
Runs dozens of targeted Google searches (by city, visa type, professional body, LinkedIn) and adds new companies to the sheet. Deduplicates across all tabs so the same company never appears twice.

**2. Enrich missing data**
For companies added without a full profile (no notes), fires SerpAPI searches in parallel and uses an LLM to fill in the missing website, LinkedIn, company size, HQ location, and description. Handles rows added manually by a human — even a company name alone is enough to start enrichment.

**3. Detect buying signals**
For each company, collects evidence from three sources in order:
1. `site:<domain>` SerpAPI search — precise, comes directly from the firm's own site
2. Name-based SerpAPI search — fallback for large firms whose domains block site: searches (e.g. Fragomen)
3. Direct HTTP scrape of the company's homepage — last resort when both searches return nothing

An LLM then reads the collected text and decides Yes/No for each of the five signals, with a source URL for manual verification. LegaltechBrokers is skipped — signals are not relevant for partners.

**4. Rate each company 1–10**
Rule-based scoring from detected signals — no LLM needed. Rows without signals are skipped (run signal detection first).

| Points | Source |
|--------|--------|
| +3 | Corporate signal = Yes |
| +3 | HighVolume signal = Yes |
| +2 | Specialist signal = Yes |
| +2 | MultiVisa signal = Yes |
| +1 | Growth signal = Yes |
| +1 | Size in sweet spot (1–200 staff) |
| +1 | Complete profile (website + LinkedIn + size all present) |

Score → rating: 0pts=1, 1pt=2, … 9+pts=10 (capped). LegaltechBrokers is skipped.

**5. Find contact people**
For each rated company (default: rating ≥ 8), searches LinkedIn (via SerpAPI) for the Managing Partner, Head of Immigration, Partner, or Director and outputs a Waalaxy-compatible CSV for outreach. Role priorities are tuned per tab — LawFirms targets senior partners and immigration directors; Charities targets CEOs and service heads; LegaltechBrokers targets managing directors and consultants.

Contacts are also written back to column T of the sheet as `First Last | Role | linkedin_url` (one per line).

If SerpAPI finds fewer than the threshold (default: 2) profiles for a company, it automatically falls back to querying LinkedIn's Voyager API using browser session cookies (`LINKEDIN_LI_AT` + `LINKEDIN_JSESSIONID` in `.env`).

---

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Place `melt2.json` (Google service account key) in the project root. Share the spreadsheet with the service account email (Editor access).

Configure `config.py`:
```python
SPREADSHEET_ID   = "<your-spreadsheet-id>"
SERPAPI_KEY      = "<your-serpapi-key>"
CREDENTIALS_FILE = "melt2.json"
VERTEX_PROJECT   = "<your-gcp-project>"
```

For the LinkedIn contact fallback, add to `.env`:
```
LINKEDIN_LI_AT=<your li_at cookie value>
LINKEDIN_JSESSIONID=<your JSESSIONID cookie value>
```
Get these from browser DevTools → Application → Cookies while logged into linkedin.com.

**Important:** always run with `PYTHONPATH=""` to avoid the system `typing_extensions` shadowing the venv's version:
```bash
PYTHONPATH="" ./venv/bin/python agents/immigration_search_agent.py --tab LawFirms
```

### Set up / reset sheet formatting

```bash
PYTHONPATH="" ./venv/bin/python immigration_sheets_setup.py              # format all tabs
PYTHONPATH="" ./venv/bin/python immigration_sheets_setup.py --tab LawFirms  # one tab only
```

---

## Running

Run each step for each tab. Steps 3 and 4 skip LegaltechBrokers automatically.

```bash
# 1. Find companies
PYTHONPATH="" ./venv/bin/python agents/immigration_search_agent.py --tab LawFirms
PYTHONPATH="" ./venv/bin/python agents/immigration_search_agent.py --tab Advisors
PYTHONPATH="" ./venv/bin/python agents/immigration_search_agent.py --tab Charities
PYTHONPATH="" ./venv/bin/python agents/immigration_search_agent.py --tab LegaltechBrokers

# 2. Enrich missing data (fills website, LinkedIn, size, notes for incomplete rows)
PYTHONPATH="" ./venv/bin/python agents/immigration_enrich_agent.py --tab LawFirms
PYTHONPATH="" ./venv/bin/python agents/immigration_enrich_agent.py --tab LawFirms --min-rating 8  # only high-value rows
PYTHONPATH="" ./venv/bin/python agents/immigration_enrich_agent.py --tab LawFirms --max-rows 10   # process subset

# 3. Detect signals (skips LegaltechBrokers automatically)
PYTHONPATH="" ./venv/bin/python agents/signal_agent.py --tab LawFirms
PYTHONPATH="" ./venv/bin/python agents/signal_agent.py --tab LawFirms --skip-done      # skip already-scanned rows
PYTHONPATH="" ./venv/bin/python agents/signal_agent.py --tab LawFirms --retry-empty    # re-scan rows with no signals found
PYTHONPATH="" ./venv/bin/python agents/signal_agent.py --tab LawFirms --dry-run        # preview without writing
PYTHONPATH="" ./venv/bin/python agents/signal_agent.py --tab LawFirms --min-rating 8   # only high-value rows
PYTHONPATH="" ./venv/bin/python agents/signal_agent.py --tab LawFirms --max-rows 10    # cap batch size
PYTHONPATH="" ./venv/bin/python agents/signal_agent.py --tab LawFirms --only-signals specialist  # rewrite one signal only

# 4. Rate companies (skips LegaltechBrokers automatically; run after signals)
PYTHONPATH="" ./venv/bin/python agents/immigration_rating_agent.py --tab LawFirms
PYTHONPATH="" ./venv/bin/python agents/immigration_rating_agent.py --tab LawFirms --force   # re-rate all rows
PYTHONPATH="" ./venv/bin/python agents/immigration_rating_agent.py --tab LawFirms --llm     # also estimate rows without signals

# 5. Find contacts → Waalaxy-ready CSV + writes to sheet column T
PYTHONPATH="" ./venv/bin/python agents/immigration_contact_agent.py --tab LawFirms --output contacts_lawfirms.csv
PYTHONPATH="" ./venv/bin/python agents/immigration_contact_agent.py --tab Advisors --output contacts_advisors.csv
PYTHONPATH="" ./venv/bin/python agents/immigration_contact_agent.py --tab LawFirms --min-rating 8    # high-quality only (default)
PYTHONPATH="" ./venv/bin/python agents/immigration_contact_agent.py --tab LawFirms --max-profiles 2 --limit 50
PYTHONPATH="" ./venv/bin/python agents/immigration_contact_agent.py --tab LawFirms --dry-run         # preview only
PYTHONPATH="" ./venv/bin/python agents/immigration_contact_agent.py --tab LawFirms --no-sheet-write  # CSV only, skip col T
PYTHONPATH="" ./venv/bin/python agents/immigration_contact_agent.py --tab LawFirms --fallback-threshold 2  # LinkedIn fallback if <2 profiles
PYTHONPATH="" ./venv/bin/python agents/immigration_contact_agent.py --tab LawFirms --force-linkedin-fallback  # attempt fallback regardless
```

---

## Testing

```bash
PYTHONPATH="" ./venv/bin/python -m pytest tests/ -v
```

All unit/e2e tests mock Google Sheets and SerpAPI — no real API calls made.

Integration tests (real SerpAPI calls, skipped by default):
```bash
PYTEST_RUN_INTEGRATION=1 PYTHONPATH="" ./venv/bin/python -m pytest tests/test_integration_serp.py -v
```

---

## Project structure

```
immigrationfinder/
├── agents/
│   ├── immigration_search_agent.py   # Step 1 — discover UK immigration companies
│   ├── immigration_enrich_agent.py   # Step 2 — fill missing info (website, LinkedIn, size, notes)
│   ├── signal_agent.py               # Step 3 — detect buying signals (SerpAPI + scrape + LLM)
│   ├── immigration_rating_agent.py   # Step 4 — score 1–10 (rule-based; --llm for provisional estimates)
│   ├── immigration_contact_agent.py  # Step 5 — find decision-maker contacts → CSV + sheet col T
│   └── provider.py                   # LLM provider (Vertex AI)
│
├── tools/
│   ├── serp_tool.py                  # SerpAPI search with disk caching
│   ├── sheets_tool.py                # Append new company rows (with cross-tab dedup)
│   ├── sheets_read_tool.py           # Read company rows
│   ├── sheets_update_info_tool.py    # Write enriched info fields (website, LinkedIn, size, etc.)
│   └── sheets_update_signal_tool.py  # Write signal results to sheet
│
├── tests/
│   ├── test_immigration_search_agent.py
│   ├── test_immigration_enrich_agent.py
│   ├── test_immigration_contact_agent.py
│   ├── test_sheets_append_tool.py
│   ├── test_sheets_update_signal_tool.py
│   ├── test_sheets_update_tool.py
│   ├── test_e2e_real_companies.py    # Mocked e2e tests for all 4 tabs
│   └── test_integration_serp.py      # Real SerpAPI integration tests (opt-in)
│
├── immigration_sheets_setup.py       # Create/reformat sheet tabs with styling
├── config.py
└── melt2.json                        # Google service account key (not in repo)
```

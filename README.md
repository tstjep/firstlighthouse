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
| **Tech** | Already has a client portal or online case tracking — they understand the value of software |
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

Ratings marked `~N` (e.g. `~6`) are provisional estimates made before the full signal scan — treat as indicative only.

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
| L–M | Tech signal + evidence URL |
| N–O | MultiVisa signal + evidence URL |
| P–Q | HighVolume signal + evidence URL |
| R–S | Growth signal + evidence URL |

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
Rule-based scoring from detected signals — no LLM needed once signals are present. If signals haven't been run yet, an LLM estimates a provisional rating from the company description alone (marked `~N`).

| Points | Source |
|--------|--------|
| +3 | Corporate signal = Yes |
| +3 | HighVolume signal = Yes |
| +2 | Tech signal = Yes |
| +2 | MultiVisa signal = Yes |
| +1 | Growth signal = Yes |
| +1 | Size in sweet spot (1–200 staff) |
| +1 | Complete profile (website + LinkedIn + size all present) |

Score → rating: 0pts=1, 1pt=2, … 9+pts=10 (capped). LegaltechBrokers is skipped.

**5. Find contact people**
For each rated company, searches LinkedIn (via SerpAPI) for the Managing Partner, Head of Immigration, Partner, or Director and outputs a Waalaxy-compatible CSV for outreach. Role priorities are tuned per tab — LawFirms targets senior partners and immigration directors; Charities targets CEOs and service heads; LegaltechBrokers targets managing directors and consultants.

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

### Set up / reset sheet formatting

```bash
python immigration_sheets_setup.py              # format all tabs
python immigration_sheets_setup.py --tab LawFirms  # one tab only
```

---

## Running

Run each step for each tab. Steps 3 and 4 skip LegaltechBrokers automatically.

```bash
# 1. Find companies
venv/bin/python agents/immigration_search_agent.py --tab LawFirms
venv/bin/python agents/immigration_search_agent.py --tab Advisors
venv/bin/python agents/immigration_search_agent.py --tab Charities
venv/bin/python agents/immigration_search_agent.py --tab LegaltechBrokers

# 2. Enrich missing data (fills website, LinkedIn, size, notes for incomplete rows)
venv/bin/python agents/immigration_enrich_agent.py --tab LawFirms
venv/bin/python agents/immigration_enrich_agent.py --tab Advisors
venv/bin/python agents/immigration_enrich_agent.py --tab Charities
venv/bin/python agents/immigration_enrich_agent.py --tab LegaltechBrokers
venv/bin/python agents/immigration_enrich_agent.py --tab LawFirms --max-rows 10  # process subset

# 3. Detect signals (skips LegaltechBrokers automatically)
venv/bin/python agents/signal_agent.py --tab LawFirms
venv/bin/python agents/signal_agent.py --tab LawFirms --skip-done     # skip already-scanned rows
venv/bin/python agents/signal_agent.py --tab LawFirms --retry-empty   # re-scan rows with no signals found
venv/bin/python agents/signal_agent.py --tab LawFirms --dry-run       # preview without writing

# 4. Rate companies (skips LegaltechBrokers automatically)
venv/bin/python agents/immigration_rating_agent.py --tab LawFirms
venv/bin/python agents/immigration_rating_agent.py --tab LawFirms --force    # re-rate all rows
venv/bin/python agents/immigration_rating_agent.py --tab LawFirms --no-llm  # rule-based only

# 5. Find contacts → Waalaxy-ready CSV
venv/bin/python agents/immigration_contact_agent.py --tab LawFirms --output contacts_lawfirms.csv
venv/bin/python agents/immigration_contact_agent.py --tab Advisors --output contacts_advisors.csv
venv/bin/python agents/immigration_contact_agent.py --tab LawFirms --min-rating 7   # high-quality only
venv/bin/python agents/immigration_contact_agent.py --tab LawFirms --max-profiles 2 --limit 50
venv/bin/python agents/immigration_contact_agent.py --tab LawFirms --dry-run        # preview only
```

---

## Testing

```bash
venv/bin/python -m pytest tests/ -v
```

All tests mock Google Sheets and SerpAPI — no real API calls made.

---

## Project structure

```
immigrationfinder/
├── agents/
│   ├── immigration_search_agent.py   # Step 1 — discover UK immigration companies
│   ├── immigration_enrich_agent.py   # Step 2 — fill missing info (website, LinkedIn, size, notes)
│   ├── signal_agent.py               # Step 3 — detect buying signals (SerpAPI + scrape + LLM)
│   ├── immigration_rating_agent.py   # Step 4 — score 1–10 (rule-based + LLM fallback)
│   ├── immigration_contact_agent.py  # Step 5 — find decision-maker contacts → CSV for Waalaxy
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
│   └── test_sheets_update_tool.py
│
├── immigration_sheets_setup.py       # Create/reformat sheet tabs with styling
├── config.py
└── melt2.json                        # Google service account key (not in repo)
```

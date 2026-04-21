# salesintel

Automatically builds ranked lists of target companies in any market — so you know exactly who to call, email, or interview first.

Built on **AI agents** ([nanobot](https://github.com/nanobot-ai/nanobot)): each step is an autonomous agent that runs searches, reasons over results, and decides what to write — so it handles ambiguous company data, missing websites, and partial information the way a researcher would, not with brittle rules.

Works for any stage of go-to-market:

- **0→1 sales** — startups who need to find and prioritise their first paying customers without a dedicated sales team or expensive data tools
- **User research** — founders and PMs who need a pool of warm contacts to recruit for discovery interviews
- **Outbound at scale** — growth teams building segmented, signal-qualified prospect lists for sequenced outreach (exports to Waalaxy, Lemlist, or any CSV-based outreach tool)

Configure your ICP, region, and target segments through the **web UI**. Salesintel handles the searching, enriching, signal detection, scoring, and contact finding — and writes everything to a Google Sheet your team can work from directly.

---

## Overview

### What this produces

A Google Sheet with one tab per target segment (e.g. `LawFirms`, `Advisors`, `Charities`), each row scored 1–10 so you can sort by best prospect and work top-down.

### How companies are scored

Each company is automatically checked for the **buying signals** you define. The more signals detected, the higher the rating.

Example signals for a UK immigration campaign (selling case management software to law firms):

| Signal | What it detects |
|--------|----------------|
| **Corporate** | Handles employer/sponsor licence work — core use case |
| **Specialist** | Immigration is the firm's primary practice area |
| **MultiVisa** | Handles many visa types — more complexity, more value |
| **HighVolume** | Large team or high caseload — more cases = more pain |
| **Growth** | Hiring, opening offices, expanding |

Signals are fully configurable per campaign through the UI. You can also define **negative signals** to automatically down-rank or exclude companies that don't fit — see [Excluding companies](#excluding-companies) below.

### Sheet columns

| Column | What it shows |
|--------|--------------|
| A | Company name |
| B | Internal comment |
| C | Rating 1–10 |
| D | Notes / description |
| E | Website |
| F | LinkedIn |
| G | Company size |
| H | HQ location |
| I | Date added |
| J–… | Signal columns (Yes/No + evidence URL, one pair per signal) |
| Last | Contacts — decision-makers: `First Last \| Role \| linkedin_url` |

---

## How it works

Five automated steps:

**1. Find companies** (`search_agent.py`)
Runs targeted searches (SerpAPI) and adds new companies to the sheet. Deduplicates across all tabs.

**2. Enrich missing data** (`enrich_agent.py`)
For rows with no profile, fires SerpAPI searches and uses an LLM to fill in website, LinkedIn, size, HQ, and description. Works on manually-added rows too — a company name alone is enough.

**3. Detect buying signals** (`signal_agent.py`)
For each company, collects evidence from three sources in order:
1. `site:<domain>` SerpAPI search — precise, from the firm's own site
2. Name-based SerpAPI search — fallback for firms whose domains block site: searches
3. Direct HTTP scrape of the homepage — last resort

An LLM reads the collected text and decides Yes/No for each signal, with a source URL for verification.

**4. Rate each company 1–10** (`rating_agent.py`)
Rule-based scoring from detected signals — no LLM needed. Signal points are defined in the campaign config.

**5. Find contacts** (`contact_agent.py`)
For each rated company (default: rating ≥ 8), searches LinkedIn (via SerpAPI) for decision-makers in roles you define per segment. Falls back to LinkedIn's Voyager API using browser session cookies. Contacts are written back to the sheet and exported as a CSV ready for import into outreach tools like **Waalaxy** or **Lemlist**.

---

## Campaigns

Each campaign defines a market: who you're targeting, where they are, what signals matter, and what contacts to find. You configure this through the **web UI** — salesintel persists it as a JSON file in `campaigns/` that the agents read at runtime.

Start the UI:
```bash
cd frontend && npm run dev
# open http://localhost:5173
```

The UI lets you set:
- **ICP** — a plain-English description of your product and target audience, injected into LLM prompts
- **Region** — country, TLD filter (e.g. `.co.uk`), and SerpAPI geolocation params
- **Segments** — one per Google Sheet tab, each with its own search queries, contact roles, and signal rules
- **Signals** — what to detect, with keywords, LLM definitions, and point values for scoring
- **LinkedIn** — your session cookies for the contact-finding fallback

---

## Excluding companies

Use a **negative signal** to identify companies that are a poor fit, then configure the rating rules to penalise them.

**Example: exclude general law firms that only do immigration as a side practice**

In the UI, add a signal named `GeneralPractice` with a negative point value (`-3`) and a definition like:

> Mark Yes if the firm lists unrelated practice areas alongside immigration — e.g. conveyancing, family law, criminal defence, personal injury, employment, wills, or commercial litigation. Mark No if immigration is clearly their primary or sole focus.

Any firm where `GeneralPractice = Yes` loses 3 points from their score, pushing them below the contact threshold.

**Example: exclude very large firms that already use enterprise software**

Add a signal `EnterpriseSize` that marks Yes when the company has 500+ staff or is part of an international network. Assign `-2` points. Large firms may be too slow to buy, or already locked into a platform.

**Example: exclude charities that only offer free legal aid (no commercial relationship possible)**

Add a signal `LegalAidOnly` that marks Yes when the company mentions "legal aid", "pro bono", or "free advice" as their primary offering — without any paid services listed. Assign `-5` to effectively filter them out of outreach.

Negative signals appear as their own columns in the sheet so you can see the reasoning, and they're factored into the 1–10 score automatically.

---

## Setup

### Python

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Place `melt2.json` (Google service account key) in the project root. Share the spreadsheet with the service account email (Editor access).

For the LinkedIn contact fallback, add to `.env`:
```
LINKEDIN_LI_AT=<your li_at cookie>
LINKEDIN_JSESSIONID=<your JSESSIONID cookie>
```
Get these from browser DevTools → Application → Cookies while logged into linkedin.com.

**Always run with `PYTHONPATH=""`** to avoid the system `typing_extensions` shadowing the venv:
```bash
PYTHONPATH="" ./venv/bin/python agents/search_agent.py --campaign immigration-uk --tab LawFirms
```

### Frontend (campaign editor)

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173` to create and edit campaign configs.

### API

```bash
PYTHONPATH="" ./venv/bin/python -m uvicorn api.main:app --reload
```

---

## Running

All agents accept `--campaign <id>` (default: `immigration-uk`) and `--tab <name>`.

```bash
# 1. Find companies
PYTHONPATH="" ./venv/bin/python agents/search_agent.py --campaign immigration-uk --tab LawFirms
PYTHONPATH="" ./venv/bin/python agents/search_agent.py --campaign immigration-uk --tab Advisors

# 2. Enrich missing data
PYTHONPATH="" ./venv/bin/python agents/enrich_agent.py --campaign immigration-uk --tab LawFirms
PYTHONPATH="" ./venv/bin/python agents/enrich_agent.py --campaign immigration-uk --tab LawFirms --min-rating 8
PYTHONPATH="" ./venv/bin/python agents/enrich_agent.py --campaign immigration-uk --tab LawFirms --max-rows 10

# 3. Detect signals (skips segments with signals_enabled: false)
PYTHONPATH="" ./venv/bin/python agents/signal_agent.py --campaign immigration-uk --tab LawFirms
PYTHONPATH="" ./venv/bin/python agents/signal_agent.py --campaign immigration-uk --tab LawFirms --skip-done
PYTHONPATH="" ./venv/bin/python agents/signal_agent.py --campaign immigration-uk --tab LawFirms --retry-empty
PYTHONPATH="" ./venv/bin/python agents/signal_agent.py --campaign immigration-uk --tab LawFirms --dry-run
PYTHONPATH="" ./venv/bin/python agents/signal_agent.py --campaign immigration-uk --tab LawFirms --min-rating 8
PYTHONPATH="" ./venv/bin/python agents/signal_agent.py --campaign immigration-uk --tab LawFirms --only-signals corporate

# 4. Rate companies (skips segments with rating_enabled: false)
PYTHONPATH="" ./venv/bin/python agents/rating_agent.py --campaign immigration-uk --tab LawFirms
PYTHONPATH="" ./venv/bin/python agents/rating_agent.py --campaign immigration-uk --tab LawFirms --force
PYTHONPATH="" ./venv/bin/python agents/rating_agent.py --campaign immigration-uk --tab LawFirms --llm

# 5. Find contacts → Waalaxy-ready CSV + writes to sheet
PYTHONPATH="" ./venv/bin/python agents/contact_agent.py --campaign immigration-uk --tab LawFirms --output contacts_lawfirms.csv
PYTHONPATH="" ./venv/bin/python agents/contact_agent.py --campaign immigration-uk --tab LawFirms --min-rating 8
PYTHONPATH="" ./venv/bin/python agents/contact_agent.py --campaign immigration-uk --tab LawFirms --max-profiles 2 --limit 50
PYTHONPATH="" ./venv/bin/python agents/contact_agent.py --campaign immigration-uk --tab LawFirms --dry-run
PYTHONPATH="" ./venv/bin/python agents/contact_agent.py --campaign immigration-uk --tab LawFirms --no-sheet-write
PYTHONPATH="" ./venv/bin/python agents/contact_agent.py --campaign immigration-uk --tab LawFirms --force-linkedin-fallback
```

### Set up / reset sheet formatting

```bash
PYTHONPATH="" ./venv/bin/python sheets_setup.py --campaign immigration-uk
PYTHONPATH="" ./venv/bin/python sheets_setup.py --campaign immigration-uk --tab LawFirms
```

---

## Testing

```bash
PYTHONPATH="" ./venv/bin/python -m pytest tests/ -v
```

Integration tests (real SerpAPI calls, opt-in):
```bash
PYTEST_RUN_INTEGRATION=1 PYTHONPATH="" ./venv/bin/python -m pytest tests/test_integration_serp.py -v
```

---

## Project structure

```
salesintel/
├── campaigns/
│   └── immigration-uk.json         # Campaign config (ICP, signals, segments, region)
│
├── agents/
│   ├── search_agent.py             # Step 1 — discover companies
│   ├── enrich_agent.py             # Step 2 — fill missing info
│   ├── signal_agent.py             # Step 3 — detect buying signals
│   ├── rating_agent.py             # Step 4 — score 1–10
│   ├── contact_agent.py            # Step 5 — find decision-maker contacts
│   └── provider.py                 # LLM provider (Vertex AI / Anthropic)
│
├── tools/
│   ├── serp_tool.py                # SerpAPI search with disk caching
│   ├── sheets_tool.py              # Append new company rows
│   ├── sheets_read_tool.py         # Read company rows
│   ├── sheets_update_info_tool.py  # Write enriched info fields
│   └── sheets_update_signal_tool.py# Write signal results
│
├── api/
│   └── main.py                     # FastAPI backend for campaign CRUD
│
├── frontend/                       # React campaign editor UI
│   ├── src/
│   │   ├── App.tsx
│   │   ├── types.ts
│   │   └── components/
│   └── package.json
│
├── tests/
├── sheets_setup.py                 # Create/reformat sheet tabs
├── campaign.py                     # Campaign config schema (Pydantic)
└── melt2.json                      # Google service account key (not in repo)
```

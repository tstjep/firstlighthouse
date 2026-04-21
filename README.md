# Firstlighthouse

Automatically builds ranked lists of target companies in any market — so you know exactly who to reach out to first.

---

### The problem this solves

The 0→1 stage is the hardest part of building a company — and most founders underestimate it. You have a product and a rough sense of who might want it, but no sales team, no existing pipeline, and limited market research. You don't yet know which types of companies are the best fit, which signals separate a hot lead from a waste of time, or who inside a company is actually worth talking to.

Most founders respond by either doing nothing (waiting for word of mouth) or doing too much (manually researching hundreds of companies in spreadsheets). Both approaches stall.

firstlighthouse is built specifically for this stage. It guides you through the process of defining your ICP, identifying what a good fit looks like in practice, and turning that into a structured, prioritised list of real companies and contacts — without requiring sales experience or expensive data subscriptions. The tool is deliberately limited in scope: it doesn't try to run your outreach, manage your pipeline, or predict churn. It does one thing — helps you figure out *who* to talk to and *why* — and gets out of the way.

Built on **AI agents** ([nanobot](https://github.com/nanobot-ai/nanobot)): each step is an autonomous agent that runs searches, reasons over results, and decides what to write — so it handles ambiguous company data, missing websites, and partial information the way a human researcher would, not with brittle rules.

Works across go-to-market stages:

- **0→1 sales** — founders who need to find and prioritise their first customers, without a sales team or expensive data tools
- **User research** — founders and PMs who need a pool of warm contacts for discovery interviews
- **Outbound at scale** — growth teams building segmented, signal-qualified prospect lists for sequenced outreach

Results export directly to **Waalaxy**, **Lemlist**, or any CSV-based outreach tool.

---

## How it works

Five automated steps, each run independently or as a full pipeline:

**1. Find companies** — targeted web searches (SerpAPI) discover companies matching your ICP. Deduplicates across all segments.

**2. Enrich** — fills in missing website, LinkedIn, size, HQ, and description for every row. Works on manually-added companies too — a name alone is enough.

**3. Detect buying signals** — for each company, collects evidence from `site:` searches, name-based searches, and direct scraping, then asks an LLM to decide Yes/No for each signal you've defined, with a source link for verification.

**4. Rate 1–10** — rule-based scoring from signal results. Signal point values are set in the campaign config. No LLM needed for this step.

**5. Find contacts** — for top-rated companies, searches LinkedIn for decision-makers in the roles you specify. Writes contact lists to results and exports CSV for import into outreach tools.

---

## Setup

### Requirements

- Python 3.11+
- A SerpAPI key (set `SERPAPI_KEY` in `config.py`)
- An LLM provider: set `ANTHROPIC_API_KEY`, or configure `VERTEX_PROJECT` in `config.py` for Vertex AI (Gemini)

### Install

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Start the UI

```bash
PYTHONPATH="" ./venv/bin/python -m uvicorn frontend.app:app --reload --port 8000
```

Open `http://localhost:8000` to create and edit campaigns.

### LinkedIn (optional, for contact finding)

Add to `.env`:
```
LINKEDIN_LI_AT=<your li_at cookie>
LINKEDIN_JSESSIONID=<your JSESSIONID cookie>
```
Get these from browser DevTools → Application → Cookies while logged into linkedin.com.

---

## Campaigns

Each campaign defines a market: who you're targeting, where they are, what signals matter, and what contacts to find. Configure everything in the **web UI** — firstlighthouse persists it as a JSON file in `campaigns/` that the agents read at runtime.

**Campaign fields:**
- **ICP** — plain-English description of your product and ideal customer, injected into all LLM prompts
- **Region** — country, TLD filter (e.g. `.co.uk`), SerpAPI geolocation params. Quick-select presets for UK, Germany, Austria, Switzerland, USA.
- **Segments** — one per results tab, each with its own search queries and contact roles
- **Signals** — what to detect, with keywords, LLM definitions, and point values
- **LinkedIn** — session cookies for the contact-finding fallback

### Writing a sharp ICP

The ICP field accepts plain prose. The more specific it is, the better the search queries, signal suggestions, and signal detections become. A useful template:

```
We sell [product] to [industry / company type] with [size] employees.
Their main pain point is [pain point].
A strong buying signal is when they [buying trigger].
The decision maker is the [role].
Skip companies that [exclusion criteria].
```

You can start broad and sharpen over time — even a single sentence is enough to get started.

**Example — HR SaaS for Swiss SMBs:**
> We sell HRly, a cloud HR platform, to Swiss SMBs with 20–200 employees in professional services and light manufacturing. Their main pain point is manual HR admin and staying compliant with Swiss labour law. A strong buying signal is that they are actively hiring an HR manager or scaling headcount. The decision maker is typically the CEO or head of HR. Skip companies with fewer than 10 staff, large enterprises with SAP/Workday already in place, or government-sector organisations.

Signals generated from this ICP: **Active Hiring** (+2), **No HR System** (+2), **Too Large** (−3).
Reference campaign: [`campaigns/hr-saas-ch.json`](campaigns/hr-saas-ch.json)

**Example — B2B outbound sales tool for UK SaaS companies:**
> We sell Outbound.io to UK-based SaaS and tech companies with 10–150 employees. Their pain point is that the sales team spends too much time manually researching leads. A buying signal is that they recently hired an SDR, Head of Growth, or VP Sales, or raised Series A/B funding. The decision maker is the VP of Sales or Head of Revenue. Skip bootstrapped solo founders, agencies, and companies with a mature Salesforce/RevOps setup.

Signals generated from this ICP: **Recent Funding** (+3), **Sales Hiring** (+2), **No Outbound Stack** (+2), **Enterprise RevOps** (−3).
Reference campaign: [`campaigns/sales-tools-uk.json`](campaigns/sales-tools-uk.json)

---

### AI signal suggestions

The editor has a **✦ Suggest signals** button. Fill in your ICP, click it, and the AI runs two parallel calls:
1. Suggests 1–3 positive buying signals relevant to your ICP
2. Suggests 1–2 negative signals (red flags / exclusion criteria)

You can accept or skip each suggestion individually. Accepted signals are added as full editable cards — review and refine them before saving.

You can also ask for **additional suggestions** once you've defined your initial signals. This second call sees both your ICP and your existing signals, so it avoids duplicating what you already have and focuses on gaps.

---

## Buying signals

Signals are clues the AI looks for on each company's website. Define them in the UI:

| Field | What it does |
|---|---|
| **Name** | Display label in the results table |
| **Points** | Positive = boosts score; negative = down-ranks or excludes |
| **Description** | Short tooltip for the results column header |
| **Definition** | Full instruction for the LLM: when to mark Yes vs No |
| **Keywords** | Used to build targeted `site:` search queries |

### Excluding companies

Use a **negative signal** to identify poor fits. Example:

> Signal: `GeneralPractice` · Points: `-3`
> Definition: Mark Yes if the firm lists unrelated practice areas alongside immigration — e.g. conveyancing, family law, criminal defence. Mark No if immigration is their primary or sole focus.

Any company where that signal = Yes loses 3 points, pushing them below the contact threshold. Negative signals appear as their own columns so you can see the reasoning.

More examples of exclusion signals:
- **EnterpriseSize** (−2): company has 500+ staff or is part of an international network — likely too slow to buy or already locked in
- **LegalAidOnly** (−5): mentions legal aid or pro bono as primary offering — no commercial relationship possible

---

## Results

Results are stored locally in `data/<campaign_id>/results.json` and displayed in a table at `/campaigns/<id>/results`. No Google Sheets auth needed.

Each company row has: name, rating, notes, website, size, HQ, date added, one column per signal (✓/✗ with source tooltip), and contacts.

**Export** to Waalaxy or Lemlist CSV from the results page header.

---

## Running agents

Agents are launched from the **Run** page in the UI. Select a segment, pick a run mode, and click Start:

| Mode | Steps |
|---|---|
| Quick test | Search only, 5 companies — verify your config works |
| Search only | Find companies for a segment |
| Search + Enrich | Find and enrich company profiles |
| Search + Enrich + Signals + Rating | Full scoring pipeline |
| Full run | Everything, including contact finding |

The Run page shows live output as the agents execute.

---

## Testing

```bash
PYTHONPATH="" ./venv/bin/python -m pytest tests/ -v
```

Integration tests (real SerpAPI calls, opt-in):
```bash
PYTEST_RUN_INTEGRATION=1 PYTHONPATH="" ./venv/bin/python -m pytest tests/ -v -m integration
```

---

## Project structure

```
firstlighthouse/
├── campaigns/
│   ├── immigration-uk.json      # UK immigration law firms (original)
│   ├── hr-saas-ch.json          # HR SaaS → Swiss SMBs (reference scenario)
│   └── sales-tools-uk.json     # B2B sales tool → UK SaaS (reference scenario)
│
├── agents/
│   ├── search_agent.py          # Step 1 — discover companies
│   ├── enrich_agent.py          # Step 2 — fill missing info
│   ├── signal_agent.py          # Step 3 — detect buying signals
│   ├── rating_agent.py          # Step 4 — score 1–10
│   ├── contact_agent.py         # Step 5 — find contacts
│   └── provider.py              # LLM provider (Vertex AI / Anthropic / OpenAI-compat)
│
├── tools/
│   ├── serp_tool.py             # SerpAPI search with disk caching
│   ├── json_append_tool.py      # Add company to JSON store
│   ├── json_update_info_tool.py # Write enriched fields
│   └── json_update_signal_tool.py # Write signal results
│
├── frontend/
│   ├── app.py                   # FastAPI server (SSR, no websockets)
│   └── templates/               # Jinja2 templates (Pico CSS)
│       ├── base.html
│       ├── campaigns.html
│       ├── editor.html
│       ├── run.html
│       └── results.html
│
├── tests/
│   ├── test_suggest_signals.py  # Signal suggestion logic
│   ├── test_store.py            # JSON result store
│   └── test_campaign.py         # Campaign schema validation
│
├── campaign.py                  # Pydantic v2 campaign schema
├── store.py                     # Local JSON data store
├── suggest_signals.py           # LLM-powered signal suggestions
├── run_manager.py               # Background agent subprocess runner
└── config.py                    # SerpAPI key, LLM provider config
```

# Immigration Finder — UK Immigration Lead Intelligence

An AI agent that discovers UK immigration companies — law firms, OISC advisers, corporate consultants, and legaltech providers — and records them in a Google Sheet for outreach. Built on [nanobot-ai](https://github.com/HKUDS/nanobot).

---

## Overview

The search agent runs targeted SerpAPI queries per company type and appends results to the corresponding Google Sheet tab. Each tab is a separate company category.

```
immigration_search_agent   Find UK immigration companies → append to sheet tab
```

Future pipeline steps (not yet implemented):
```
enrich_agent               Fill missing info (website, LinkedIn, size, HQ)
signal_agent               Detect buying signals (case management, tech adoption, etc.)
rating_agent               Score leads 1–5
export_agent               Find decision-makers → CSV for outreach
```

---

## Google Sheet Structure

One spreadsheet with four tabs — one per company type.

| Tab | Target companies |
|-----|----------------|
| `LawFirms` | SRA-regulated immigration solicitors and law firms |
| `Advisors` | OISC-regulated immigration advisers (Level 1–3) |
| `Consultants` | Corporate immigration consultants, global mobility, HR visa services |
| `LegaltechBrokers` | Immigration case management software, process outsourcers |

### Columns (A:I)

| Col | Header | Description |
|-----|--------|-------------|
| A | Company Name | Legal or trading name |
| B | Comment Melt | Internal team notes |
| C | Rating | 1–5 score (manual or future agent) |
| D | Notes | Agent-generated description |
| E | Website | Primary website URL |
| F | LinkedIn | LinkedIn company page URL |
| G | Size | Employee count range (e.g. `11-50`) |
| H | HQ Location | City, UK |
| I | Date Added | Auto-set on append |

---

## Project Structure

```
immigrationfinder/
├── agents/
│   ├── immigration_search_agent.py   # Company discovery via SerpAPI (entry point)
│   └── provider.py                   # Shared LLM provider factory
│
├── tools/
│   ├── serp_tool.py                  # SerpAPI search with disk caching
│   └── sheets_tool.py                # Append company rows with dedup
│
├── tests/                            # Mocked unit tests (no real API calls)
├── config.py                         # API keys, spreadsheet ID, LLM config
├── requirements.txt
└── melt2.json                        # Google service account credentials (not in repo)
```

---

## Setup

### 1. Install dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip3 install -r requirements.txt
```

### 2. Google Sheets credentials

Create a Google Cloud service account with **Google Sheets API** access. Download the JSON key as `melt2.json` and place it in the project root. Share the target spreadsheet with the service account email (Editor access).

### 3. Configure `config.py`

```python
SPREADSHEET_ID   = "<your-spreadsheet-id>"
SERPAPI_KEY      = "<your-serpapi-key>"
CREDENTIALS_FILE = "melt2.json"
```

### 4. LLM provider

Agents use Vertex AI by default (`vertex_ai/gemini-2.5-flash`). Configure in `config.py`:

```python
VERTEX_PROJECT  = "your-gcp-project-id"
VERTEX_LOCATION = "us-central1"
DEFAULT_MODEL   = "vertex_ai/gemini-2.5-flash"
```

---

## Running the Agent

```bash
# Search for law firms (default)
python3 agents/immigration_search_agent.py

# Search a specific company type
python3 agents/immigration_search_agent.py --tab LawFirms
python3 agents/immigration_search_agent.py --tab Advisors
python3 agents/immigration_search_agent.py --tab Consultants
python3 agents/immigration_search_agent.py --tab LegaltechBrokers
```

The agent runs all configured search queries for that tab, deduplicates against existing sheet rows, and appends new companies.

---

## Testing

```bash
python3 -m pytest tests/ -v
```

All tests mock Google Sheets and SerpAPI — no real API calls needed.

---

## API Keys

| Key | Source |
|-----|--------|
| `SERPAPI_KEY` | [serpapi.com](https://serpapi.com) |
| Google Sheets | [Google Cloud Console](https://console.cloud.google.com) → Service Accounts |
| Vertex AI | [Google Cloud Console](https://console.cloud.google.com) → Vertex AI |

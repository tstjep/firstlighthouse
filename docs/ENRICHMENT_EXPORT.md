# Enrichment & Export Pipeline

How companies go from a name in the sheet to an outreach-ready CSV with decision-maker contacts.

---

## Pipeline Overview

```
Sheet row (name only)
  │
  ├─ enrich_agent        Fill website, LinkedIn, size, HQ, notes
  ├─ signal_keyword_agent   Detect AI / Sovereignty / Edge / Cost / K8s signals
  ├─ rating_agent           Score 1–5 (rule-based, no LLM)
  │
  └─ export_agent        Find people → CSV
       ├─ LinkedIn via SerpAPI    site:linkedin.com/in queries
       ├─ Company website         site:domain team/about pages
       ├─ Follow-up LinkedIn      resolve website-found people to LinkedIn URLs
       └─ LinkedIn API (optional) direct search via linkedin-api library
```

Each step reads from and writes to the shared Google Sheet. Run them independently or chain them.

---

## 1. Enrich Agent (`agents/enrich_agent.py`)

Fills missing company info for rows where the `Notes` column is empty (meaning the row hasn't been enriched yet).

### What it fills
| Field | Example |
|-------|---------|
| Website | `https://acme.com` |
| LinkedIn | `https://linkedin.com/company/acme` |
| Size | `51-200` |
| HQ Location | `Zurich, Switzerland` |
| Notes | `Swiss cloud hosting provider specialising in managed Kubernetes` |

### How it works
1. **Pre-filter** — Python scans the sheet and selects rows with empty Notes
2. **SerpAPI prefetch** — fires all searches in parallel (up to 20 concurrent):
   - Search A: `"domain.com"` or `"company name" company IT OR cloud`
   - Search B: `site:linkedin.com/company "company name"` (if LinkedIn unknown)
3. **LLM extraction** — Gemini reads the search snippets (no URL visits) and calls `sheets_update_company_info` for each row
4. Processes in chunks of 20 companies per LLM call

### CLI
```bash
python agents/enrich_agent.py                   # default country (config.py)
python agents/enrich_agent.py --country CH       # Swiss companies
python agents/enrich_agent.py --country KubeCon  # KubeCon tab
```

### Handles manually-added rows
Type a company name (or name + website) into the sheet → run enrich_agent → all fields get filled.

---

## 2. Signal Keyword Agent (`agents/signal_keyword_agent.py`)

Detects five technology buying signals by searching each company's own website.

### Signals
| Signal | Keywords searched |
|--------|------------------|
| AI | AI, KI, machine learning, LLM, GPT, neocloud |
| Sovereignty | data sovereignty, GDPR, DSGVO, nDSG, private cloud, on-premise |
| Edge | edge computing, IoT edge, CDN, fog computing |
| Cost | cost optimisation, FinOps, Kostenoptimierung |
| Kubernetes | Kubernetes, k8s, Docker, Helm, microservices, serverless |

### How it works
1. Reads all companies from the sheet
2. For each company with a website, runs two `site:domain` SerpAPI searches:
   - Search A: AI + Kubernetes + Edge keywords
   - Search B: Sovereignty + Cost keywords
3. LLM reads titles/snippets and writes Yes/No + evidence source for each signal

### CLI
```bash
python agents/signal_keyword_agent.py --country CH
```

---

## 3. Rating Agent (`agents/rating_agent.py`)

Rule-based 1–5 scoring. No LLM calls — fast and free.

### Scoring formula
| Factor | Points |
|--------|--------|
| Sovereignty signal = Yes | +3 |
| KubeCon 2026 signal = Yes | +3 |
| AI signal = Yes | +2 |
| Kubernetes signal = Yes | +2 |
| Sovereignty keywords in notes/HQ | +2 |
| Edge signal = Yes | +1 |
| Cost signal = Yes | +1 |
| Cloud/infrastructure keywords | +1 |
| Modern-infra keywords (k8s, docker, devops) | +1 |
| Complete profile (website + LinkedIn + size) | +1 |

### Score mapping
| Points | Score | Label |
|--------|-------|-------|
| >= 7 | 5 | Prime |
| >= 4 | 4 | Strong |
| >= 2 | 3 | Solid |
| >= 1 | 2 | Weak |
| 0 | 1 | Unknown |

### CLI
```bash
python agents/rating_agent.py --country CH
python agents/rating_agent.py --country DE --force   # re-rate all rows
```

---

## 4. Export Agent (`agents/export_agent.py` + `agents/export/`)

Finds decision-makers at rated companies and outputs a CSV for outreach.

### Module structure

| Module | Purpose |
|--------|---------|
| `export_agent.py` | CLI entry point (`main()`) |
| `export/constants.py` | Constants, regexes, role priority logic |
| `export/helpers.py` | Pure utilities: name parsing, country matching, color classification |
| `export/sheets.py` | Google Sheets reading, filtering, URL enrichment |
| `export/serp.py` | SerpAPI profile search, SERP parsing, async orchestration |
| `export/linkedin.py` | LinkedIn API search with caching |
| `export/csv_writer.py` | CSV output |

### People search sources

**LinkedIn via SerpAPI** (default) — two queries per company:
- Leadership: `site:linkedin.com/in "Company" "CTO" OR "Founder" OR "VP Engineering" OR ...`
- Technical: `site:linkedin.com/in "Company" "DevOps" OR "Cloud Architect" OR "SRE" OR ...`

**Company website** — if the company has a website URL:
- `site:domain.com CTO OR "DevOps" OR "Head of" OR "team" OR "about"`
- Names extracted from search result titles (e.g. "John Doe - CTO at Acme")

**Follow-up LinkedIn resolution** — people found on websites but not LinkedIn get:
- `site:linkedin.com/in "First Last" "Company"` to find their profile URL

**LinkedIn API fallback** (`--linkedin-fallback N`) — after SerpAPI, for companies that need better profiles:
- Triggers when a company has <N profiles **or** has no high-priority role (Platform Engineer through SRE)
- Runs `linkedin-api` search only for gap companies
- Merges with SerpAPI results, deduplicates by URL and name, sorts by role priority
- Requires `LINKEDIN_EMAIL` + `LINKEDIN_PASSWORD` (via `.env` or env vars)

**LinkedIn API only** (`--linkedin` flag) — skips SerpAPI entirely:
- Searches by role keywords with company filter
- Requires `LINKEDIN_EMAIL` + `LINKEDIN_PASSWORD`
- Results cached to `linkedin_cache/` (daily)

### Roles searched
Leadership: CTO, Head of Infrastructure, IT-Leiter

Technical: DevOps, Platform Engineer, Cloud Architect, SRE, Infrastructure Engineer, System Administrator

### Role priority (for `--max-profiles` capping)
When more profiles are found than `--max-profiles` allows, the highest-priority roles are kept:

**Tier 1 — Core technical (high-priority for fallback trigger):**
1. Platform Engineer
2. DevOps
3. Cloud Architect
4. CTO
5. SRE
6. Site Reliability
7. Container

**Tier 2 — Infrastructure/ops:**
8. Infrastructure Engineer
9. Head of Infrastructure
10. Cloud Engineer
11. Data Center / Datacenter
12. Network Engineer
13. Ceph

**Tier 3 — Leadership/exec:**
14. VP (Engineering, Infrastructure, etc.)
15. Technical Lead
16. CEO
17. Founder

**Tier 4 — General IT:**
18. IT-Leiter
19. System Administrator
20. System Engineer

After the LinkedIn fallback merge, any dropped profiles are reviewed by the LLM which highlights
which ones were worth keeping for infrastructure outreach.

### CLI flags
| Flag | Default | Description |
|------|---------|-------------|
| `--min-rating` | 3 | Minimum numeric rating |
| `--include-hosters` | off | Include "possible hoster" companies |
| `--country CODE` | all | Filter by country (CH, DE, AT, UK, NL, FR, ...) |
| `--linkedin` | off | Use LinkedIn API only (needs env vars) |
| `--linkedin-fallback N` | 0 (off) | SerpAPI first, LinkedIn API for companies with <N profiles |
| `--tab NAME` | KubeCon | Sheet tab to read from |
| `--color COLOR` | all | Filter by row background color (green, red, yellow, blue) |
| `--max-profiles` | 5 | Max profiles per company |
| `--limit N` | no limit | Cap number of companies processed |
| `--dry-run` | off | List companies without searching |
| `--output FILE` | stdout | Output CSV path |

### CSV output
```
LinkedIn URL, First Name, Last Name, Company Name, Company LinkedIn URL, Company Website, Rating
```

LinkedIn URL may be empty for people found on company websites where no LinkedIn profile was resolved.

### Examples
```bash
# Preview qualifying companies
python agents/export_agent.py --dry-run

# Export from a specific tab, green rows only, with LinkedIn fallback
python agents/export_agent.py --tab "KubeCon-Outreach-EN" --color green --linkedin-fallback 2 -o green_leads.csv

# Export German companies rated 4+
python agents/export_agent.py --min-rating 4 --country DE -o de_leads.csv

# Use LinkedIn API only
python agents/export_agent.py --linkedin -o leads.csv

# Include "possible hoster" companies, limit to 20
python agents/export_agent.py --include-hosters --limit 20 -o leads.csv
```

---

## KubeCon Pipeline

The KubeCon tab follows the same enrichment/export flow but has two extra agents at the front:

1. **`kubecon_agent.py`** — stages the attendee list from `kubecon.txt` into the KubeCon sheet tab, classifies obvious non-targets as "D"
2. **`kubecon_rate_agent.py`** — LLM-based 1–5 scoring (unlike the rule-based DACH rating agent)
3. **`enrich_agent.py --country KubeCon`** — fills missing company info
4. **`export_agent.py`** — finds people, outputs CSV

### KubeCon sheet differences
- Extra "Human Comment" column at D (shifts Notes to E, everything after by +1)
- 28 columns (A:AB) vs 27 (A:AA) for DACH tabs

---

## Caching

- **SerpAPI**: results cached to `serp_cache/` as JSON, keyed by date + query hash
- **LinkedIn API**: results cached to `linkedin_cache/` as JSON, keyed by date + company ID

Both caches are daily — same query on the same day hits disk instead of the API.

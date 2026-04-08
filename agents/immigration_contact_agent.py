#!/usr/bin/env python3
"""
Immigration Contact Finder Agent
==================================
Reads rated companies from the Google Sheet, searches for LinkedIn profiles
of decision-makers at each firm, and outputs a CSV for Waalaxy outreach.

Target contacts by tab:
  LawFirms        — Managing Partner, Head of Immigration, Partner, Director
  Advisors        — Director, Owner, Head of Immigration
  Charities       — CEO, Director, Head of Service
  LegaltechBrokers — Managing Director, Consultant, Partner

Usage:
    python agents/immigration_contact_agent.py
    python agents/immigration_contact_agent.py --tab Advisors
    python agents/immigration_contact_agent.py --min-rating 6
    python agents/immigration_contact_agent.py --max-profiles 2
    python agents/immigration_contact_agent.py --dry-run
    python agents/immigration_contact_agent.py --output contacts.csv
    python agents/immigration_contact_agent.py --limit 20
    python agents/immigration_contact_agent.py --fallback-threshold 2   # LinkedIn fallback if <2 profiles found

LinkedIn fallback:
    When SerpAPI finds fewer than --fallback-threshold profiles for a company,
    the agent queries LinkedIn's Voyager API using browser cookies to search
    for employees in the correct roles. Set in .env:
        LINKEDIN_LI_AT=<your li_at cookie>
        LINKEDIN_JSESSIONID=<your JSESSIONID cookie>
    Get these from browser DevTools → Application → Cookies on linkedin.com.

Output: Waalaxy-compatible CSV on stdout (or --output file).
"""

import argparse
import asyncio
import csv
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import httpx

import config as cfg
from tools.serp_tool import SerpSearchTool

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# ── Google Sheets ─────────────────────────────────────────────────────────────

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# Immigration sheet column indices (0-based, A:H)
_COL_NAME    = 0  # A
_COL_RATING  = 2  # C
_COL_NOTES   = 3  # D
_COL_WEBSITE = 4  # E
_COL_LINKEDIN= 5  # F
_COL_SIZE    = 6  # G
_COL_HQ      = 7  # H

# ── Role priorities ───────────────────────────────────────────────────────────
# Lower index = higher priority. Used to sort and cap profiles per company.

_ROLE_PRIORITY = [
    # Tier 1 — most relevant decision-makers
    "Managing Partner",
    "Head of Immigration",
    "Immigration Partner",
    "Immigration Director",
    # Tier 2 — senior leadership
    "Partner",
    "Director",
    "Managing Director",
    "Chief Executive",
    "CEO",
    # Tier 3 — operational leaders
    "Operations Director",
    "Operations Manager",
    "Head of Operations",
    "Head of ",
    # Tier 4 — senior practitioners
    "Senior Immigration",
    "Immigration Solicitor",
    "Immigration Adviser",
    "Immigration Consultant",
    # Tier 5 — general senior roles
    "Senior Associate",
    "Principal",
    "Founder",
    "Owner",
]

_ROLE_PRIORITY_DEFAULT = len(_ROLE_PRIORITY)

# ── Tab-specific search queries ───────────────────────────────────────────────

_TAB_ROLE_QUERIES: dict[str, list[tuple[str, str]]] = {
    "LawFirms": [
        (
            '"Managing Partner" OR "Head of Immigration" OR "Immigration Partner"',
            '"Immigration Director" OR "Partner"',
        ),
        (
            '"Director" OR "Operations Director" OR "Operations Manager"',
            '"Immigration Solicitor" OR "Senior Immigration"',
        ),
    ],
    "Advisors": [
        (
            '"Director" OR "Head of Immigration" OR "Owner"',
            '"Managing Director" OR "Chief Executive" OR "CEO"',
        ),
        (
            '"Immigration Adviser" OR "Senior Adviser"',
            '"Operations Manager" OR "Head of"',
        ),
    ],
    "Charities": [
        (
            '"CEO" OR "Chief Executive" OR "Director"',
            '"Head of Immigration" OR "Head of Services" OR "Head of"',
        ),
        (
            '"Programme Manager" OR "Operations Manager"',
            '"Immigration Adviser" OR "Senior Caseworker"',
        ),
    ],
    "LegaltechBrokers": [
        (
            '"Managing Director" OR "Partner" OR "Consultant"',
            '"Director" OR "Head of" OR "CEO"',
        ),
        (
            '"Technology Consultant" OR "Legal Technology"',
            '"Founder" OR "Principal"',
        ),
    ],
}

_SEARCH_CONCURRENCY = 5
_LINKEDIN_DELAY = 1.5  # seconds between LinkedIn API calls to avoid rate limiting

# Role keywords per tab used for LinkedIn people search
_TAB_LINKEDIN_ROLES: dict[str, list[str]] = {
    "LawFirms":         ["Managing Partner", "Head of Immigration", "Partner", "Director"],
    "Advisors":         ["Director", "Owner", "Head of Immigration", "Managing Director"],
    "Charities":        ["CEO", "Director", "Chief Executive", "Head of"],
    "LegaltechBrokers": ["Managing Director", "Consultant", "Partner", "Director"],
}

# ── Regex helpers ─────────────────────────────────────────────────────────────

_LINKEDIN_PROFILE_RE = re.compile(
    r"https?://(?:\w+\.)?linkedin\.com/in/([\w-]+)",
    re.IGNORECASE,
)
_COMPANY_URL_RE = re.compile(
    r"https?://(?:\w+\.)?linkedin\.com/company/([\w-]+)",
    re.IGNORECASE,
)
_TITLE_NAME_RE = re.compile(
    r"^([A-Z][a-zà-ÿ]+(?:\s+[A-Z][a-zà-ÿ]+){1,2})\s*[-–|,]\s*(.+)",
)
_NON_PERSON_WORDS = frozenset({
    "our", "about", "the", "meet", "team", "company", "home", "welcome",
    "contact", "careers", "services", "immigration", "advice", "legal",
    "overview", "leadership", "management", "executive", "board", "staff",
})
_COMPANY_SUFFIXES = frozenset({
    "llp", "llc", "ltd", "limited", "inc", "plc", "co", "solicitors",
    "advisers", "advisors", "associates", "partners", "group",
})
_LEGAL_SUFFIX_RE = re.compile(
    r"\s+(?:LLP|Ltd|Limited|LLC|Inc|Corp|Co|PLC|& Co)\s*\.?\s*$",
    re.IGNORECASE,
)

# ── CSV headers ───────────────────────────────────────────────────────────────

_CSV_HEADERS = [
    "LinkedIn URL",
    "First Name",
    "Last Name",
    "Company Name",
    "Company LinkedIn URL",
    "Company Website",
    "Rating",
    "Role",
    "Title Hint",
]


# ── Pure helpers ──────────────────────────────────────────────────────────────

def _cell(row: list, idx: int) -> str:
    return row[idx].strip() if idx < len(row) else ""


def _extract_domain(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = urlparse(url).hostname
        return host if host else None
    except Exception:
        return None


def _strip_legal_suffix(name: str) -> str:
    return _LEGAL_SUFFIX_RE.sub("", name).strip()


def _role_priority(title_hint: str) -> int:
    hint_lower = title_hint.lower()
    for i, role in enumerate(_ROLE_PRIORITY):
        if role.lower() in hint_lower:
            return i
    return _ROLE_PRIORITY_DEFAULT


def _matched_role(title_hint: str) -> str:
    hint_lower = title_hint.lower()
    for role in _ROLE_PRIORITY:
        if role.lower() in hint_lower:
            return role.strip()
    return ""


def _is_valid_linkedin_slug(slug: str) -> bool:
    if not slug or len(slug) < 3:
        return False
    if slug.endswith("-"):
        return False
    if slug.startswith("ACo") and len(slug) > 20:
        return False
    return True


def _parse_name_from_slug(slug: str) -> tuple[str, str]:
    parts = slug.rstrip("/").split("-")
    if not parts or parts == [""]:
        return "", ""
    if len(parts) > 2 and re.match(r"^[a-f0-9]{4,}$", parts[-1], re.I):
        parts = parts[:-1]
    if len(parts) < 2:
        return parts[0].title() if parts else "", ""
    first = " ".join(p.title() for p in parts[:-1])
    last = parts[-1].title()
    return first, last


def _parse_name_from_title(title: str) -> tuple[str, str, str] | None:
    if not title:
        return None
    m = _TITLE_NAME_RE.match(title.strip())
    if not m:
        return None
    name_part = m.group(1)
    role_part = m.group(2).strip()
    first_word = name_part.split()[0].lower()
    if first_word in _NON_PERSON_WORDS:
        return None
    last_word = name_part.split()[-1].lower()
    if last_word in _COMPANY_SUFFIXES:
        return None
    parts = name_part.split()
    first = " ".join(parts[:-1])
    last = parts[-1]
    return first, last, role_part


def _parse_profiles_from_serp(serp_text: str, company_name: str) -> list[dict]:
    """Extract people from SerpAPI results (LinkedIn profiles + website mentions)."""
    if not serp_text or serp_text.startswith("[serp") or serp_text.startswith("No results"):
        return []

    seen_urls: set[str] = set()
    seen_names: set[tuple[str, str]] = set()
    profiles: list[dict] = []

    blocks = re.split(r"\n\n(?=\d+\.)", serp_text)

    for block in blocks:
        url_match = re.search(r"URL:\s*(\S+)", block)
        if not url_match:
            continue
        url = url_match.group(1)

        title_match = re.match(r"\d+\.\s+(.+)", block)
        title_text = title_match.group(1).strip() if title_match else ""

        profile_match = _LINKEDIN_PROFILE_RE.match(url)
        if profile_match:
            slug = profile_match.group(1)
            if not _is_valid_linkedin_slug(slug):
                continue
            normalized_url = f"https://www.linkedin.com/in/{slug}"
            if normalized_url in seen_urls:
                continue
            seen_urls.add(normalized_url)

            first, last = _parse_name_from_slug(slug)
            name_key = (first.lower(), last.lower())
            seen_names.add(name_key)

            parsed = _parse_name_from_title(title_text)
            role_hint = parsed[2] if parsed else title_text

            profiles.append({
                "url": normalized_url,
                "first_name": first,
                "last_name": last,
                "title_hint": role_hint,
                "source_url": "",
            })
            continue

        if _COMPANY_URL_RE.match(url):
            continue

        parsed = _parse_name_from_title(title_text)
        if not parsed:
            continue
        first, last, role_hint = parsed
        name_key = (first.lower(), last.lower())
        if name_key in seen_names:
            continue
        seen_names.add(name_key)
        profiles.append({
            "url": "",
            "first_name": first,
            "last_name": last,
            "title_hint": role_hint,
            "source_url": url,
        })

    return profiles


# ── Sheet reading ─────────────────────────────────────────────────────────────

def _read_and_filter(
    spreadsheet_id: str,
    credentials_file: str,
    tab: str,
    min_rating: int,
) -> list[dict]:
    """Read a sheet tab and return companies meeting the rating threshold."""
    creds = Credentials.from_service_account_file(credentials_file, scopes=_SCOPES)
    service = build("sheets", "v4", credentials=creds)
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=f"{tab}!A:H")
        .execute()
    )
    all_rows = result.get("values", [])
    if len(all_rows) < 2:
        return []

    companies = []
    for i, row in enumerate(all_rows[1:]):
        name        = _cell(row, _COL_NAME)
        rating_raw  = _cell(row, _COL_RATING)
        if not name:
            continue
        try:
            if int(rating_raw) < min_rating:
                continue
        except (ValueError, TypeError):
            continue  # skip unrated / provisional rows

        companies.append({
            "name":      name,
            "rating":    rating_raw,
            "notes":     _cell(row, _COL_NOTES),
            "website":   _cell(row, _COL_WEBSITE),
            "linkedin":  _cell(row, _COL_LINKEDIN),
            "size":      _cell(row, _COL_SIZE),
            "hq":        _cell(row, _COL_HQ),
            "sheet_row": i + 2,
        })

    return companies


# ── Search query building ─────────────────────────────────────────────────────

def _build_search_queries(company: dict, tab: str) -> list[str]:
    """Build SerpAPI queries for decision-maker profiles at an immigration firm."""
    name = company["name"]
    clean = _strip_legal_suffix(name)
    role_pairs = _TAB_ROLE_QUERIES.get(tab, _TAB_ROLE_QUERIES["LawFirms"])

    queries = []
    for primary_roles, secondary_roles in role_pairs:
        queries.append(
            f'site:linkedin.com/in "{clean}" ({primary_roles})'
        )
        queries.append(
            f'site:linkedin.com/in "{clean}" ({secondary_roles})'
        )

    # Website team/about page as fallback
    domain = _extract_domain(company.get("website", ""))
    if domain:
        queries.append(
            f'site:{domain} "Managing Partner" OR "Head of" OR "Director" OR "team" OR "about"'
        )

    return queries


def _build_fallback_queries(company: dict) -> list[str]:
    """Broader queries when targeted role search finds nobody."""
    name = company["name"]
    clean = _strip_legal_suffix(name)
    queries = [f'site:linkedin.com/in "{clean}"']
    domain = _extract_domain(company.get("website", ""))
    if domain:
        queries.append(f'site:{domain} team OR "about us" OR leadership OR management')
    return queries


# ── Async SerpAPI orchestration ───────────────────────────────────────────────

async def _resolve_linkedin_urls(
    profiles: list[dict],
    company_name: str,
    serp_tool: SerpSearchTool,
) -> list[dict]:
    """Follow-up search for LinkedIn URLs of website-sourced profiles."""
    for profile in profiles:
        if profile["url"]:
            continue
        first = profile["first_name"]
        last  = profile["last_name"]
        query = f'site:linkedin.com/in "{first} {last}" "{company_name}"'
        result = await serp_tool.execute(query=query, num=3)
        for p in _parse_profiles_from_serp(result, company_name):
            if p["url"]:
                profile["url"] = p["url"]
                break
    return profiles


async def _search_profiles_for_companies(
    companies: list[dict],
    api_key: str,
    tab: str,
    max_profiles: int = 3,
    concurrency: int = _SEARCH_CONCURRENCY,
) -> dict[str, list[dict]]:
    """Search LinkedIn for decision-makers at each company via SerpAPI."""
    tool = SerpSearchTool(api_key=api_key)
    sem  = asyncio.Semaphore(concurrency)

    async def search_one(company: dict) -> tuple[str, list[dict]]:
        async with sem:
            seen_urls:  set[str]            = set()
            seen_names: set[tuple[str, str]] = set()
            all_profiles: list[dict] = []

            def _collect(parsed: list[dict]) -> None:
                for p in parsed:
                    name_key = (p["first_name"].lower(), p["last_name"].lower())
                    if p["url"] and p["url"] in seen_urls:
                        continue
                    if name_key in seen_names:
                        continue
                    if p["url"]:
                        seen_urls.add(p["url"])
                    seen_names.add(name_key)
                    all_profiles.append(p)

            for query in _build_search_queries(company, tab=tab):
                result = await tool.execute(query=query, num=10)
                _collect(_parse_profiles_from_serp(result, company["name"]))

            if not all_profiles:
                print(f"    [contact] {company['name'][:50]} — retrying with broader queries",
                      file=sys.stderr)
                for query in _build_fallback_queries(company):
                    result = await tool.execute(query=query, num=10)
                    _collect(_parse_profiles_from_serp(result, company["name"]))

            all_profiles = await _resolve_linkedin_urls(
                all_profiles, company["name"], tool,
            )
            all_profiles.sort(key=lambda p: _role_priority(p["title_hint"]))
            return company["name"], all_profiles[:max_profiles]

    print(f"  [contact] Searching profiles for {len(companies)} companies "
          f"(concurrency={concurrency})…", file=sys.stderr)
    results = await asyncio.gather(*[search_one(c) for c in companies])
    print(f"  [contact] Done.", file=sys.stderr)
    return dict(results)


# ── LinkedIn cookie-based API ─────────────────────────────────────────────────

_LINKEDIN_BASE = "https://www.linkedin.com"
_LINKEDIN_VOYAGER = f"{_LINKEDIN_BASE}/voyager/api"

_COMPANY_SLUG_RE = re.compile(
    r"linkedin\.com/company/([\w-]+)",
    re.IGNORECASE,
)


def _linkedin_headers(li_at: str, jsessionid: str) -> dict:
    """Build headers for LinkedIn Voyager API requests."""
    token = jsessionid.strip('"')
    return {
        "Cookie": f'li_at={li_at}; JSESSIONID="{token}"',
        "Csrf-Token": token,
        "X-RestLi-Protocol-Version": "2.0.0",
        "X-Li-Lang": "en_US",
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
    }


def _extract_company_slug(linkedin_url: str) -> str | None:
    """Extract company slug from a LinkedIn company URL."""
    m = _COMPANY_SLUG_RE.search(linkedin_url)
    return m.group(1) if m else None


def _get_company_id(slug: str, headers: dict, timeout: float = 10.0) -> str | None:
    """Resolve a LinkedIn company slug to its numeric entity ID via Voyager API.

    Returns the string ID (e.g. "12345") or None on failure.
    """
    url = f"{_LINKEDIN_VOYAGER}/organization/companies"
    params = {"q": "universalName", "universalName": slug}
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(url, headers=headers, params=params)
        if r.status_code != 200:
            return None
        data = r.json()
        elements = data.get("elements", [])
        if elements:
            return str(elements[0].get("id", ""))
    except Exception:
        pass
    return None


def _parse_linkedin_people_response(data: dict) -> list[dict]:
    """Extract profile dicts from a Voyager search/blended response."""
    profiles: list[dict] = []
    for item in data.get("elements", []):
        # Voyager returns nested elements per cluster
        for elem in item.get("elements", []):
            entity = elem.get("targetUrn", "")
            hit = elem.get("hitInfo", {})
            mini = (
                hit.get("com.linkedin.voyager.search.SearchProfile", {})
                .get("miniProfile", {})
            )
            if not mini:
                continue
            first = mini.get("firstName", "")
            last  = mini.get("lastName", "")
            slug  = mini.get("publicIdentifier", "")
            occupation = mini.get("occupation", "")

            if not first and not last:
                continue

            url = f"https://www.linkedin.com/in/{slug}" if slug else ""
            profiles.append({
                "url":        url,
                "first_name": first,
                "last_name":  last,
                "title_hint": occupation,
                "source_url": "",
            })
    return profiles


def _search_linkedin_people(
    company_id: str,
    role_keywords: list[str],
    headers: dict,
    max_results: int = 5,
    timeout: float = 10.0,
) -> list[dict]:
    """Search LinkedIn for people at a company matching role keywords.

    Uses the Voyager search/blended endpoint with a currentCompany filter.
    """
    keywords = " OR ".join(f'"{kw}"' for kw in role_keywords)
    params = {
        "count":        str(max_results),
        "filters":      f"List(currentCompany->{company_id},resultType->PEOPLE)",
        "keywords":     keywords,
        "q":            "all",
        "queryContext": "List(spellCorrectionEnabled->true)",
        "start":        "0",
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(
                f"{_LINKEDIN_VOYAGER}/search/blended",
                headers=headers,
                params=params,
            )
        if r.status_code != 200:
            return []
        return _parse_linkedin_people_response(r.json())
    except Exception:
        return []


def _merge_profiles(
    existing: list[dict],
    new_profiles: list[dict],
    max_profiles: int,
) -> list[dict]:
    """Merge new profiles into existing list, deduplicating by URL and name."""
    seen_urls  = {p["url"] for p in existing if p["url"]}
    seen_names = {(p["first_name"].lower(), p["last_name"].lower()) for p in existing}
    merged = list(existing)
    for p in new_profiles:
        if p["url"] and p["url"] in seen_urls:
            continue
        name_key = (p["first_name"].lower(), p["last_name"].lower())
        if name_key in seen_names:
            continue
        if p["url"]:
            seen_urls.add(p["url"])
        seen_names.add(name_key)
        merged.append(p)
    merged.sort(key=lambda p: _role_priority(p["title_hint"]))
    return merged[:max_profiles]


def _run_linkedin_fallback(
    gap_companies: list[dict],
    company_profiles: dict[str, list[dict]],
    tab: str,
    li_at: str,
    jsessionid: str,
    max_profiles: int,
) -> int:
    """Run LinkedIn API fallback for companies below the profile threshold.

    Mutates company_profiles in place. Returns number of companies enriched.
    """
    headers      = _linkedin_headers(li_at, jsessionid)
    role_keywords = _TAB_LINKEDIN_ROLES.get(tab, _TAB_LINKEDIN_ROLES["LawFirms"])
    enriched     = 0

    for company in gap_companies:
        name = company["name"]
        slug = _extract_company_slug(company.get("linkedin", ""))
        if not slug:
            clean = _strip_legal_suffix(name).lower().replace(" ", "-")
            slug  = clean

        company_id = _get_company_id(slug, headers)
        if not company_id:
            print(f"    [linkedin] {name[:50]} — could not resolve company ID, skipping",
                  file=sys.stderr)
            continue

        time.sleep(_LINKEDIN_DELAY)
        new_profiles = _search_linkedin_people(
            company_id, role_keywords, headers, max_results=max_profiles,
        )

        if new_profiles:
            before = len(company_profiles.get(name, []))
            company_profiles[name] = _merge_profiles(
                company_profiles.get(name, []), new_profiles, max_profiles,
            )
            after = len(company_profiles[name])
            added = after - before
            print(f"    [linkedin] {name[:50]} — +{added} profile(s) "
                  f"(total: {after})", file=sys.stderr)
            if added:
                enriched += 1
        else:
            print(f"    [linkedin] {name[:50]} — no profiles found", file=sys.stderr)

    return enriched


# ── CSV output ────────────────────────────────────────────────────────────────

def _write_csv(
    company_profiles: dict[str, list[dict]],
    companies: list[dict],
    output,
) -> int:
    """Write Waalaxy-compatible CSV. Returns number of data rows written."""
    import io
    writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(_CSV_HEADERS)

    company_lookup = {c["name"]: c for c in companies}
    total = 0
    for company_name, profiles in company_profiles.items():
        if not profiles:
            continue
        company = company_lookup.get(company_name, {})
        for profile in profiles:
            writer.writerow([
                profile["url"],
                profile["first_name"],
                profile["last_name"],
                company_name,
                company.get("linkedin", ""),
                company.get("website", ""),
                company.get("rating", ""),
                _matched_role(profile.get("title_hint", "")),
                profile.get("title_hint", ""),
            ])
            total += 1
    return total


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find decision-maker contacts for rated immigration companies."
    )
    parser.add_argument(
        "--tab", default=cfg.DEFAULT_TAB, choices=cfg.IMMIGRATION_TABS,
        help=f"Sheet tab to read from (default: {cfg.DEFAULT_TAB})",
    )
    parser.add_argument(
        "--min-rating", type=int, default=5,
        help="Minimum numeric rating to include (default: 5)",
    )
    parser.add_argument(
        "--max-profiles", type=int, default=3,
        help="Maximum LinkedIn profiles per company (default: 3)",
    )
    parser.add_argument(
        "--limit", type=int, default=0, metavar="N",
        help="Cap number of companies to process (0 = no limit)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show qualifying companies without searching for contacts",
    )
    parser.add_argument(
        "--output", "-o", default=None, metavar="FILE",
        help="Output CSV file path (default: stdout)",
    )
    parser.add_argument(
        "--fallback-threshold", type=int, default=2, metavar="N",
        help="LinkedIn API fallback for companies with fewer than N profiles (default: 2, 0 = disabled)",
    )
    args = parser.parse_args()

    credentials_file = str(PROJECT_ROOT / cfg.CREDENTIALS_FILE)

    li_at      = cfg.LINKEDIN_LI_AT
    jsessionid = cfg.LINKEDIN_JSESSIONID
    has_linkedin_auth = bool(li_at and jsessionid)

    errors = []
    if not cfg.SPREADSHEET_ID:
        errors.append("SPREADSHEET_ID not set in config.py")
    if not cfg.SERPAPI_KEY:
        errors.append("SERPAPI_KEY not set in config.py")
    if not Path(credentials_file).exists():
        errors.append(f"Credentials file not found: {credentials_file}")
    if errors:
        print("[startup] Cannot continue:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)

    print(f"[contact] Reading tab '{args.tab}' (min_rating={args.min_rating})",
          file=sys.stderr)
    companies = _read_and_filter(
        cfg.SPREADSHEET_ID, credentials_file,
        tab=args.tab,
        min_rating=args.min_rating,
    )

    if args.limit and len(companies) > args.limit:
        print(f"[contact] {len(companies)} qualifying companies, "
              f"capped to {args.limit}", file=sys.stderr)
        companies = companies[:args.limit]
    else:
        print(f"[contact] {len(companies)} qualifying companies", file=sys.stderr)

    if not companies:
        print("[contact] No companies match the criteria.", file=sys.stderr)
        sys.exit(0)

    for c in companies:
        print(f"  [{c['rating']:>4}] {c['name'][:55]:<55}  {c['website'][:35]}",
              file=sys.stderr)

    if args.dry_run:
        print(f"\n[contact] DRY RUN — {len(companies)} companies would be searched.",
              file=sys.stderr)
        return

    company_profiles = asyncio.run(
        _search_profiles_for_companies(
            companies, cfg.SERPAPI_KEY,
            tab=args.tab,
            max_profiles=args.max_profiles,
        )
    )

    found = sum(1 for ps in company_profiles.values() if ps)
    total_profiles = sum(len(ps) for ps in company_profiles.values())
    no_results = [c["name"] for c in companies if not company_profiles.get(c["name"])]

    print(f"\n[contact] Found {total_profiles} profiles across "
          f"{found}/{len(companies)} companies", file=sys.stderr)
    if no_results:
        print("[contact] No profiles found for:", file=sys.stderr)
        for name in no_results:
            print(f"  - {name}", file=sys.stderr)

    # LinkedIn API fallback for companies below the threshold
    threshold = args.fallback_threshold
    if threshold > 0:
        gap_companies = [
            c for c in companies
            if len(company_profiles.get(c["name"], [])) < threshold
        ]
        if gap_companies and has_linkedin_auth:
            print(f"\n[contact] LinkedIn fallback: {len(gap_companies)} companies "
                  f"have fewer than {threshold} profiles, searching via LinkedIn API…",
                  file=sys.stderr)
            enriched = _run_linkedin_fallback(
                gap_companies, company_profiles,
                tab=args.tab,
                li_at=li_at,
                jsessionid=jsessionid,
                max_profiles=args.max_profiles,
            )
            new_total = sum(len(ps) for ps in company_profiles.values())
            print(f"[contact] LinkedIn fallback complete — "
                  f"{enriched} companies enriched, "
                  f"{new_total} total profiles (+{new_total - total_profiles})",
                  file=sys.stderr)
        elif gap_companies and not has_linkedin_auth:
            print(f"\n[contact] {len(gap_companies)} companies have fewer than {threshold} "
                  f"profiles but LinkedIn auth is not configured. "
                  f"Set LINKEDIN_LI_AT and LINKEDIN_JSESSIONID in .env to enable fallback.",
                  file=sys.stderr)

    if args.output:
        with open(args.output, "w", newline="", encoding="utf-8") as f:
            rows_written = _write_csv(company_profiles, companies, f)
        print(f"\n[contact] Wrote {rows_written} rows to {args.output}", file=sys.stderr)
    else:
        rows_written = _write_csv(company_profiles, companies, sys.stdout)
        print(f"\n[contact] Wrote {rows_written} rows to stdout", file=sys.stderr)

    if rows_written > 2500:
        print(f"[contact] WARNING: {rows_written} rows exceeds Waalaxy's 2500 row limit. "
              f"Use --limit or --max-profiles to reduce.", file=sys.stderr)


if __name__ == "__main__":
    main()

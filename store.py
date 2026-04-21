"""
Local JSON data store for firstlighthouse.

One JSON file per campaign: data/<campaign_id>/results.json

Company row schema:
  {
    "row_index":  int,           # 1-based, auto-assigned on append
    "name":       str,
    "comment":    str,
    "rating":     str,           # "8" or "~3" (provisional)
    "notes":      str,
    "website":    str,
    "linkedin":   str,
    "size":       str,           # "11-50"
    "hq":         str,           # "London, UK"
    "date_added": str,           # "YYYY-MM-DD"
    "signals":    {              # keyed by signal.key
      "corporate": {"value": "Yes", "source": "..."},
      ...
    },
    "contacts":   [str]          # "First Last | Role | linkedin_url"
  }

Store layout:
  data/<campaign_id>/results.json  →  {"rows": [<company>, ...]}
"""

from __future__ import annotations

import csv
import io
import json
import logging
import threading
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "data"
_ROWS_KEY = "rows"

_lock = threading.Lock()


class ResultStore:
    def __init__(self, campaign_id: str):
        self.campaign_id = campaign_id
        self._dir  = DATA_DIR / campaign_id
        self._path = self._dir / "results.json"

    # ── I/O ──────────────────────────────────────────────────────────────────

    def _load_raw(self) -> list[dict]:
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                rows = data.get(_ROWS_KEY, [])
                if isinstance(rows, list):
                    return rows
            logger.warning("results.json for %s has unexpected format, resetting", self.campaign_id)
            return []
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read results for %s: %s", self.campaign_id, exc)
            return []

    def _save_raw(self, rows: list[dict]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({_ROWS_KEY: rows}, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path)

    # ── Read ──────────────────────────────────────────────────────────────────

    def get_rows(self) -> list[dict]:
        return self._load_raw()

    def all_company_names(self) -> set[str]:
        return {
            r["name"].strip().lower()
            for r in self._load_raw()
            if isinstance(r.get("name"), str) and r["name"].strip()
        }

    def all_domains(self) -> set[str]:
        return {
            d
            for r in self._load_raw()
            if (d := _extract_domain(r.get("website", "")))
        }

    # ── Write ─────────────────────────────────────────────────────────────────

    def append_company(self, company: dict) -> bool:
        """Add a company if not already present (by name + domain). Returns True if added."""
        raw_name = company.get("name", "")
        name     = str(raw_name).strip().lower() if raw_name else ""
        domain   = _extract_domain(str(company.get("website", "")))

        if not name:
            logger.warning("append_company called with empty name, skipping")
            return False

        with _lock:
            rows = self._load_raw()

            for r in rows:
                if name and str(r.get("name", "")).strip().lower() == name:
                    return False
                if domain and _extract_domain(str(r.get("website", ""))) == domain:
                    return False

            next_idx = max((r.get("row_index", 0) for r in rows if isinstance(r.get("row_index"), int)), default=0) + 1

            row: dict[str, Any] = {
                "row_index":  next_idx,
                "name":       str(company.get("name", "")).strip(),
                "comment":    str(company.get("comment", "")),
                "rating":     str(company.get("rating", "")),
                "notes":      str(company.get("notes", "")),
                "website":    str(company.get("website", "")).strip(),
                "linkedin":   str(company.get("linkedin", "")).strip(),
                "size":       str(company.get("size", "")),
                "hq":         str(company.get("hq", "")),
                "date_added": str(company.get("date_added", date.today().isoformat())),
                "signals":    {},
                "contacts":   [],
            }
            rows.append(row)
            self._save_raw(rows)
            return True

    def update_company(self, row_index: int, fields: dict) -> bool:
        """Update fields on the row with the given row_index. Returns True if found."""
        _IMMUTABLE = {"row_index", "signals", "contacts"}
        with _lock:
            rows = self._load_raw()
            for row in rows:
                if row.get("row_index") == row_index:
                    for k, v in fields.items():
                        if k not in _IMMUTABLE:
                            row[k] = v
                    self._save_raw(rows)
                    return True
        return False

    def update_signal(self, row_index: int, signal_key: str, value: str, source: str) -> bool:
        """Write Yes/No + source for one signal. Returns True if row found."""
        if value not in ("Yes", "No"):
            logger.warning("update_signal: invalid value %r for signal %s", value, signal_key)
            return False
        with _lock:
            rows = self._load_raw()
            for row in rows:
                if row.get("row_index") == row_index:
                    row.setdefault("signals", {})[signal_key] = {
                        "value": value, "source": source
                    }
                    self._save_raw(rows)
                    return True
        return False

    def update_rating(self, row_index: int, rating: str) -> bool:
        return self.update_company(row_index, {"rating": rating})

    def set_contacts(self, row_index: int, contacts: list[str]) -> bool:
        """Replace the contacts list for a row."""
        with _lock:
            rows = self._load_raw()
            for row in rows:
                if row.get("row_index") == row_index:
                    row["contacts"] = [str(c) for c in contacts]
                    self._save_raw(rows)
                    return True
        return False


# ── CSV export ────────────────────────────────────────────────────────────────

def _parse_contact(contact: str) -> tuple[str, str, str]:
    """Split 'First Last | Role | URL' into (full_name, role, url). Tolerates missing parts."""
    parts = [p.strip() for p in contact.split("|")]
    name = parts[0] if len(parts) > 0 else ""
    role = parts[1] if len(parts) > 1 else ""
    url  = parts[2] if len(parts) > 2 else ""
    return name, role, url


def _split_name(full_name: str) -> tuple[str, str]:
    parts = full_name.split(" ", 1)
    return parts[0], parts[1] if len(parts) > 1 else ""


def to_waalaxy_csv(rows: list[dict]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "LinkedIn URL", "First Name", "Last Name",
        "Company Name", "Company LinkedIn URL", "Company Website",
        "Rating", "Role",
    ])
    for row in rows:
        company_name     = row.get("name", "")
        company_linkedin = row.get("linkedin", "")
        company_website  = row.get("website", "")
        rating           = row.get("rating", "")
        for contact in row.get("contacts", []):
            full_name, role, url = _parse_contact(contact)
            first, last = _split_name(full_name)
            writer.writerow([url, first, last, company_name,
                             company_linkedin, company_website, rating, role])
    return out.getvalue()


def to_lemlist_csv(rows: list[dict]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "firstName", "lastName", "email", "companyName",
        "linkedinUrl", "companyDomain", "icebreaker",
    ])
    for row in rows:
        company_name = row.get("name", "")
        website      = row.get("website", "")
        domain       = _extract_domain(website)
        for contact in row.get("contacts", []):
            full_name, _, url = _parse_contact(contact)
            first, last = _split_name(full_name)
            writer.writerow([first, last, "", company_name, url, domain, ""])
    return out.getvalue()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _extract_domain(url: str) -> str:
    if not url or not isinstance(url, str):
        return ""
    url = url.strip().lower()
    for prefix in ("https://", "http://", "www."):
        if url.startswith(prefix):
            url = url[len(prefix):]
    return url.rstrip("/").split("/")[0].split("?")[0]

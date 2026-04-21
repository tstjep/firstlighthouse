"""Campaign configuration schema for salesintel.

A Campaign captures everything domain-specific:
  - what companies to look for (segments + search queries)
  - what buying signals to detect (signal definitions + LLM prompts)
  - how to score companies (rating rules)
  - what contacts to find (role priorities per segment)
  - which region / Google Sheet to use
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

CAMPAIGNS_DIR = Path(__file__).resolve().parent / "campaigns"

_VALID_ID = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]*$')
_VALID_EXPORT_FORMATS = {"waalaxy", "lemlist", "csv"}


# ── Sub-models ─────────────────────────────────────────────────────────────────

class Region(BaseModel):
    label:            str = "United Kingdom"
    country_code:     str = "gb"          # SerpAPI gl= param
    country_restrict: str = "countryGB"   # SerpAPI cr= param
    tld:              str = "co.uk"       # preferred TLD for site: queries


class SearchConfig(BaseModel):
    tld_queries:   list[str] = Field(default_factory=list)
    extra_queries: list[str] = Field(default_factory=list)


class ContactConfig(BaseModel):
    roles: list[str] = Field(default_factory=list)


class Segment(BaseModel):
    """One target audience tab."""
    name:            str
    description:     str = ""
    icp_context:     str = ""
    enrich_context:  str = ""
    signals_enabled: bool = True
    rating_enabled:  bool = True
    search:  SearchConfig  = Field(default_factory=SearchConfig)
    contact: ContactConfig = Field(default_factory=ContactConfig)

    @field_validator("name")
    @classmethod
    def name_no_spaces(cls, v: str) -> str:
        return v.strip().replace(" ", "_")


class Signal(BaseModel):
    """One buying signal to detect per company."""
    key:            str
    name:           str
    description:    str = ""
    llm_definition: str = ""
    keywords:       list[str] = Field(default_factory=list)
    points:         int = 1

    @field_validator("points")
    @classmethod
    def points_in_range(cls, v: int) -> int:
        if not (-10 <= v <= 10):
            raise ValueError("Signal points must be between -10 and 10")
        return v


class RatingConfig(BaseModel):
    contact_threshold: int = 8
    sweet_spot_sizes:  list[str] = Field(default_factory=list)

    @field_validator("contact_threshold")
    @classmethod
    def threshold_in_range(cls, v: int) -> int:
        return max(1, min(10, v))


class LinkedInConfig(BaseModel):
    li_at:     str = ""
    jsessionid: str = ""

    def resolved_li_at(self) -> str:
        return _resolve_env(self.li_at)

    def resolved_jsessionid(self) -> str:
        return _resolve_env(self.jsessionid)


# ── Main model ─────────────────────────────────────────────────────────────────

class Campaign(BaseModel):
    id:              str
    name:            str
    product_context: str = ""
    region:          Region          = Field(default_factory=Region)
    spreadsheet_id:  str = ""
    credentials_file: str = "melt2.json"
    linkedin:        LinkedInConfig  = Field(default_factory=LinkedInConfig)
    export_format:   str = "waalaxy"
    segments:        list[Segment]   = Field(default_factory=list)
    signals:         list[Signal]    = Field(default_factory=list)
    rating:          RatingConfig    = Field(default_factory=RatingConfig)

    @field_validator("id")
    @classmethod
    def id_slug(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Campaign ID cannot be empty")
        if not _VALID_ID.match(v):
            raise ValueError("Campaign ID must start with a letter or digit and contain only letters, digits, hyphens, and underscores")
        return v

    @field_validator("export_format")
    @classmethod
    def valid_export_format(cls, v: str) -> str:
        if v not in _VALID_EXPORT_FORMATS:
            return "waalaxy"
        return v

    # ── Persistence ────────────────────────────────────────────────────────────

    @classmethod
    def load(cls, campaign_id: str, campaigns_dir: Path | None = None) -> "Campaign":
        d    = campaigns_dir or CAMPAIGNS_DIR
        path = d / f"{campaign_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Campaign not found: {path}")
        try:
            return cls.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"Campaign '{campaign_id}' has invalid config: {exc}") from exc

    @classmethod
    def list_all(cls, campaigns_dir: Path | None = None) -> list["Campaign"]:
        d = campaigns_dir or CAMPAIGNS_DIR
        d.mkdir(parents=True, exist_ok=True)
        campaigns = []
        for p in sorted(d.glob("*.json")):
            try:
                campaigns.append(cls.load(p.stem, d))
            except Exception as exc:
                logger.warning("Skipping malformed campaign file %s: %s", p.name, exc)
        return campaigns

    def save(self, campaigns_dir: Path | None = None) -> None:
        d = campaigns_dir or CAMPAIGNS_DIR
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{self.id}.json"
        # Write to temp file first, then rename — prevents partial writes
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)

    def delete(self, campaigns_dir: Path | None = None) -> None:
        d = campaigns_dir or CAMPAIGNS_DIR
        (d / f"{self.id}.json").unlink(missing_ok=True)

    # ── Derived helpers ────────────────────────────────────────────────────────

    def segment(self, name: str) -> Segment:
        for seg in self.segments:
            if seg.name == name:
                return seg
        raise KeyError(f"Segment '{name}' not found in campaign '{self.id}'")

    def segment_names(self) -> list[str]:
        return [s.name for s in self.segments]

    def signal_cols(self) -> dict[str, tuple[str, str]]:
        """Return {signal_key: (bool_col_letter, source_col_letter)}."""
        BASE = 9
        return {
            sig.key: (_col_letter(BASE + i * 2), _col_letter(BASE + i * 2 + 1))
            for i, sig in enumerate(self.signals)
        }

    def signal_col_indices(self) -> dict[str, tuple[int, int]]:
        BASE = 9
        return {
            sig.key: (BASE + i * 2, BASE + i * 2 + 1)
            for i, sig in enumerate(self.signals)
        }

    def contacts_col_idx(self) -> int:
        return 9 + len(self.signals) * 2

    def signal_tab_names(self) -> set[str]:
        return {s.name for s in self.segments if s.signals_enabled}

    def rating_tab_names(self) -> set[str]:
        return {s.name for s in self.segments if s.rating_enabled}

    def all_tab_names(self) -> list[str]:
        return [s.name for s in self.segments]

    def serp_params(self) -> dict[str, str]:
        return {"gl": self.region.country_code, "cr": self.region.country_restrict}

    def sheet_range_end(self) -> str:
        return _col_letter(self.contacts_col_idx())

    def all_signal_col_indices(self) -> list[int]:
        BASE = 9
        return [BASE + i * 2 for i in range(len(self.signals))]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _col_letter(idx: int) -> str:
    """Convert 0-based column index to spreadsheet letter (A, B, …, Z, AA, …)."""
    result = ""
    n = idx + 1
    while n:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


def _resolve_env(value: str) -> str:
    if not value:
        return ""
    if value.startswith("$"):
        env_key = value[1:]
        resolved = os.environ.get(env_key, "")
        if not resolved:
            logger.warning("Environment variable %s is not set", env_key)
        return resolved
    return value

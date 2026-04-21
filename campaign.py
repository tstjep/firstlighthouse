"""Campaign configuration schema for firstlighthouse.

A Campaign captures everything domain-specific:
  - what companies to look for (search queries)
  - what buying signals to detect (signal definitions + LLM prompts)
  - how to score companies (rating rules)
  - what contacts to find (role priorities)
  - which region to use
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
    label:            str = "Switzerland"
    country_code:     str = "ch"
    country_restrict: str = "countryCH"
    tld:              str = "ch"


class SearchConfig(BaseModel):
    tld_queries:   list[str] = Field(default_factory=list)
    extra_queries: list[str] = Field(default_factory=list)


class ContactConfig(BaseModel):
    roles: list[str] = Field(default_factory=list)


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
    region:          Region        = Field(default_factory=Region)
    linkedin:        LinkedInConfig = Field(default_factory=LinkedInConfig)
    export_format:   str = "waalaxy"
    search:          SearchConfig  = Field(default_factory=SearchConfig)
    contact:         ContactConfig = Field(default_factory=ContactConfig)
    signals:         list[Signal]  = Field(default_factory=list)
    rating:          RatingConfig  = Field(default_factory=RatingConfig)

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
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)

    def delete(self, campaigns_dir: Path | None = None) -> None:
        d = campaigns_dir or CAMPAIGNS_DIR
        (d / f"{self.id}.json").unlink(missing_ok=True)

    # ── Derived helpers ────────────────────────────────────────────────────────

    def serp_params(self) -> dict[str, str]:
        return {"gl": self.region.country_code, "cr": self.region.country_restrict}


# ── Helpers ────────────────────────────────────────────────────────────────────

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

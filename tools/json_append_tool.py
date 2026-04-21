"""Nanobot tool: append a discovered company to the local JSON store."""

from datetime import date
from typing import Any

from nanobot.agent.tools.base import Tool
from store import ResultStore


class JsonAppendTool(Tool):
    """Append a company to the campaign result store (deduplicates automatically)."""

    def __init__(self, store: ResultStore, segment: str):
        self._store   = store
        self._segment = segment

    @property
    def name(self) -> str:
        return "sheets_append_company"   # kept for prompt compat with search agent

    @property
    def description(self) -> str:
        return (
            "Record a discovered company. Duplicates (same name or domain) are "
            "skipped automatically. Provide at minimum the company name."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "company_name": {"type": "string", "description": "Legal or trading name"},
                "website":      {"type": "string", "description": "Primary website URL"},
                "linkedin":     {"type": "string", "description": "LinkedIn company page URL"},
                "notes":        {"type": "string", "description": "One-sentence description"},
                "size":         {"type": "string", "description": "Employee count range e.g. '11-50'"},
                "hq_location":  {"type": "string", "description": "City, Country e.g. 'London, UK'"},
            },
            "required": ["company_name"],
        }

    async def execute(
        self,
        company_name: str,
        website: str = "",
        linkedin: str = "",
        notes: str = "",
        size: str = "",
        hq_location: str = "",
        **_: Any,
    ) -> str:
        added = self._store.append_company(self._segment, {
            "name":       company_name,
            "website":    website,
            "linkedin":   linkedin,
            "notes":      notes,
            "size":       size,
            "hq":         hq_location,
            "date_added": date.today().isoformat(),
        })
        if added:
            return f"Added: {company_name}"
        return f"Skipped (duplicate): {company_name}"

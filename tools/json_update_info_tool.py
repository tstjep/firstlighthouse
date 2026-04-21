"""Nanobot tool: update company info fields in the local JSON store."""

from typing import Any

from nanobot.agent.tools.base import Tool
from store import ResultStore


class JsonUpdateInfoTool(Tool):
    """Update enriched info fields for a company row in the result store."""

    def __init__(self, store: ResultStore, segment: str):
        self._store   = store
        self._segment = segment

    @property
    def name(self) -> str:
        return "update_company_info"

    @property
    def description(self) -> str:
        return (
            "Update company information fields (website, LinkedIn, size, HQ, notes) "
            "for a row identified by row_index. Only pass fields you are confident about."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "row_index":    {"type": "integer", "description": "Row identifier (from the company list)"},
                "company_name": {"type": "string"},
                "website":      {"type": "string"},
                "linkedin":     {"type": "string"},
                "size":         {"type": "string", "description": "'1-10', '11-50', '51-200', '201-500', '501-1000', '1000+'"},
                "hq_location":  {"type": "string", "description": "e.g. 'London, UK'"},
                "notes":        {"type": "string", "description": "One-sentence description"},
            },
            "required": ["row_index", "company_name"],
        }

    async def execute(
        self,
        row_index: int,
        company_name: str,
        website: str = "",
        linkedin: str = "",
        size: str = "",
        hq_location: str = "",
        notes: str = "",
        **_: Any,
    ) -> str:
        fields: dict[str, Any] = {"name": company_name}
        if website:    fields["website"]  = website
        if linkedin:   fields["linkedin"] = linkedin
        if size:       fields["size"]     = size
        if hq_location: fields["hq"]     = hq_location
        if notes:      fields["notes"]    = notes

        ok = self._store.update_company(self._segment, row_index, fields)
        if ok:
            return f"Updated row {row_index}: {company_name}"
        return f"Row {row_index} not found"

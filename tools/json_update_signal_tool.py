"""Nanobot tool: write a signal result to the local JSON store."""

from typing import Any

from nanobot.agent.tools.base import Tool
from store import ResultStore


class JsonUpdateSignalTool(Tool):
    """Update one buying signal for a company row in the result store."""

    def __init__(self, store: ResultStore, segment: str, valid_signals: list[str]):
        self._store         = store
        self._segment       = segment
        self._valid_signals = valid_signals

    @property
    def name(self) -> str:
        return "update_signal"

    @property
    def description(self) -> str:
        return (
            "Update one buying signal for a company row. "
            "Provide the row_index, the signal name, whether it was detected, "
            "and a short source note. "
            f"Signal names: {', '.join(repr(s) for s in self._valid_signals)}."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "row_index": {"type": "integer", "minimum": 1},
                "signal":    {"type": "string", "enum": self._valid_signals},
                "detected":  {"type": "boolean"},
                "source":    {"type": "string", "description": "Source snippet/URL or 'not found'"},
            },
            "required": ["row_index", "signal", "detected", "source"],
        }

    async def execute(
        self,
        row_index: int,
        signal: str,
        detected: bool,
        source: str = "",
        **_: Any,
    ) -> str:
        if signal not in self._valid_signals:
            return f"Unknown signal {signal!r}. Valid: {self._valid_signals}"
        value = "Yes" if detected else "No"
        ok = self._store.update_signal(self._segment, row_index, signal, value, source or "not found")
        if ok:
            return f"Row {row_index}: {signal} = {value}"
        return f"Row {row_index} not found"

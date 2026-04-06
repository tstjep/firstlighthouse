"""SerpAPI search tool for nanobot agents."""

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from nanobot.agent.tools.base import Tool

CACHE_DIR = Path(__file__).resolve().parent.parent / "serp_cache"


def _cache_path(query: str, num: int) -> Path:
    """Return the JSON cache file path for a given query + num."""
    slug = re.sub(r"[^\w\-]", "_", query)[:60]
    key = hashlib.md5(f"{query}|{num}".encode()).hexdigest()[:8]
    filename = f"{date.today().isoformat()}_{slug}_{key}.json"
    return CACHE_DIR / filename


class SerpSearchTool(Tool):
    """Search the web via SerpAPI (Google Search engine)."""

    def __init__(self, api_key: str, gl: str | None = None, cr: str | None = None):
        """
        api_key: SerpAPI key
        gl:      Google country code for geolocation, e.g. "ch", "de", "at"
        cr:      Google country restrict, e.g. "countryCH", "countryDE", "countryAT"
        """
        self._api_key = api_key
        self._gl = gl
        self._cr = cr
        CACHE_DIR.mkdir(exist_ok=True)

    @property
    def name(self) -> str:
        return "serp_search"

    @property
    def description(self) -> str:
        return (
            "Search Google via SerpAPI and return organic results (title, URL, snippet). "
            "Use this to discover IT infrastructure companies, verify company details, "
            "or find LinkedIn pages."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Google search query string",
                    "minLength": 2,
                },
                "num": {
                    "type": "integer",
                    "description": "Number of organic results to return (1-10)",
                    "minimum": 1,
                    "maximum": 10,
                },
            },
            "required": ["query"],
        }

    async def execute(self, query: str, num: int = 10, **kwargs: Any) -> str:
        if not self._api_key:
            return "[serp_search error] SERPAPI_KEY is not set in config.py — cannot perform searches"

        params = {
            "q": query,
            "api_key": self._api_key,
            "num": num,
            "engine": "google",
        }
        if self._gl:
            params["gl"] = self._gl
        if self._cr:
            params["cr"] = self._cr

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get("https://serpapi.com/search", params=params)
                if response.status_code == 401:
                    return (
                        "[serp_search error] SerpAPI returned 401 Unauthorized — "
                        "check that SERPAPI_KEY in config.py is valid"
                    )
                if response.status_code == 429:
                    return (
                        "[serp_search error] SerpAPI rate limit hit (429) — "
                        "too many requests, wait before retrying"
                    )
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException:
            return f"[serp_search error] Request timed out for query: {query!r} — check network or SerpAPI status"
        except httpx.ConnectError as exc:
            return f"[serp_search error] Connection failed for query: {query!r} — {exc}"
        except httpx.HTTPStatusError as exc:
            return f"[serp_search error] HTTP {exc.response.status_code} for query: {query!r}"

        # Save full raw response for later reuse (non-fatal if it fails)
        try:
            cache_file = _cache_path(query, num)
            cache_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        except OSError:
            pass

        organic = data.get("organic_results", [])
        if not organic:
            return f"No results found for: {query}"

        lines = []
        for i, r in enumerate(organic[:num], 1):
            title = r.get("title", "")
            link = r.get("link", "")
            snippet = r.get("snippet", "")
            lines.append(f"{i}. {title}\n   URL: {link}\n   {snippet}")

        return "\n\n".join(lines)

"""
LLM-powered signal suggestions for salesintel.

Given an ICP description, runs two parallel LLM calls:
  1. Suggest 1-3 positive buying signals
  2. Suggest 1-2 negative/exclusion signals

Returns a list of Signal-shaped dicts ready to render in the UI.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)

# ── Prompts ───────────────────────────────────────────────────────────────────

_SYSTEM = """\
You are a B2B sales intelligence assistant helping a user configure a company-prospecting tool.
Your job is to suggest buying signals — observable clues that indicate a company is a good fit
for the user's product.

Respond ONLY with valid JSON. No prose, no markdown fences, no explanation outside the JSON.\
"""

_POSITIVE_PROMPT = """\
The user's ICP (Ideal Customer Profile) is:
---
{icp}
---

Suggest {n} buying signals that would indicate a strong fit for this ICP.
Each signal should be detectable from a company's website, LinkedIn profile, or public job postings.

Return a JSON array of objects, each with these exact keys:
  "name"           — short display name (2-4 words, title case)
  "key"            — snake_case identifier, unique, no spaces
  "description"    — one sentence (under 15 words) for a column header tooltip
  "llm_definition" — 2-3 sentences instructing an AI analyst when to mark Yes vs No.
                     Be specific about what to look for and what to ignore.
  "keywords"       — list of 3-6 search keywords to find evidence of this signal
  "points"         — integer 1-3 reflecting how strong an indicator this is

Example output format (replace with real suggestions):
[
  {{
    "name": "Active Hiring",
    "key": "active_hiring",
    "description": "Company is actively growing its team",
    "llm_definition": "Mark Yes if the company has open job postings in the last 90 days, particularly in roles related to the buyer's domain. Mark No if no recent job postings are found or if the company shows signs of a hiring freeze.",
    "keywords": ["hiring", "careers", "join our team", "open roles"],
    "points": 2
  }}
]
\
"""

_NEGATIVE_PROMPT = """\
The user's ICP (Ideal Customer Profile) is:
---
{icp}
---

Suggest {n} negative signals — red flags that would indicate a company is a POOR fit or
should be deprioritised (e.g. wrong size, already has a competitor solution, or wrong market).

Return a JSON array of objects, each with these exact keys:
  "name"           — short display name (2-4 words, title case)
  "key"            — snake_case identifier, unique, starts with "no_" or "avoid_" or similar
  "description"    — one sentence (under 15 words) for a column header tooltip
  "llm_definition" — 2-3 sentences instructing an AI analyst when to mark Yes vs No.
                     Mark Yes means the red flag IS present (bad fit). Be specific.
  "keywords"       — list of 3-6 keywords that would surface this red flag
  "points"         — negative integer -1 to -3 (more negative = stronger exclusion)

Example output format (replace with real suggestions):
[
  {{
    "name": "Enterprise Only",
    "key": "avoid_enterprise",
    "description": "Company is too large for our ICP",
    "llm_definition": "Mark Yes if the company has more than 1,000 employees, serves Fortune 500 clients exclusively, or their website emphasises enterprise or global scale. Mark No if the company appears to be an SMB or mid-market player.",
    "keywords": ["enterprise", "Fortune 500", "global", "1000+ employees"],
    "points": -2
  }}
]
\
"""


_MORE_POSITIVE_PROMPT = """\
The user's ICP (Ideal Customer Profile) is:
---
{icp}
---

They have already configured these buying signals:
{existing}

Suggest {n} ADDITIONAL positive buying signals that would be useful for this ICP
and that are NOT already covered by the existing signals above.
Focus on angles the user might not have thought of.

Return a JSON array with the same keys as before:
  "name", "key", "description", "llm_definition", "keywords", "points"

Rules:
- Do not suggest signals that overlap with the existing ones
- "key" must be unique and different from existing keys
- points: integer 1-3
\
"""

_MORE_NEGATIVE_PROMPT = """\
The user's ICP (Ideal Customer Profile) is:
---
{icp}
---

They have already configured these signals (including any exclusion signals):
{existing}

Suggest {n} ADDITIONAL negative/exclusion signals — red flags not already covered.
These should identify companies that look like a fit on the surface but aren't.

Return a JSON array with the same keys:
  "name", "key", "description", "llm_definition", "keywords", "points"

Rules:
- Do not suggest signals that overlap with existing ones
- "key" must be unique and different from existing keys
- points: negative integer -1 to -3
\
"""


# ── Provider ─────────────────────────────────────────────────────────────────

def _get_provider():
    """Thin wrapper so tests can patch suggest_signals._get_provider."""
    from agents.provider import build_provider
    return build_provider()


# ── Core ──────────────────────────────────────────────────────────────────────

def _extract_json_array(text: str) -> list[dict]:
    """Extract a JSON array from LLM output, tolerating minor formatting issues."""
    text = text.strip()

    # Strip markdown code fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    # Find the first '[' and last ']'
    start = text.find("[")
    end   = text.rfind("]")
    if start == -1 or end == -1:
        logger.warning("No JSON array found in LLM output: %r", text[:200])
        return []

    try:
        result = json.loads(text[start:end + 1])
        if not isinstance(result, list):
            return []
        return result
    except json.JSONDecodeError as exc:
        logger.warning("JSON parse error in LLM signal suggestion: %s | text: %r", exc, text[:300])
        return []


def _normalise(signal: dict, expected_positive: bool) -> dict | None:
    """
    Validate and normalise a signal dict from the LLM. Returns None if unusable.

    AI pitfall guards applied here:
    - Name/key missing or whitespace-only → discard (LLM sometimes returns placeholder text)
    - Key sanitised: lowercase, spaces→underscores, non-alphanumeric stripped
    - Points sign enforced: LLM sometimes ignores the sign instruction
    - Points clamped to [-3, 3]: LLM sometimes returns extreme values like 10 or -100
    - Keywords coerced: LLM sometimes returns a comma string instead of a list
    - Overly long llm_definition truncated: protects downstream prompt budgets
    - Key uniqueness collision (e.g. "signal_1", "signal_2") is a caller responsibility
    """
    name = str(signal.get("name", "")).strip()
    raw_key = str(signal.get("key", "")).strip().lower()

    if not name or not raw_key:
        return None

    # Sanitise key: keep only alphanumeric + underscores, collapse runs of underscores
    import re as _re
    key = _re.sub(r"[^a-z0-9_]", "_", raw_key.replace(" ", "_"))
    key = _re.sub(r"_+", "_", key).strip("_")
    if not key:
        return None

    # Guard: reject generic/placeholder names the LLM sometimes emits
    _PLACEHOLDER_NAMES = {"signal", "example", "placeholder", "your signal here", "tbd", "n/a"}
    if name.lower() in _PLACEHOLDER_NAMES or raw_key in _PLACEHOLDER_NAMES:
        logger.debug("Discarding placeholder signal name/key: %r / %r", name, raw_key)
        return None

    try:
        points = int(signal.get("points", 1 if expected_positive else -1))
    except (TypeError, ValueError):
        points = 1 if expected_positive else -1

    # Enforce sign convention (LLM occasionally ignores the sign instruction)
    if expected_positive and points <= 0:
        points = 1
    if not expected_positive and points >= 0:
        points = -1

    # Clamp to reasonable range
    points = max(-3, min(3, points))

    # Coerce keywords (LLM sometimes returns a comma-string instead of a list)
    keywords = signal.get("keywords", [])
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",") if k.strip()]
    elif not isinstance(keywords, list):
        keywords = []
    keywords = [str(k).strip() for k in keywords if k and str(k).strip()][:10]

    # Truncate overly long fields that could blow out downstream LLM prompts
    description    = str(signal.get("description",    "")).strip()[:200]
    llm_definition = str(signal.get("llm_definition", "")).strip()[:800]

    return {
        "name":           name[:80],
        "key":            key,
        "description":    description,
        "llm_definition": llm_definition,
        "keywords":       keywords,
        "points":         points,
    }


async def _call(provider, model: str, prompt: str) -> str:
    try:
        response = await provider.chat(
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": prompt},
            ],
            model=model,
            max_tokens=1024,
            temperature=0.7,
        )
        return response.content or ""
    except Exception as exc:
        logger.error("LLM call failed in suggest_signals: %s", exc)
        return ""


async def suggest(product_context: str) -> list[dict]:
    """
    Run two parallel LLM calls and return a combined list of signal suggestions.
    Positive signals come first, then negative.
    Returns [] if no provider is configured or LLM calls fail.
    """
    if not product_context.strip():
        return []

    try:
        provider, model = _get_provider()
    except SystemExit:
        logger.warning("No LLM provider configured — signal suggestions unavailable.")
        return []
    except Exception as exc:
        logger.error("Failed to build LLM provider: %s", exc)
        return []

    pos_prompt = _POSITIVE_PROMPT.format(icp=product_context.strip(), n=3)
    neg_prompt = _NEGATIVE_PROMPT.format(icp=product_context.strip(), n=2)

    pos_text, neg_text = await asyncio.gather(
        _call(provider, model, pos_prompt),
        _call(provider, model, neg_prompt),
    )

    positive = [
        s for raw in _extract_json_array(pos_text)
        if (s := _normalise(raw, expected_positive=True)) is not None
    ]
    negative = [
        s for raw in _extract_json_array(neg_text)
        if (s := _normalise(raw, expected_positive=False)) is not None
    ]

    return positive + negative


async def suggest_more(product_context: str, existing_signals: list[dict]) -> list[dict]:
    """
    Suggest additional signals given an ICP and existing signals.
    Filters out any returned signals whose key matches an existing one.
    """
    if not product_context.strip():
        return []

    existing_keys = {str(s.get("key", "")).lower() for s in existing_signals}

    # Build a readable summary of existing signals for the prompt
    existing_summary = "\n".join(
        f"- {s.get('name', s.get('key', '?'))} (key: {s.get('key', '?')})"
        for s in existing_signals
    ) or "None yet."

    try:
        provider, model = _get_provider()
    except SystemExit:
        logger.warning("No LLM provider configured — signal suggestions unavailable.")
        return []
    except Exception as exc:
        logger.error("Failed to build LLM provider: %s", exc)
        return []

    pos_prompt = _MORE_POSITIVE_PROMPT.format(
        icp=product_context.strip(), existing=existing_summary, n=2
    )
    neg_prompt = _MORE_NEGATIVE_PROMPT.format(
        icp=product_context.strip(), existing=existing_summary, n=1
    )

    pos_text, neg_text = await asyncio.gather(
        _call(provider, model, pos_prompt),
        _call(provider, model, neg_prompt),
    )

    positive = [
        s for raw in _extract_json_array(pos_text)
        if (s := _normalise(raw, expected_positive=True)) is not None
        and s["key"] not in existing_keys
    ]
    negative = [
        s for raw in _extract_json_array(neg_text)
        if (s := _normalise(raw, expected_positive=False)) is not None
        and s["key"] not in existing_keys
    ]

    return positive + negative

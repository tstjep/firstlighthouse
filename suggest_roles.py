"""
LLM-powered contact role suggestions for salesintel.

Given an ICP description and the campaign's buying signals, runs two parallel LLM calls:
  1. Suggest buyer roles (economic decision-makers who sign / approve the purchase)
  2. Suggest user roles (day-to-day users who influence or champion the purchase)

Returns a list of role suggestion dicts:
  {
    "role":        str,   # job title / role name
    "role_type":   str,   # "buyer" | "user"
    "rationale":   str,   # one sentence: why this role matters for the ICP
  }
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
You are a B2B sales intelligence assistant. Your job is to suggest which job titles
to target at prospect companies for a given product and ICP.

Respond ONLY with valid JSON. No prose, no markdown fences, no explanation outside the JSON.\
"""

_BUYER_PROMPT = """\
The user's ICP (Ideal Customer Profile) is:
---
{icp}
---

{signals_block}

Suggest {n} BUYER roles — the people who own the budget, sign the contract, or formally
approve the purchase. These are economic decision-makers, not day-to-day users.

Return a JSON array of objects, each with these exact keys:
  "role"      — job title as it would appear on LinkedIn (2-5 words, title case)
  "role_type" — always the string "buyer"
  "rationale" — one sentence (under 20 words) explaining why this role makes the buying decision

Rules:
- Use real LinkedIn job titles, not generic terms like "Decision Maker"
- Think about the actual org structure at companies matching this ICP
- Consider both founders/owners at small companies and functional heads at larger ones

Example output format (replace with real suggestions):
[
  {{
    "role": "Managing Partner",
    "role_type": "buyer",
    "rationale": "Owns the firm's technology budget and approves all software contracts."
  }}
]
\
"""

_USER_PROMPT = """\
The user's ICP (Ideal Customer Profile) is:
---
{icp}
---

{signals_block}

Suggest {n} USER roles — the people who will use the product day-to-day and are likely
to champion or block the purchase. These are not necessarily the budget holders.

Return a JSON array of objects, each with these exact keys:
  "role"      — job title as it would appear on LinkedIn (2-5 words, title case)
  "role_type" — always the string "user"
  "rationale" — one sentence (under 20 words) explaining how they use or are affected by the product

Rules:
- Use real LinkedIn job titles
- Focus on roles that interact directly with the pain point described in the ICP
- These often influence the buying decision even if they don't sign the contract

Example output format:
[
  {{
    "role": "HR Manager",
    "role_type": "user",
    "rationale": "Manages the day-to-day HR workflows the product directly replaces."
  }}
]
\
"""


def _signals_block(signals: list[dict]) -> str:
    if not signals:
        return ""
    lines = [
        f"  - {s.get('name', s.get('key', '?'))}: {s.get('description', '')}"
        for s in signals
        if s.get('points', 0) > 0  # only positive signals for context
    ]
    if not lines:
        return ""
    return "Buying signals already configured:\n" + "\n".join(lines) + "\n"


# ── Provider ──────────────────────────────────────────────────────────────────

def _get_provider():
    """Thin wrapper so tests can patch suggest_roles._get_provider."""
    from agents.provider import build_provider
    return build_provider()


# ── Core ──────────────────────────────────────────────────────────────────────

def _extract_json_array(text: str) -> list[dict]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("[")
    end   = text.rfind("]")
    if start == -1 or end == -1:
        logger.warning("No JSON array found in role suggestion output: %r", text[:200])
        return []
    try:
        result = json.loads(text[start:end + 1])
        if not isinstance(result, list):
            return []
        return result
    except json.JSONDecodeError as exc:
        logger.warning("JSON parse error in role suggestion: %s | text: %r", exc, text[:300])
        return []


_PLACEHOLDER_ROLES = {
    "role", "example", "placeholder", "your role here", "tbd", "n/a",
    "job title", "title", "position",
}
_VALID_ROLE_TYPES = {"buyer", "user"}


def _normalise(raw: dict, expected_type: str) -> dict | None:
    """Validate and normalise a role suggestion dict. Returns None if unusable."""
    role = str(raw.get("role", "")).strip()
    if not role or role.lower() in _PLACEHOLDER_ROLES:
        return None

    # Always enforce expected_type — LLM often ignores this instruction
    role_type = expected_type

    rationale = str(raw.get("rationale", "")).strip()[:200]

    return {
        "role":      role[:80],
        "role_type": role_type,
        "rationale": rationale,
    }


async def _call(provider, model: str, prompt: str) -> str:
    try:
        response = await provider.chat(
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": prompt},
            ],
            model=model,
            max_tokens=512,
            temperature=0.7,
        )
        return response.content or ""
    except Exception as exc:
        logger.error("LLM call failed in suggest_roles: %s", exc)
        return ""


async def suggest(
    product_context: str,
    existing_signals: list[dict] | None = None,
) -> list[dict]:
    """
    Run two parallel LLM calls and return combined role suggestions.
    Buyer roles come first, then user roles.
    Returns [] if no provider is configured or LLM calls fail.
    """
    if not product_context.strip():
        return []

    try:
        provider, model = _get_provider()
    except SystemExit:
        logger.warning("No LLM provider configured — role suggestions unavailable.")
        return []
    except Exception as exc:
        logger.error("Failed to build LLM provider: %s", exc)
        return []

    sig_block = _signals_block(existing_signals or [])

    buyer_prompt = _BUYER_PROMPT.format(icp=product_context.strip(), signals_block=sig_block, n=3)
    user_prompt  = _USER_PROMPT.format(icp=product_context.strip(), signals_block=sig_block, n=3)

    buyer_text, user_text = await asyncio.gather(
        _call(provider, model, buyer_prompt),
        _call(provider, model, user_prompt),
    )

    buyers = [
        s for raw in _extract_json_array(buyer_text)
        if (s := _normalise(raw, expected_type="buyer")) is not None
    ]
    users = [
        s for raw in _extract_json_array(user_text)
        if (s := _normalise(raw, expected_type="user")) is not None
    ]

    return buyers + users

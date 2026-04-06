"""Shared LLM provider factory for all agents.

Priority order:
  1. VERTEX_PROJECT in config  (Vertex AI via service account)
  2. ANTHROPIC_API_KEY env var (Anthropic direct)
  3. LLM_API_KEY env var       (any LiteLLM-compatible key)

Override model via LLM_MODEL env var.
Default model: config.DEFAULT_MODEL
"""

import os
import sys
from pathlib import Path
from typing import Any

import config as cfg
from nanobot.providers.base import LLMResponse
from nanobot.providers.litellm_provider import LiteLLMProvider

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class _VertexProvider(LiteLLMProvider):
    """LiteLLMProvider variant that skips nanobot's model-prefix logic and
    logs token usage / finish_reason after every LLM call.

    Nanobot's registry matches "gemini" inside "vertex_ai/gemini-*" and
    re-prefixes the model as "gemini/vertex_ai/…", which breaks routing.
    Overriding _resolve_model returns the model unchanged so LiteLLM
    receives the correct "vertex_ai/<model>" string.
    """

    def _resolve_model(self, model: str) -> str:
        return model

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        response = await super().chat(
            messages=messages,
            tools=tools,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        usage = response.usage or {}
        prompt_tok = usage.get("prompt_tokens", "?")
        completion_tok = usage.get("completion_tokens", "?")
        finish = response.finish_reason or "?"
        overflow = finish == "length"
        flag = "  *** TOKEN LIMIT HIT ***" if overflow else ""
        print(
            f"[llm] finish={finish}  tokens:"
            f" prompt={prompt_tok}  completion={completion_tok}/{max_tokens}{flag}"
        )
        if overflow:
            print(
                f"[llm] Model hit max_tokens={max_tokens} — "
                "increase max_tokens in AgentLoop to fix empty responses."
            )
        return response


def build_provider() -> tuple[LiteLLMProvider, str]:
    """Return (provider, model_name) ready to pass to AgentLoop."""
    model = os.environ.get("LLM_MODEL", cfg.DEFAULT_MODEL)

    # 1. Vertex AI via service account JSON
    if cfg.VERTEX_PROJECT:
        os.environ["VERTEXAI_PROJECT"] = cfg.VERTEX_PROJECT
        os.environ["VERTEXAI_LOCATION"] = cfg.VERTEX_LOCATION
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(PROJECT_ROOT / cfg.CREDENTIALS_FILE)
        return _VertexProvider(default_model=model), model

    # 2. Direct Anthropic
    if key := os.environ.get("ANTHROPIC_API_KEY"):
        return LiteLLMProvider(api_key=key, default_model=model), model

    # 3. Generic fallback
    if key := os.environ.get("LLM_API_KEY"):
        return LiteLLMProvider(api_key=key, default_model=model), model

    print(
        "Error: no LLM provider configured. Set VERTEX_PROJECT in config.py, "
        "or set ANTHROPIC_API_KEY / LLM_API_KEY env vars.",
        file=sys.stderr,
    )
    sys.exit(1)

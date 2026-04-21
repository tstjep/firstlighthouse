"""Shared LLM provider factory for all agents.

Priority order:
  1. VERTEX_PROJECT in config  (Vertex AI via service account → OpenAI-compat endpoint)
  2. ANTHROPIC_API_KEY env var
  3. LLM_API_KEY env var       (OpenAI-compatible fallback)

Override model via LLM_MODEL env var.
Default model: config.DEFAULT_MODEL
"""

import os
import sys
from pathlib import Path
from typing import Any

import config as cfg
from nanobot.providers.base import LLMProvider, LLMResponse
from nanobot.providers.anthropic_provider import AnthropicProvider
from nanobot.providers.openai_compat_provider import OpenAICompatProvider

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Vertex AI exposes an OpenAI-compatible endpoint per project/location
_VERTEX_API_BASE = (
    "https://{location}-aiplatform.googleapis.com/v1/projects/{project}"
    "/locations/{location}/endpoints/openapi"
)

# Vertex AI model names differ from the LiteLLM "vertex_ai/" prefix style
_VERTEX_MODEL_MAP = {
    "vertex_ai/gemini-2.5-flash": "google/gemini-2.5-flash",
    "vertex_ai/gemini-2.5-pro":   "google/gemini-2.5-pro",
    "vertex_ai/gemini-2.0-flash": "google/gemini-2.0-flash",
    "vertex_ai/gemini-1.5-flash": "google/gemini-1.5-flash",
    "vertex_ai/gemini-1.5-pro":   "google/gemini-1.5-pro",
}


def _vertex_access_token(credentials_file: str) -> str:
    """Obtain a short-lived OAuth2 bearer token from a service account JSON key."""
    from google.oauth2.service_account import Credentials
    import google.auth.transport.requests

    creds = Credentials.from_service_account_file(
        credentials_file,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


class _VertexProvider(OpenAICompatProvider):
    """Routes to Vertex AI's OpenAI-compatible endpoint using a service account.

    Logs token usage and finish_reason after every call.
    """

    def __init__(self, credentials_file: str, project: str, location: str, default_model: str):
        token = _vertex_access_token(credentials_file)
        api_base = _VERTEX_API_BASE.format(project=project, location=location)
        self._vertex_model = _VERTEX_MODEL_MAP.get(default_model, default_model)
        super().__init__(api_key=token, api_base=api_base, default_model=self._vertex_model)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,  # noqa: ARG002 — always overridden by _vertex_model
        max_tokens: int = 4096,
        temperature: float = 0.7,
        **kwargs,
    ) -> LLMResponse:
        resolved_model = self._vertex_model
        response = await super().chat(
            messages=messages,
            tools=tools,
            model=resolved_model,
            max_tokens=max_tokens,
            temperature=temperature,
            **kwargs,
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


class _DebugProvider(LLMProvider):
    """Wraps any provider and dumps every prompt to stdout when DEBUG_PROMPTS=1."""

    def __init__(self, inner: LLMProvider):
        super().__init__()
        self._inner = inner

    def get_default_model(self) -> str:
        return self._inner.get_default_model()

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        **kwargs,
    ) -> LLMResponse:
        sep = "─" * 60
        print(f"\n{sep}")
        print(f"[prompt] model={model or self.default_model}  max_tokens={max_tokens}  temp={temperature}")
        for i, msg in enumerate(messages):
            role = msg.get("role", "?")
            content = msg.get("content", "")
            if isinstance(content, list):
                # tool results / multi-part content
                content = " ".join(
                    p.get("text", str(p)) if isinstance(p, dict) else str(p)
                    for p in content
                )
            preview = content[:2000] + ("…" if len(content) > 2000 else "")
            print(f"[{i}] {role.upper()}:\n{preview}")
        print(sep)
        response = await self._inner.chat(
            messages=messages,
            tools=tools,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            **kwargs,
        )
        print(f"[response] finish={response.finish_reason}  content preview: {str(response.content)[:300]}")
        print(sep + "\n")
        return response


def build_provider() -> tuple[LLMProvider, str]:
    """Return (provider, model_name) ready to pass to AgentLoop."""
    model = os.environ.get("LLM_MODEL", cfg.DEFAULT_MODEL)
    _debug = os.environ.get("DEBUG_PROMPTS", "").strip() == "1"

    # 1. Vertex AI via service account JSON
    if cfg.VERTEX_PROJECT:
        credentials_file = str(PROJECT_ROOT / cfg.CREDENTIALS_FILE)
        vertex_model = _VERTEX_MODEL_MAP.get(model, model)
        provider = _VertexProvider(
            credentials_file=credentials_file,
            project=cfg.VERTEX_PROJECT,
            location=cfg.VERTEX_LOCATION,
            default_model=model,
        )
        return (_DebugProvider(provider), vertex_model) if _debug else (provider, vertex_model)

    # 2. Direct Anthropic
    if key := os.environ.get("ANTHROPIC_API_KEY"):
        provider = AnthropicProvider(api_key=key, default_model=model)
        return (_DebugProvider(provider), model) if _debug else (provider, model)

    # 3. Generic OpenAI-compat fallback
    if key := os.environ.get("LLM_API_KEY"):
        provider = OpenAICompatProvider(api_key=key, default_model=model)
        return (_DebugProvider(provider), model) if _debug else (provider, model)

    print(
        "Error: no LLM provider configured. Set VERTEX_PROJECT in config.py, "
        "or set ANTHROPIC_API_KEY / LLM_API_KEY env vars.",
        file=sys.stderr,
    )
    sys.exit(1)

"""LLM providers: which API to talk to and how.

Two wire protocols cover every backend:

- ``openai``   — OpenAI-compatible ``/chat/completions`` (DeepSeek, OpenAI, and
  local servers like Ollama / LM Studio / vLLM — they differ only in
  ``base_url`` / ``model`` / key).
- ``anthropic`` — Claude ``/v1/messages``.

A provider is a named preset (protocol + default base_url + default model).
Pick one with ``$LLM_PROVIDER`` (or ``pr-reviewer provider <name>``);
``$LLM_BASE_URL`` / ``$LLM_MODEL`` override the preset when set. The reviewer
core stays provider-agnostic — it receives the resolved values.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    name: str
    kind: str            # wire protocol: "openai" | "anthropic"
    base_url: str        # default endpoint base
    default_model: str
    json_mode: bool = True  # send response_format=json_object (openai kind only)
    needs_key: bool = True  # local servers usually don't require an API key


# Built-in provider presets.
PROVIDERS: dict[str, Provider] = {
    "deepseek": Provider("deepseek", "openai", "https://api.deepseek.com", "deepseek-chat"),
    "openai": Provider("openai", "openai", "https://api.openai.com/v1", "gpt-4o-mini"),
    "claude": Provider("claude", "anthropic", "https://api.anthropic.com", "claude-sonnet-5", json_mode=False),
    # Ollama's OpenAI-compatible endpoint. Point base_url at LM Studio / vLLM instead if you use those.
    "local": Provider("local", "openai", "http://localhost:11434/v1", "qwen2.5-coder", json_mode=False, needs_key=False),
}

DEFAULT_PROVIDER = "deepseek"


def get_provider(name: str | None) -> Provider:
    return PROVIDERS.get((name or DEFAULT_PROVIDER).lower(), PROVIDERS[DEFAULT_PROVIDER])


def available_providers() -> list[str]:
    return list(PROVIDERS)


def is_known(name: str) -> bool:
    return name.lower() in PROVIDERS


@dataclass(frozen=True)
class LLMConfig:
    """Concrete, ready-to-call LLM configuration (preset merged with overrides)."""
    provider: str
    kind: str
    base_url: str
    model: str
    api_key: str
    json_mode: bool
    needs_key: bool


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() not in ("false", "0", "no", "off")


def resolve(
    *,
    provider: str | None = None,
    api_key: str = "",
    base_url: str = "",
    model: str = "",
    json_mode: str | None = None,
) -> LLMConfig:
    """Merge a provider preset with explicit overrides into a concrete config.

    Empty ``base_url`` / ``model`` fall back to the provider's defaults, so an
    existing ``.env`` that only sets ``LLM_API_KEY`` keeps working.
    """
    p = get_provider(provider)
    return LLMConfig(
        provider=p.name,
        kind=p.kind,
        base_url=base_url or p.base_url,
        model=model or p.default_model,
        api_key=api_key,
        json_mode=_as_bool(json_mode, p.json_mode),
        needs_key=p.needs_key,
    )

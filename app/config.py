"""Runtime configuration loaded from environment (.env picked up by uvicorn/dotenv)."""
from __future__ import annotations

import os
from dataclasses import dataclass

from .providers import resolve as resolve_llm


@dataclass(frozen=True)
class Settings:
    # GitHub
    github_token: str            # PAT with repo scope (pull_requests: write)
    webhook_secret: str          # shared secret for X-Hub-Signature-256
    github_api: str = "https://api.github.com"

    # LLM (provider preset merged with overrides — see app/providers.py)
    llm_provider: str = "deepseek"   # deepseek | openai | claude | local
    llm_kind: str = "openai"         # wire protocol: openai | anthropic
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    llm_json_mode: bool = True

    # Behaviour
    review_profile: str = "android"  # which review focus to use (see app/profiles.py)
    post_reviews: bool = True     # set False to dry-run without touching GitHub
    review_actions: tuple[str, ...] = ("opened", "synchronize", "reopened")


def load_settings() -> Settings:
    llm = resolve_llm(
        provider=os.getenv("LLM_PROVIDER"),
        api_key=os.getenv("LLM_API_KEY", ""),
        base_url=os.getenv("LLM_BASE_URL", ""),
        model=os.getenv("LLM_MODEL", ""),
        json_mode=os.getenv("LLM_JSON_MODE"),
    )
    return Settings(
        github_token=os.getenv("GITHUB_TOKEN", ""),
        webhook_secret=os.getenv("WEBHOOK_SECRET", ""),
        github_api=os.getenv("GITHUB_API", "https://api.github.com"),
        llm_provider=llm.provider,
        llm_kind=llm.kind,
        llm_api_key=llm.api_key,
        llm_base_url=llm.base_url,
        llm_model=llm.model,
        llm_json_mode=llm.json_mode,
        review_profile=os.getenv("REVIEW_PROFILE", "android"),
        post_reviews=os.getenv("POST_REVIEWS", "true").lower() != "false",
    )

"""Runtime configuration loaded from environment (.env picked up by uvicorn/dotenv)."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # GitHub
    github_token: str            # PAT with repo scope (pull_requests: write)
    webhook_secret: str          # shared secret for X-Hub-Signature-256
    github_api: str = "https://api.github.com"

    # DeepSeek (OpenAI-compatible)
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"

    # Behaviour
    review_profile: str = "android"  # which review focus to use (see app/profiles.py)
    post_reviews: bool = True     # set False to dry-run without touching GitHub
    review_actions: tuple[str, ...] = ("opened", "synchronize", "reopened")


def load_settings() -> Settings:
    return Settings(
        github_token=os.getenv("GITHUB_TOKEN", ""),
        webhook_secret=os.getenv("WEBHOOK_SECRET", ""),
        github_api=os.getenv("GITHUB_API", "https://api.github.com"),
        llm_api_key=os.getenv("LLM_API_KEY", ""),
        llm_base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com"),
        llm_model=os.getenv("LLM_MODEL", "deepseek-chat"),
        review_profile=os.getenv("REVIEW_PROFILE", "android"),
        post_reviews=os.getenv("POST_REVIEWS", "true").lower() != "false",
    )

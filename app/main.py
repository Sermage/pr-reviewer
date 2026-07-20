"""Android PR Reviewer — FastAPI webhook service.

Receives GitHub `pull_request` webhooks, runs an Android-focused LLM review
over the PR diff, and posts back a review with a verdict
(APPROVE / REQUEST_CHANGES / COMMENT).
"""
from __future__ import annotations

import logging

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

from .config import load_settings
from .github_client import GitHubClient
from .reviewer import review_diff
from .security import verify_signature

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("android-pr-reviewer")

settings = load_settings()
app = FastAPI(
    title="Android PR Reviewer",
    description="AI code review for Android (Kotlin/Compose) pull requests.",
    version="0.1.0",
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "android-pr-reviewer"}


async def run_review(owner: str, repo: str, number: int) -> None:
    """Background job: fetch diff → LLM review → post back to GitHub."""
    client = GitHubClient(settings.github_token, settings.github_api)
    try:
        diff = await client.get_diff(owner, repo, number)
        result = await review_diff(
            diff,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            profile=settings.review_profile,
            provider=settings.llm_kind,
            json_mode=settings.llm_json_mode,
        )
        log.info("Review for %s/%s#%s -> %s", owner, repo, number, result.event)
        if settings.post_reviews:
            await client.post_review(
                owner, repo, number, result.event, result.body, result.comments
            )
        else:
            log.info("POST_REVIEWS=false, dry-run body:\n%s", result.body)
    except Exception:  # noqa: BLE001 — background task must not crash silently
        log.exception("Review failed for %s/%s#%s", owner, repo, number)


@app.post("/webhook")
async def webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
) -> dict:
    body = await request.body()
    if not verify_signature(body, x_hub_signature_256, settings.webhook_secret):
        raise HTTPException(status_code=401, detail="invalid signature")

    if x_github_event == "ping":
        return {"msg": "pong"}
    if x_github_event != "pull_request":
        return {"skipped": f"event '{x_github_event}' ignored"}

    payload = await request.json()
    action = payload.get("action")
    if action not in settings.review_actions:
        return {"skipped": f"action '{action}' ignored"}

    pr = payload["pull_request"]
    repo = payload["repository"]
    owner = repo["owner"]["login"]
    name = repo["name"]
    number = pr["number"]

    # Answer fast (GitHub retries after ~10s); do the LLM work in background.
    background_tasks.add_task(run_review, owner, name, number)
    return {"accepted": True, "pr": f"{owner}/{name}#{number}"}

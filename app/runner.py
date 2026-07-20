"""Shared review pipeline: fetch a PR diff, review it, post the result.

Used by both the CLI (`pr-reviewer review`) and the CI entrypoint
(`scripts/review_pr.py`) so the review logic lives in exactly one place.
"""
from __future__ import annotations

from dataclasses import dataclass

from .github_client import GitHubClient
from .reviewer import ReviewResult, review_diff


@dataclass
class RunOutcome:
    posted_event: str       # what was actually submitted (may differ from verdict)
    result: ReviewResult    # the raw review (body, comments, verdict)
    posted: bool            # False for a dry run


async def review_pr(
    owner: str,
    repo: str,
    number: int,
    *,
    token: str,
    api_key: str,
    base_url: str,
    model: str,
    profile: str | None = None,
    provider: str = "openai",
    json_mode: bool = True,
    api_base: str = "https://api.github.com",
    allow_approve: bool = True,
    post: bool = True,
) -> RunOutcome:
    """Review a single PR and (optionally) post the review back to GitHub.

    `allow_approve=False` downgrades an APPROVE verdict to a COMMENT — needed
    when the token can't approve (GitHub Actions token, or approving your own PR).
    `post=False` runs everything but skips the GitHub write (dry run).
    """
    client = GitHubClient(token, api_base)
    diff = await client.get_diff(owner, repo, number)
    result = await review_diff(
        diff, api_key=api_key, base_url=base_url, model=model, profile=profile,
        provider=provider, json_mode=json_mode,
    )

    event = result.event
    if event == "APPROVE" and not allow_approve:
        event = "COMMENT"

    if post:
        await client.post_review(
            owner, repo, number, event, result.body, result.comments
        )
    return RunOutcome(posted_event=event, result=result, posted=post)

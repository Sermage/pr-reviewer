"""CI entrypoint: review a single PR and post the result back.

Designed to run inside GitHub Actions (but works locally too). Reuses the same
review core as the webhook service — only the trigger differs.

Inputs (flags override env):
    --repo owner/name   (default: $GITHUB_REPOSITORY)
    --pr N              (default: PR number from $GITHUB_EVENT_PATH)
    --profile android|kmp   (default: $REVIEW_PROFILE or "android")

Env:
    GITHUB_TOKEN   provided automatically by Actions (needs pull-requests: write)
    LLM_API_KEY / LLM_BASE_URL / LLM_MODEL   DeepSeek credentials

Note: GitHub Actions' GITHUB_TOKEN is *not permitted to approve* PRs, so when
running under Actions an APPROVE verdict is downgraded to COMMENT to avoid a
hard failure. Locally (with a PAT) APPROVE is posted as-is.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.runner import review_pr  # noqa: E402


def _pr_number_from_event() -> int | None:
    event_path = os.getenv("GITHUB_EVENT_PATH")
    if not event_path or not Path(event_path).exists():
        return None
    event = json.loads(Path(event_path).read_text())
    pr = event.get("pull_request") or {}
    return pr.get("number")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Review a PR in CI")
    parser.add_argument("--repo", default=os.getenv("GITHUB_REPOSITORY", ""))
    parser.add_argument("--pr", type=int, default=None)
    parser.add_argument("--profile", default=os.getenv("REVIEW_PROFILE", "android"))
    args = parser.parse_args()

    number = args.pr if args.pr is not None else _pr_number_from_event()
    if "/" not in args.repo or not number:
        print("::error::could not resolve repo/PR (need --repo owner/name and --pr N)")
        return 1
    owner, name = args.repo.split("/", 1)

    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        print("::error::GITHUB_TOKEN is not set")
        return 1

    # Actions' token is not permitted to approve PRs — downgrade there.
    allow_approve = os.getenv("GITHUB_ACTIONS") != "true"

    outcome = await review_pr(
        owner, name, number,
        token=token,
        api_key=os.getenv("LLM_API_KEY", ""),
        base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com"),
        model=os.getenv("LLM_MODEL", "deepseek-chat"),
        profile=args.profile,
        api_base=os.getenv("GITHUB_API", "https://api.github.com"),
        allow_approve=allow_approve,
    )
    print(
        f"Posted {outcome.posted_event} review on {owner}/{name}#{number} "
        f"(verdict={outcome.result.verdict}, inline={len(outcome.result.comments)})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

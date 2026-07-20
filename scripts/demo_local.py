"""Run the reviewer over a local .diff file — no GitHub needed.

Usage:
    LLM_API_KEY=sk-... python scripts/demo_local.py samples/sample.diff
    python scripts/demo_local.py samples/sample.diff --profile kmp

Handy for the demo/defense: exercises the full review core against a saved
diff and prints the verdict + body that would be posted to the PR.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.reviewer import review_diff  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser(description="Local Android PR Reviewer demo")
    parser.add_argument("diff", help="path to a .diff file")
    parser.add_argument("--profile", default=os.getenv("REVIEW_PROFILE", "android"))
    args = parser.parse_args()

    diff = Path(args.diff).read_text()
    result = await review_diff(
        diff,
        api_key=os.getenv("LLM_API_KEY", ""),
        base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com"),
        model=os.getenv("LLM_MODEL", "deepseek-chat"),
        profile=args.profile,
    )
    print(f"\n=== VERDICT: {result.event} (profile: {args.profile}) ===\n")
    print(result.body)


if __name__ == "__main__":
    asyncio.run(main())

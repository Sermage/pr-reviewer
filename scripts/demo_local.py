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

from app.providers import resolve as resolve_llm  # noqa: E402
from app.reviewer import review_diff  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser(description="Local AI PR Reviewer demo")
    parser.add_argument("diff", help="path to a .diff file")
    parser.add_argument("--profile", default=os.getenv("REVIEW_PROFILE", "android"),
                        help="android|compose|kmp|auto|<свой>")
    args = parser.parse_args()

    llm = resolve_llm(
        provider=os.getenv("LLM_PROVIDER"),
        api_key=os.getenv("LLM_API_KEY", ""),
        base_url=os.getenv("LLM_BASE_URL", ""),
        model=os.getenv("LLM_MODEL", ""),
        json_mode=os.getenv("LLM_JSON_MODE"),
    )
    diff = Path(args.diff).read_text()
    result = await review_diff(
        diff,
        api_key=llm.api_key,
        base_url=llm.base_url,
        model=llm.model,
        provider=llm.kind,
        json_mode=llm.json_mode,
        profile=args.profile,
    )
    print(f"\n=== VERDICT: {result.event} "
          f"(profile: {args.profile}, provider: {llm.provider}/{llm.model}) ===\n")
    print(result.body)


if __name__ == "__main__":
    asyncio.run(main())

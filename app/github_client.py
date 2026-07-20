"""Thin async GitHub REST client: fetch a PR diff and post a review."""
from __future__ import annotations

import httpx


class GitHubClient:
    def __init__(self, token: str, api_base: str = "https://api.github.com") -> None:
        self._token = token
        self._api = api_base.rstrip("/")

    def _headers(self, accept: str = "application/vnd.github+json") -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "pr-reviewer",
        }

    async def get_diff(self, owner: str, repo: str, number: int) -> str:
        """Return the unified diff of a pull request as plain text."""
        url = f"{self._api}/repos/{owner}/{repo}/pulls/{number}"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                url, headers=self._headers("application/vnd.github.v3.diff")
            )
            resp.raise_for_status()
            return resp.text

    async def post_review(
        self, owner: str, repo: str, number: int, event: str, body: str
    ) -> dict:
        """Submit a PR review.

        `event` is one of APPROVE, REQUEST_CHANGES, COMMENT.
        """
        url = f"{self._api}/repos/{owner}/{repo}/pulls/{number}/reviews"
        payload = {"event": event, "body": body}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=self._headers(), json=payload)
            resp.raise_for_status()
            return resp.json()

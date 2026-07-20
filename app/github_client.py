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
        self,
        owner: str,
        repo: str,
        number: int,
        event: str,
        body: str,
        comments: list[dict] | None = None,
    ) -> dict:
        """Submit a PR review.

        `event` is one of APPROVE, REQUEST_CHANGES, COMMENT.
        `comments` are inline review comments: {path, line, side, body}.
        """
        url = f"{self._api}/repos/{owner}/{repo}/pulls/{number}/reviews"
        payload: dict = {"event": event, "body": body}
        if comments:
            payload["comments"] = comments
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=self._headers(), json=payload)
            if resp.status_code == 422 and comments:
                # An inline anchor GitHub disagrees with fails the whole review.
                # Don't lose the review — retry with the body only.
                fallback = {"event": event, "body": body}
                resp = await client.post(url, headers=self._headers(), json=fallback)
            resp.raise_for_status()
            return resp.json()

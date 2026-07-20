import app.runner as runner
from app.reviewer import ReviewResult


class _FakeClient:
    last_post = None

    def __init__(self, token, api_base="https://api.github.com"):
        pass

    async def get_diff(self, owner, repo, number):
        return "diff --git a/x b/x"

    async def post_review(self, owner, repo, number, event, body, comments=None):
        _FakeClient.last_post = (owner, repo, number, event, comments)
        return {"id": 1}


def _patch(monkeypatch, verdict, event):
    monkeypatch.setattr(runner, "GitHubClient", _FakeClient)

    async def _fake_review(diff, **kw):
        return ReviewResult(event=event, body="b", verdict=verdict, comments=[])

    monkeypatch.setattr(runner, "review_diff", _fake_review)
    _FakeClient.last_post = None


async def test_posts_review(monkeypatch):
    _patch(monkeypatch, "request_changes", "REQUEST_CHANGES")
    outcome = await runner.review_pr(
        "o", "r", 5, token="t", api_key="k", base_url="u", model="m"
    )
    assert outcome.posted is True
    assert outcome.posted_event == "REQUEST_CHANGES"
    assert _FakeClient.last_post[3] == "REQUEST_CHANGES"


async def test_approve_downgraded_when_not_allowed(monkeypatch):
    _patch(monkeypatch, "approve", "APPROVE")
    outcome = await runner.review_pr(
        "o", "r", 5, token="t", api_key="k", base_url="u", model="m",
        allow_approve=False,
    )
    assert outcome.posted_event == "COMMENT"


async def test_dry_run_does_not_post(monkeypatch):
    _patch(monkeypatch, "comment", "COMMENT")
    outcome = await runner.review_pr(
        "o", "r", 5, token="t", api_key="k", base_url="u", model="m", post=False
    )
    assert outcome.posted is False
    assert _FakeClient.last_post is None

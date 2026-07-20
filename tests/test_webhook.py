import dataclasses
import hashlib
import hmac
import json

from fastapi.testclient import TestClient

import app.main as main
from app.main import app

client = TestClient(app)

SECRET = "s3cr3t"


def _use_secret(monkeypatch, secret: str) -> None:
    monkeypatch.setattr(main, "settings", dataclasses.replace(main.settings, webhook_secret=secret))


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["service"] == "android-pr-reviewer"


def test_webhook_rejects_bad_signature(monkeypatch):
    _use_secret(monkeypatch, SECRET)
    r = client.post(
        "/webhook",
        content=b"{}",
        headers={"X-Hub-Signature-256": "sha256=deadbeef", "X-GitHub-Event": "pull_request"},
    )
    assert r.status_code == 401


def test_webhook_accepts_and_schedules(monkeypatch):
    _use_secret(monkeypatch, SECRET)

    scheduled = {}

    async def _fake_run(owner, repo, number):
        scheduled.update(owner=owner, repo=repo, number=number)

    monkeypatch.setattr(main, "run_review", _fake_run)

    payload = {
        "action": "opened",
        "pull_request": {"number": 7},
        "repository": {"name": "app", "owner": {"login": "octocat"}},
    }
    body = json.dumps(payload).encode()
    r = client.post(
        "/webhook",
        content=body,
        headers={"X-Hub-Signature-256": _sign(body), "X-GitHub-Event": "pull_request"},
    )
    assert r.status_code == 200
    assert r.json()["accepted"] is True
    assert scheduled == {"owner": "octocat", "repo": "app", "number": 7}


def test_ping_event(monkeypatch):
    _use_secret(monkeypatch, "")
    r = client.post("/webhook", content=b"{}", headers={"X-GitHub-Event": "ping"})
    assert r.json() == {"msg": "pong"}

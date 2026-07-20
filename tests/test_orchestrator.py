import app.orchestrator as orch
import app.reviewer as reviewer
from app.orchestrator import detect_topics, orchestrate_review, plan_review
from app.reviewer import ReviewResult, review_diff

_COMPOSE_DIFF = (
    "diff --git a/ui/Profile.kt b/ui/Profile.kt\n"
    "+++ b/ui/Profile.kt\n"
    "@@ -1,2 +1,4 @@\n"
    "+import androidx.compose.runtime.Composable\n"
    "+@Composable\n"
    "+fun Profile() { val s = remember { mutableStateOf(0) } }\n"
)

_NETWORK_DIFF = (
    "diff --git a/net/Api.kt b/net/Api.kt\n"
    "+++ b/net/Api.kt\n"
    "@@ -1,2 +1,3 @@\n"
    "+import retrofit2.http.GET\n"
    "+interface Api { @GET(\"/users\") suspend fun users(): List<User> }\n"
)


def test_detect_compose_and_android():
    scores = detect_topics(_COMPOSE_DIFF)
    assert "compose" in scores
    assert "android" in scores  # .kt / androidx baseline


def test_detect_network():
    scores = detect_topics(_NETWORK_DIFF)
    assert "network" in scores


def test_plan_runs_available_profiles():
    plan = plan_review(_COMPOSE_DIFF, ["android", "compose", "kmp"])
    assert "compose" in plan.chosen
    assert "android" in plan.chosen
    assert plan.missing == []


def test_plan_flags_missing_direction():
    # network is detected but no "network" profile exists -> missing + default fallback
    plan = plan_review(_NETWORK_DIFF, ["android", "compose", "kmp"])
    assert "network" in plan.missing
    assert plan.chosen == ["android"]
    assert plan.default == "android"


def test_plan_falls_back_to_default_when_nothing_detected():
    plan = plan_review("just some prose, no code signals", ["android", "kmp"])
    assert plan.chosen == ["android"]


async def _fake_agents(monkeypatch, mapping):
    """Patch orchestrator.review_diff to return canned per-profile results."""
    async def _fake(diff, *, profile, **kw):
        return mapping[profile]
    monkeypatch.setattr(orch, "review_diff", _fake)


async def test_orchestrate_merges_verdicts_and_tags_comments(monkeypatch):
    monkeypatch.setattr(orch, "available_profiles", lambda: ["android", "compose", "kmp"])
    await _fake_agents(monkeypatch, {
        "android": ReviewResult("COMMENT", "b", "comment", summary="android ok"),
        "compose": ReviewResult(
            "REQUEST_CHANGES", "b", "request_changes",
            comments=[{"path": "ui/Profile.kt", "line": 3, "side": "RIGHT", "body": "recompose"}],
            summary="compose issue",
        ),
    })

    result = await orchestrate_review(
        _COMPOSE_DIFF, api_key="k", base_url="u", model="m",
    )
    # worst verdict wins
    assert result.event == "REQUEST_CHANGES"
    # comment is tagged with the profile that raised it
    assert result.comments[0]["body"] == "[compose] recompose"
    # both summaries surface in the merged body
    assert "compose issue" in result.body
    assert "android ok" in result.body


async def test_orchestrate_warns_about_missing_profile(monkeypatch):
    monkeypatch.setattr(orch, "available_profiles", lambda: ["android", "compose", "kmp"])
    await _fake_agents(monkeypatch, {
        "android": ReviewResult("COMMENT", "b", "comment", summary="checked generally"),
    })

    result = await orchestrate_review(_NETWORK_DIFF, api_key="k", base_url="u", model="m")
    assert "Нет профиля для направления" in result.body
    assert "точность по этому направлению снижена" in result.body


async def test_single_profile_no_missing_returns_agent_result(monkeypatch):
    monkeypatch.setattr(orch, "available_profiles", lambda: ["android", "compose", "kmp"])
    only = ReviewResult("APPROVE", "clean body", "approve", summary="lgtm")
    async def _fake(diff, *, profile, **kw):
        return only
    monkeypatch.setattr(orch, "review_diff", _fake)

    # A diff that only trips the android baseline -> exactly one profile, no missing.
    result = await orchestrate_review(
        "diff --git a/A.kt b/A.kt\n+++ b/A.kt\n+val x = 1\n",
        api_key="k", base_url="u", model="m",
    )
    assert result is only  # returned unchanged, no orchestrator wrapper


async def test_review_diff_auto_delegates_to_orchestrator(monkeypatch):
    called = {}
    async def _fake_orch(diff, **kw):
        called["diff"] = diff
        called["kw"] = kw
        return ReviewResult("COMMENT", "orchestrated", "comment")

    monkeypatch.setattr(orch, "orchestrate_review", _fake_orch)
    result = await review_diff(
        "some diff", api_key="k", base_url="u", model="m",
        profile="auto", provider="anthropic", json_mode=False,
    )
    assert result.body == "orchestrated"
    assert called["kw"]["provider"] == "anthropic"
    assert called["kw"]["json_mode"] is False

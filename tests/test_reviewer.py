import app.reviewer as reviewer
from app.profiles import get_profile
from app.reviewer import review_diff


async def _fake_llm(diff, *, system_prompt, api_key, base_url, model):
    # Assert the selected profile's prompt actually reaches the LLM layer.
    _fake_llm.seen_prompt = system_prompt
    return {
        "verdict": "request_changes",
        "summary": "Найдены проблемы с корутинами.",
        "issues": [
            {"file": "MainActivity.kt", "severity": "high", "note": "GlobalScope утечёт."}
        ],
    }


async def test_verdict_maps_to_github_event(monkeypatch):
    monkeypatch.setattr(reviewer, "_call_llm", _fake_llm)
    result = await review_diff(
        "diff --git a/x b/x", api_key="k", base_url="u", model="m", profile="android"
    )
    assert result.event == "REQUEST_CHANGES"
    assert "MainActivity.kt" in result.body
    assert "Android" in result.body


async def test_empty_diff_short_circuits(monkeypatch):
    # Should not even call the LLM.
    called = False

    async def _boom(*a, **k):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(reviewer, "_call_llm", _boom)
    result = await review_diff("   ", api_key="k", base_url="u", model="m")
    assert result.event == "COMMENT"
    assert called is False


async def test_kmp_profile_selected(monkeypatch):
    monkeypatch.setattr(reviewer, "_call_llm", _fake_llm)
    result = await review_diff(
        "diff", api_key="k", base_url="u", model="m", profile="kmp"
    )
    assert "expect/actual" in _fake_llm.seen_prompt
    assert "Kotlin Multiplatform" in result.body


def test_unknown_profile_falls_back_to_android():
    assert get_profile("does-not-exist").name == "android"

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


_DIFF = (
    "diff --git a/app/Main.kt b/app/Main.kt\n"
    "--- a/app/Main.kt\n"
    "+++ b/app/Main.kt\n"
    "@@ -10,1 +10,2 @@\n"
    "     val a = 1\n"
    "+    GlobalScope.launch { }\n"
)


async def test_valid_line_becomes_inline_comment(monkeypatch):
    async def _llm(diff, *, system_prompt, api_key, base_url, model):
        return {
            "verdict": "request_changes",
            "summary": "s",
            "issues": [
                # line 11 is the added GlobalScope line -> valid anchor
                {"file": "app/Main.kt", "line": 11, "severity": "high", "note": "GlobalScope"},
                # line 999 does not exist -> falls back to body
                {"file": "app/Main.kt", "line": 999, "severity": "low", "note": "ghost"},
            ],
        }

    monkeypatch.setattr(reviewer, "_call_llm", _llm)
    result = await review_diff(_DIFF, api_key="k", base_url="u", model="m")

    assert len(result.comments) == 1
    c = result.comments[0]
    assert c == {"path": "app/Main.kt", "line": 11, "side": "RIGHT", "body": "🔴 GlobalScope"}
    # the invalid-line issue is not lost — it lands in the body
    assert "ghost" in result.body

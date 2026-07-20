import app.reviewer as reviewer
from app.providers import get_provider, is_known, resolve
from app.reviewer import _extract_json, review_diff


def test_resolve_defaults_to_deepseek():
    llm = resolve()
    assert llm.provider == "deepseek"
    assert llm.kind == "openai"
    assert llm.base_url == "https://api.deepseek.com"
    assert llm.model == "deepseek-chat"
    assert llm.json_mode is True
    assert llm.needs_key is True


def test_resolve_claude_uses_anthropic_protocol():
    llm = resolve(provider="claude", api_key="sk-ant")
    assert llm.kind == "anthropic"
    assert llm.model == "claude-sonnet-5"
    assert llm.json_mode is False  # Claude has no response_format


def test_resolve_local_is_keyless():
    llm = resolve(provider="local")
    assert llm.needs_key is False
    assert llm.base_url.startswith("http://localhost")
    assert llm.json_mode is False


def test_resolve_overrides_win():
    llm = resolve(provider="openai", base_url="http://x/v1", model="gpt-4o")
    assert llm.base_url == "http://x/v1"
    assert llm.model == "gpt-4o"


def test_resolve_json_mode_env_override():
    assert resolve(provider="deepseek", json_mode="false").json_mode is False
    assert resolve(provider="local", json_mode="true").json_mode is True


def test_unknown_provider_falls_back():
    assert is_known("openai") and not is_known("bard")
    assert get_provider("bard").name == "deepseek"


def test_extract_json_strips_code_fences():
    assert _extract_json('{"a": 1}') == {"a": 1}
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json('```\n{"a": 2}\n```') == {"a": 2}


async def test_review_diff_routes_to_anthropic(monkeypatch):
    seen = {}

    async def _spy(diff, *, system_prompt, api_key, base_url, model, provider, json_mode):
        seen["provider"] = provider
        seen["json_mode"] = json_mode
        return {"verdict": "comment", "summary": "ok", "issues": []}

    monkeypatch.setattr(reviewer, "_call_llm", _spy)
    await review_diff("diff x", api_key="k", base_url="u", model="m",
                      provider="anthropic", json_mode=False)
    assert seen == {"provider": "anthropic", "json_mode": False}


async def test_call_llm_dispatches_by_kind(monkeypatch):
    calls = []

    async def _openai(diff, *, system_prompt, api_key, base_url, model, json_mode=True):
        calls.append(("openai", json_mode))
        return {}

    async def _anthropic(diff, *, system_prompt, api_key, base_url, model):
        calls.append(("anthropic", None))
        return {}

    monkeypatch.setattr(reviewer, "_call_openai", _openai)
    monkeypatch.setattr(reviewer, "_call_anthropic", _anthropic)

    await reviewer._call_llm("d", system_prompt="s", api_key="k", base_url="u",
                             model="m", provider="anthropic")
    await reviewer._call_llm("d", system_prompt="s", api_key="k", base_url="u",
                             model="m", provider="openai", json_mode=False)
    assert calls == [("anthropic", None), ("openai", False)]

import argparse

import app.cli as cli
from app.cli import cmd_profile, env_value, upsert_env
from app.profiles import PROFILES, get_profile


def test_upsert_updates_existing_key():
    text = "LLM_API_KEY=old\nREVIEW_PROFILE=android\n"
    out = upsert_env(text, {"LLM_API_KEY": "new"})
    assert "LLM_API_KEY=new" in out
    assert "REVIEW_PROFILE=android" in out
    assert out.count("LLM_API_KEY=") == 1


def test_upsert_appends_missing_key():
    out = upsert_env("REVIEW_PROFILE=kmp\n", {"LLM_API_KEY": "sk-1"})
    assert "REVIEW_PROFILE=kmp" in out
    assert "LLM_API_KEY=sk-1" in out


def test_upsert_preserves_comments_and_blanks():
    text = "# GitHub\nGITHUB_TOKEN=t\n\n# LLM\nLLM_API_KEY=sk-xxx\n"
    out = upsert_env(text, {"LLM_API_KEY": "sk-real"})
    assert "# GitHub" in out
    assert "# LLM" in out
    assert "LLM_API_KEY=sk-real" in out
    assert "LLM_API_KEY=sk-xxx" not in out


def test_env_value_reads_and_ignores_comments():
    text = "# LLM_API_KEY=commented\nLLM_API_KEY=sk-9\n"
    assert env_value(text, "LLM_API_KEY") == "sk-9"
    assert env_value(text, "MISSING") == ""


def test_compose_profile_registered():
    assert "compose" in PROFILES
    assert get_profile("compose").name == "compose"
    assert "recomposition" in get_profile("compose").system_prompt.lower()


def _profile_args(name="", repo="", sync=False):
    return argparse.Namespace(name=name, repo=repo, sync=sync)


def test_profile_switch_writes_env(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    monkeypatch.setattr(cli, "ENV_PATH", env)
    monkeypatch.setattr(cli, "gh_default_repo", lambda: "")  # no remote sync
    assert cmd_profile(_profile_args(name="compose")) == 0
    assert env_value(env.read_text(), "REVIEW_PROFILE") == "compose"


def test_profile_rejects_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ENV_PATH", tmp_path / ".env")
    assert cmd_profile(_profile_args(name="perl")) == 1

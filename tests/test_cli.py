import argparse

import app.cli as cli
import app.profiles as profiles
from app.cli import cmd_profile, env_value, upsert_env
from app.profiles import PROFILES, get_profile, load_profiles


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


def _profile_args(name="", repo="", sync=False, add="", edit="", show="",
                  remove="", focus="", from_file=""):
    return argparse.Namespace(
        name=name, repo=repo, sync=sync, add=add, edit=edit, show=show,
        remove=remove, focus=focus, from_file=from_file,
    )


def test_profile_switch_writes_env(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    monkeypatch.setattr(cli, "ENV_PATH", env)
    monkeypatch.setattr(cli, "gh_default_repo", lambda: "")  # no remote sync
    assert cmd_profile(_profile_args(name="compose")) == 0
    assert env_value(env.read_text(), "REVIEW_PROFILE") == "compose"


def test_profile_rejects_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(profiles, "PROFILES_DIR", tmp_path / "profiles.d")
    assert cmd_profile(_profile_args(name="perl")) == 1


def test_custom_profile_loaded_from_dir(tmp_path, monkeypatch):
    pdir = tmp_path / "profiles.d"
    pdir.mkdir()
    (pdir / "security.md").write_text("Ищи SQL-инъекции и утечки секретов.")
    monkeypatch.setattr(profiles, "PROFILES_DIR", pdir)
    loaded = load_profiles()
    assert "security" in loaded
    assert "SQL" in get_profile("security").system_prompt


def test_profile_add_and_remove(tmp_path, monkeypatch):
    pdir = tmp_path / "profiles.d"
    monkeypatch.setattr(profiles, "PROFILES_DIR", pdir)

    assert cmd_profile(_profile_args(add="security", focus="Ищи уязвимости.")) == 0
    assert (pdir / "security.md").exists()
    assert "security" in load_profiles()

    assert cmd_profile(_profile_args(remove="security")) == 0
    assert not (pdir / "security.md").exists()


def test_cannot_override_builtin(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "PROFILES_DIR", tmp_path / "profiles.d")
    assert cmd_profile(_profile_args(add="android", focus="x")) == 1


def test_profile_edit_replaces_focus(tmp_path, monkeypatch):
    pdir = tmp_path / "profiles.d"
    monkeypatch.setattr(profiles, "PROFILES_DIR", pdir)
    assert cmd_profile(_profile_args(add="security", focus="Старый focus.")) == 0

    assert cmd_profile(_profile_args(edit="security", focus="Новый focus.")) == 0
    assert (pdir / "security.md").read_text().strip() == "Новый focus."
    assert "Новый" in get_profile("security").system_prompt


def test_profile_edit_missing_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "PROFILES_DIR", tmp_path / "profiles.d")
    assert cmd_profile(_profile_args(edit="ghost")) == 1


def test_profile_edit_rejects_builtin(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "PROFILES_DIR", tmp_path / "profiles.d")
    assert cmd_profile(_profile_args(edit="android", focus="x")) == 1


def test_profile_show_builtin_and_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "PROFILES_DIR", tmp_path / "profiles.d")
    assert cmd_profile(_profile_args(show="compose")) == 0
    assert cmd_profile(_profile_args(show="nope")) == 1

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


# ── update / uninstall ─────────────────────────────────────────────────
def test_update_source_pulls_then_reinstalls(tmp_path, monkeypatch):
    pkg = tmp_path / "repo"
    (pkg / ".venv" / "bin").mkdir(parents=True)
    (pkg / ".venv" / "bin" / "pip").write_text("#!/bin/sh\n")
    monkeypatch.setattr(cli.paths, "is_source_checkout", lambda: True)
    monkeypatch.setattr(cli.paths, "package_root", lambda: pkg)
    calls = []
    monkeypatch.setattr(cli.subprocess, "call", lambda c, *a, **k: calls.append(c) or 0)
    assert cli.cmd_update(argparse.Namespace()) == 0
    assert calls[0][:3] == ["git", "-C", str(pkg)] and "pull" in calls[0]
    assert calls[1][0] == str(pkg / ".venv" / "bin" / "pip") and "install" in calls[1]


def test_update_source_stops_on_pull_failure(tmp_path, monkeypatch):
    pkg = tmp_path / "repo"
    pkg.mkdir()
    monkeypatch.setattr(cli.paths, "is_source_checkout", lambda: True)
    monkeypatch.setattr(cli.paths, "package_root", lambda: pkg)
    monkeypatch.setattr(cli.subprocess, "call", lambda c, *a, **k: 1)
    assert cli.cmd_update(argparse.Namespace()) == 1  # no reinstall after failed pull


def test_update_installed_without_pipx_prints_hint(monkeypatch, capsys):
    monkeypatch.setattr(cli.paths, "is_source_checkout", lambda: False)
    monkeypatch.setattr(cli.shutil, "which", lambda n: None)
    assert cli.cmd_update(argparse.Namespace()) == 0
    assert "pipx" in capsys.readouterr().out


def test_uninstall_source_removes_symlink_and_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    pkg = tmp_path / "repo"
    (pkg / ".venv" / "bin").mkdir(parents=True)
    binexe = pkg / ".venv" / "bin" / "pr-reviewer"
    binexe.write_text("#!/bin/sh\n")
    bindir = tmp_path / ".local" / "bin"
    bindir.mkdir(parents=True)
    link = bindir / "pr-reviewer"
    link.symlink_to(binexe)
    env = pkg / ".env"
    env.write_text("LLM_API_KEY=sk\n")
    monkeypatch.setattr(cli.paths, "is_source_checkout", lambda: True)
    monkeypatch.setattr(cli.paths, "package_root", lambda: pkg)
    monkeypatch.setattr(cli, "ENV_PATH", env)
    assert cli.cmd_uninstall(argparse.Namespace(yes=True)) == 0
    assert not link.is_symlink()
    assert not env.exists()


def test_uninstall_keeps_foreign_symlink(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    other = tmp_path / "other" / "pr-reviewer"
    other.parent.mkdir(parents=True)
    other.write_text("#!/bin/sh\n")
    bindir = tmp_path / ".local" / "bin"
    bindir.mkdir(parents=True)
    link = bindir / "pr-reviewer"
    link.symlink_to(other)
    monkeypatch.setattr(cli.paths, "is_source_checkout", lambda: True)
    monkeypatch.setattr(cli.paths, "package_root", lambda: tmp_path / "repo")
    monkeypatch.setattr(cli, "ENV_PATH", tmp_path / "repo" / ".env")
    assert cli.cmd_uninstall(argparse.Namespace(yes=True)) == 0
    assert link.is_symlink()  # points elsewhere → untouched


def test_uninstall_installed_removes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))  # no ~/.local/bin symlink present
    home = tmp_path / ".pr-reviewer"
    home.mkdir()
    (home / ".env").write_text("x")
    monkeypatch.setattr(cli.paths, "is_source_checkout", lambda: False)
    monkeypatch.setattr(cli.paths, "home_dir", lambda: home)
    assert cli.cmd_uninstall(argparse.Namespace(yes=True)) == 0
    assert not home.exists()


# ── install-workflow ───────────────────────────────────────────────────
def _cp(returncode=0, stdout=""):
    import subprocess
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def test_external_workflow_installs_from_git_and_runs_cli():
    wf = cli.EXTERNAL_WORKFLOW
    assert 'pip install "git+https://github.com/Sermage/pr-reviewer"' in wf
    assert "pr-reviewer review" in wf
    assert "on:\n  pull_request:" in wf
    assert "${{ secrets.GITHUB_TOKEN }}" in wf  # f-string braces rendered correctly


def test_install_workflow_creates_when_absent(monkeypatch):
    calls = []

    def fake_gh(*args, stdin=None):
        calls.append(args)
        if ".default_branch" in args:
            return _cp(stdout="main\n")
        if "GET" in args:                      # existing file lookup → not found
            return _cp(returncode=1)
        return _cp(stdout="{}")                # PUT

    monkeypatch.setattr(cli, "_gh", fake_gh)
    assert cli._install_workflow("o/r").returncode == 0
    put = next(c for c in calls if "PUT" in c)
    assert "branch=main" in put
    assert any(a.startswith("content=") for a in put)
    assert not any(a.startswith("sha=") for a in put)   # create path: no sha


def test_install_workflow_updates_existing(monkeypatch):
    calls = []

    def fake_gh(*args, stdin=None):
        calls.append(args)
        if ".default_branch" in args:
            return _cp(stdout="master\n")
        if "GET" in args:                      # existing file → return its sha
            return _cp(stdout="blobsha123\n")
        return _cp(stdout="{}")                # PUT

    monkeypatch.setattr(cli, "_gh", fake_gh)
    assert cli._install_workflow("o/r").returncode == 0
    put = next(c for c in calls if "PUT" in c)
    assert "sha=blobsha123" in put             # update path passes blob sha


def test_install_workflow_aborts_without_repo(monkeypatch):
    monkeypatch.setattr(cli, "_gh", lambda *a, **k: _cp(returncode=1))
    assert cli._install_workflow("o/r").returncode == 1  # default-branch lookup failed


def test_cmd_install_workflow_print_outputs_yaml(monkeypatch, capsys):
    assert cli.cmd_install_workflow(argparse.Namespace(repo="", print=True)) == 0
    assert "pr-reviewer review" in capsys.readouterr().out


# ── uninstall-workflow ──────────────────────────────────────────────────
def test_remove_workflow_deletes_when_present(monkeypatch):
    calls = []

    def fake_gh(*args, stdin=None):
        calls.append(args)
        if ".default_branch" in args:
            return _cp(stdout="main\n")
        if "GET" in args:                      # file present → return sha
            return _cp(stdout="blob99\n")
        return _cp(stdout="{}")                # DELETE

    monkeypatch.setattr(cli, "_gh", fake_gh)
    assert cli._remove_workflow("o/r").returncode == 0
    delete = next(c for c in calls if "DELETE" in c)
    assert "sha=blob99" in delete and "branch=main" in delete


def test_remove_workflow_absent_is_ok(monkeypatch):
    def fake_gh(*args, stdin=None):
        if ".default_branch" in args:
            return _cp(stdout="main\n")
        if "GET" in args:                      # file not found
            return _cp(returncode=1)
        raise AssertionError("должно остановиться до DELETE")

    monkeypatch.setattr(cli, "_gh", fake_gh)
    assert cli._remove_workflow("o/r").returncode == 0   # idempotent


def test_uninstall_workflow_removes_secret_and_vars(monkeypatch):
    calls = []

    def fake_gh(*args, stdin=None):
        calls.append(args)
        if ".default_branch" in args:
            return _cp(stdout="main\n")
        if "GET" in args:
            return _cp(returncode=1)           # no workflow file
        return _cp(stdout="{}")

    monkeypatch.setattr(cli, "gh_available", lambda: True)
    monkeypatch.setattr(cli, "_gh", fake_gh)
    rc = cli.cmd_uninstall_workflow(argparse.Namespace(repo="o/r", yes=True))
    assert rc == 0
    assert ("secret", "delete", "LLM_API_KEY", "--repo", "o/r") in calls
    for var in ("REVIEW_PROFILE", "LLM_PROVIDER", "LLM_MODEL", "LLM_BASE_URL"):
        assert ("variable", "delete", var, "--repo", "o/r") in calls


def test_uninstall_workflow_needs_repo(monkeypatch):
    monkeypatch.setattr(cli, "gh_available", lambda: True)
    monkeypatch.setattr(cli, "gh_default_repo", lambda: "")
    assert cli.cmd_uninstall_workflow(argparse.Namespace(repo="", yes=True)) == 1


def test_remote_workflow_exists_true_and_false(monkeypatch):
    monkeypatch.setattr(cli, "_gh", lambda *a, **k: _cp(stdout="sha123\n"))
    assert cli._remote_workflow_exists("o/r") is True
    monkeypatch.setattr(cli, "_gh", lambda *a, **k: _cp(returncode=1))
    assert cli._remote_workflow_exists("o/r") is False

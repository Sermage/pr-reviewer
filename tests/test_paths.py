from pathlib import Path

import app.paths as paths


def test_source_checkout_uses_repo_root():
    # The test suite runs from a checkout, so repo files are present.
    assert paths.is_source_checkout()
    assert paths.home_dir() == paths.package_root()
    assert paths.env_path() == paths.package_root() / ".env"


def test_pr_reviewer_home_override(monkeypatch, tmp_path):
    monkeypatch.setenv("PR_REVIEWER_HOME", str(tmp_path))
    assert paths.home_dir() == tmp_path
    assert paths.env_path() == tmp_path / ".env"
    assert paths.profiles_dir() == tmp_path / "profiles.d"


def test_installed_mode_falls_back_to_user_home(monkeypatch):
    monkeypatch.delenv("PR_REVIEWER_HOME", raising=False)
    monkeypatch.setattr(paths, "_is_source_checkout", lambda: False)
    assert paths.home_dir() == Path.home() / ".pr-reviewer"


def test_profiles_dir_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("PROFILES_DIR", str(tmp_path / "custom"))
    assert paths.profiles_dir() == tmp_path / "custom"

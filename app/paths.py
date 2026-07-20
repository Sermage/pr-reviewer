"""Where the tool keeps its config (``.env``) and custom profiles (``profiles.d/``).

Two modes, detected automatically:

- **Source checkout** (git clone / ``pip install -e .``): the repo root, so
  ``.env``, ``profiles.d/`` and ``.github/workflows/`` sit next to the code and
  ship to CI.
- **Installed tool** (``pipx install`` / installed from anywhere): a per-user
  directory ``~/.pr-reviewer``, because the package lives in read-only
  site-packages.

Override the base with ``$PR_REVIEWER_HOME`` (and ``profiles.d`` alone with
``$PROFILES_DIR``).
"""
from __future__ import annotations

import os
from pathlib import Path

# Parent of the ``app/`` package. In a checkout this is the repo root; when
# installed it's site-packages.
_PKG_ROOT = Path(__file__).resolve().parent.parent

USER_HOME = Path.home() / ".pr-reviewer"


def _is_source_checkout() -> bool:
    """True when running from a repo/editable install (repo files are present)."""
    return (_PKG_ROOT / "pyproject.toml").is_file() and (_PKG_ROOT / "app").is_dir()


def is_source_checkout() -> bool:
    return _is_source_checkout()


def package_root() -> Path:
    """Directory that contains the ``app`` package (for `serve`'s working dir)."""
    return _PKG_ROOT


def home_dir() -> Path:
    """Base directory for ``.env`` and ``profiles.d/``."""
    override = os.getenv("PR_REVIEWER_HOME")
    if override:
        return Path(override).expanduser()
    if _is_source_checkout():
        return _PKG_ROOT
    return USER_HOME


def ensure_home() -> Path:
    d = home_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def env_path() -> Path:
    return home_dir() / ".env"


def env_example_path() -> Path:
    return _PKG_ROOT / ".env.example"


def profiles_dir() -> Path:
    override = os.getenv("PROFILES_DIR")
    if override:
        return Path(override).expanduser()
    return home_dir() / "profiles.d"


def workflow_path() -> Path:
    return _PKG_ROOT / ".github" / "workflows" / "ai-review.yml"

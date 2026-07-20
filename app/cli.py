"""AI PR Reviewer — friendly command-line interface.

Commands:
    pr-reviewer setup     interactive wizard: API key (hidden), profile, GitHub Actions
    pr-reviewer doctor     check that everything is configured
    pr-reviewer serve      run the webhook service locally
    pr-reviewer install-workflow   commit the auto-review GitHub Actions workflow to a repo
    pr-reviewer uninstall-workflow remove auto-review from a repo (workflow, secret, variables)
    pr-reviewer update     pull the latest version and reinstall (or pipx upgrade)
    pr-reviewer uninstall  remove the symlink and config (points to .venv/pipx removal)

The setup wizard reads the DeepSeek key with getpass (input is hidden, like a
password prompt) and, when `gh` is authenticated, offers to wire up GitHub
Actions for you — setting the repo secret via stdin so the key never appears in
the process list.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import getpass
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import paths
from .profiles import (
    AUTO_PROFILE,
    DEFAULT_PROFILE,
    available_profiles,
    custom_path,
    is_builtin,
    load_profiles,
)
from .providers import (
    DEFAULT_PROVIDER,
    available_providers,
    get_provider,
    is_known,
    resolve as resolve_llm,
)

PKG_ROOT = paths.package_root()      # dir containing app/ — used as serve's cwd
ENV_PATH = paths.env_path()          # repo/.env in a checkout, else ~/.pr-reviewer/.env
ENV_EXAMPLE = paths.env_example_path()
WORKFLOW = paths.workflow_path()

# How the user actually invokes us. In a checkout there is no global command —
# only the ./pr-reviewer launcher — so hints must carry the ./ prefix. After
# pipx install the command is on PATH globally.
CMD = "./pr-reviewer" if paths.is_source_checkout() else "pr-reviewer"

# Where this tool is installed from — used by the CI workflow (pip install) and
# by `update` (pipx). Single source of truth.
REPO_HTTPS = "https://github.com/Sermage/pr-reviewer"
REPO_URL = f"git+{REPO_HTTPS}"
WORKFLOW_REL = ".github/workflows/ai-review.yml"

# What the Actions setup writes to a target repo (and uninstall-workflow removes).
ACTIONS_SECRET = "LLM_API_KEY"
ACTIONS_VARS = ("REVIEW_PROFILE", "LLM_PROVIDER", "LLM_MODEL", "LLM_BASE_URL")

# Self-contained workflow for a *target* repo (e.g. an Android app): that repo
# has no reviewer code, so the job installs pr-reviewer from git and calls it.
EXTERNAL_WORKFLOW = f"""\
name: AI PR Review

on:
  pull_request:
    types: [opened, synchronize, reopened]

# GITHUB_TOKEN needs write access to post the review.
permissions:
  contents: read
  pull-requests: write

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      # This repo has no reviewer code of its own — install it from git.
      - name: Install AI PR Reviewer
        run: pip install "{REPO_URL}"

      - name: Review pull request
        env:
          GITHUB_TOKEN: ${{{{ secrets.GITHUB_TOKEN }}}}
          # Setup stores the key as LLM_API_KEY; DEEPSEEK_API_KEY kept as fallback.
          LLM_API_KEY: ${{{{ secrets.LLM_API_KEY || secrets.DEEPSEEK_API_KEY }}}}
          LLM_PROVIDER: ${{{{ vars.LLM_PROVIDER || 'deepseek' }}}}
          LLM_BASE_URL: ${{{{ vars.LLM_BASE_URL }}}}
          LLM_MODEL: ${{{{ vars.LLM_MODEL }}}}
          REVIEW_PROFILE: ${{{{ vars.REVIEW_PROFILE || 'android' }}}}
        run: >-
          pr-reviewer review
          --repo "${{{{ github.repository }}}}"
          --pr "${{{{ github.event.pull_request.number }}}}"
"""


def _write_env(text: str) -> None:
    """Write .env, creating its directory (needed for the ~/.pr-reviewer case)."""
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    ENV_PATH.write_text(text)


# ── tiny terminal helpers ─────────────────────────────────────────────
def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if sys.stdout.isatty() else s


def ok(s: str) -> str:
    return _c("32", s)


def warn(s: str) -> str:
    return _c("33", s)


def err(s: str) -> str:
    return _c("31", s)


def bold(s: str) -> str:
    return _c("1", s)


def confirm(question: str, default: bool = True) -> bool:
    if not sys.stdin.isatty():
        return default
    suffix = "[Y/n]" if default else "[y/N]"
    ans = input(f"{question} {suffix} ").strip().lower()
    if not ans:
        return default
    return ans in ("y", "yes", "д", "да")


def ask(question: str, default: str = "") -> str:
    if not sys.stdin.isatty():
        return default
    hint = f" [{default}]" if default else ""
    return input(f"{question}{hint}: ").strip() or default


# ── .env read/write (pure, testable) ──────────────────────────────────
def upsert_env(text: str, updates: dict[str, str]) -> str:
    """Return `text` with each key in `updates` set, preserving other lines."""
    remaining = dict(updates)
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in line:
            key = line.split("=", 1)[0].strip()
            if key in remaining:
                out.append(f"{key}={remaining.pop(key)}")
                continue
        out.append(line)
    for key, value in remaining.items():
        out.append(f"{key}={value}")
    body = "\n".join(out)
    return body if body.endswith("\n") else body + "\n"


def env_value(text: str, key: str) -> str:
    """Read a KEY=value from .env text (empty string if unset)."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if k.strip() == key:
                return v.strip()
    return ""


def _base_env() -> str:
    if ENV_PATH.exists():
        return ENV_PATH.read_text()
    if ENV_EXAMPLE.exists():
        return ENV_EXAMPLE.read_text()
    return ""


# ── gh helpers ────────────────────────────────────────────────────────
def _gh(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["gh", *args], input=stdin, text=True, capture_output=True
    )


def gh_available() -> bool:
    return shutil.which("gh") is not None


def gh_account() -> str | None:
    if not gh_available():
        return None
    r = _gh("api", "user", "-q", ".login")
    return r.stdout.strip() if r.returncode == 0 else None


def gh_default_repo() -> str:
    r = _gh("repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner")
    return r.stdout.strip() if r.returncode == 0 else ""


def _step_status(r: subprocess.CompletedProcess, label: str) -> str:
    if r.returncode == 0:
        return f"   {ok('✓')} {label}"
    tail = (r.stderr or "").strip().splitlines()
    return f"   {err('✗')} {label} — {tail[-1] if tail else 'ошибка'}"


def _install_workflow(repo: str) -> subprocess.CompletedProcess:
    """Commit the self-contained review workflow to `repo`'s default branch.

    Works on any target repo via the GitHub contents API (no local checkout).
    Creates the file, or updates it in place if it already exists.
    """
    branch = _gh("api", f"repos/{repo}", "-q", ".default_branch")
    if branch.returncode != 0:
        return branch
    ref = branch.stdout.strip()
    content = base64.b64encode(EXTERNAL_WORKFLOW.encode()).decode()
    args = [
        "api", "--method", "PUT", f"repos/{repo}/contents/{WORKFLOW_REL}",
        "-f", "message=ci: авто-ревью PR через AI PR Reviewer",
        "-f", f"branch={ref}",
        "-f", f"content={content}",
    ]
    # If the file already exists, the API requires its blob sha to update.
    existing = _gh("api", f"repos/{repo}/contents/{WORKFLOW_REL}",
                   "-q", ".sha", "-X", "GET", "-f", f"ref={ref}")
    if existing.returncode == 0 and existing.stdout.strip():
        args += ["-f", f"sha={existing.stdout.strip()}"]
    return _gh(*args)


def _remove_workflow(repo: str) -> subprocess.CompletedProcess:
    """Delete the review workflow from `repo`'s default branch (idempotent)."""
    branch = _gh("api", f"repos/{repo}", "-q", ".default_branch")
    if branch.returncode != 0:
        return branch
    ref = branch.stdout.strip()
    existing = _gh("api", f"repos/{repo}/contents/{WORKFLOW_REL}",
                   "-q", ".sha", "-X", "GET", "-f", f"ref={ref}")
    if existing.returncode != 0 or not existing.stdout.strip():
        # Already absent — nothing to delete.
        return subprocess.CompletedProcess([], 0, stdout="absent", stderr="")
    return _gh("api", "--method", "DELETE", f"repos/{repo}/contents/{WORKFLOW_REL}",
               "-f", "message=ci: удалить авто-ревью (AI PR Reviewer)",
               "-f", f"branch={ref}", "-f", f"sha={existing.stdout.strip()}")


# ── setup wizard ──────────────────────────────────────────────────────
def cmd_setup(_: argparse.Namespace) -> int:
    print(bold("\n🤖 AI PR Reviewer — настройка\n"))
    if not sys.stdin.isatty():
        print(err("Нужен интерактивный терминал. Запусти в обычном shell."))
        return 1

    # 1. environment
    print(bold("1) Окружение"))
    print(f"   {ok('✓')} Python {sys.version_info.major}.{sys.version_info.minor}")
    account = gh_account()
    if account:
        print(f"   {ok('✓')} gh авторизован как {account}")
    elif gh_available():
        print(f"   {warn('!')} gh установлен, но не авторизован")
        if confirm("   Авторизоваться сейчас (gh auth login)?", default=True):
            subprocess.call(["gh", "auth", "login"])
            account = gh_account()
            if account:
                print(f"   {ok('✓')} gh авторизован как {account}")
            else:
                print(f"   {warn('!')} не удалось — шаг про Actions пропустим")
    else:
        print(f"   {warn('!')} gh не найден — шаг про Actions будет пропущен")

    env_text = _base_env()

    # 2. LLM provider
    print(bold("\n2) Провайдер LLM"))
    providers = available_providers()
    current_provider = env_value(env_text, "LLM_PROVIDER") or DEFAULT_PROVIDER
    for i, pname in enumerate(providers, 1):
        p = get_provider(pname)
        mark = " (по умолчанию)" if pname == DEFAULT_PROVIDER else ""
        note = "" if p.needs_key else " — локально, без ключа"
        print(f"   {i}. {pname} — {p.default_model}{note}{mark}")
    try:
        default_idx = providers.index(current_provider) + 1
    except ValueError:
        default_idx = providers.index(DEFAULT_PROVIDER) + 1
    raw_p = ask("   Выбор", str(default_idx))
    try:
        provider = providers[int(raw_p) - 1]
    except (ValueError, IndexError):
        provider = DEFAULT_PROVIDER
    prov = get_provider(provider)

    # 3. API key (hidden input) — skipped for keyless local backends
    label = {"deepseek": "DeepSeek", "openai": "OpenAI",
             "claude": "Anthropic (Claude)"}.get(provider, provider)
    new_key = ""
    if prov.needs_key:
        print(bold(f"\n3) Ключ {label}"))
        current = env_value(env_text, "LLM_API_KEY")
        has_key = current not in ("", "sk-xxx")
        if has_key and not confirm("   Ключ уже есть в .env. Перезаписать?", default=False):
            print("   оставляю текущий")
        else:
            new_key = getpass.getpass(f"   Введите ключ {label} (ввод скрыт): ").strip()
            while not new_key and confirm("   Пусто. Ввести снова?", default=True):
                new_key = getpass.getpass(f"   Введите ключ {label} (ввод скрыт): ").strip()
    else:
        print(bold("\n3) Ключ"))
        print(f"   {ok('✓')} {provider} — локальная модель, ключ не нужен. "
              f"Endpoint: {prov.base_url}")

    # 4. review profile
    print(bold("\n4) Профиль ревью"))
    names = available_profiles() + [AUTO_PROFILE]
    for i, name in enumerate(names, 1):
        if name == AUTO_PROFILE:
            note = " — оркестратор: сам определит направление(я) PR"
        elif name == DEFAULT_PROFILE:
            note = " (по умолчанию)"
        else:
            note = ""
        print(f"   {i}. {name}{note}")
    raw = ask("   Выбор", str(names.index(DEFAULT_PROFILE) + 1))
    try:
        profile = names[int(raw) - 1]
    except (ValueError, IndexError):
        profile = DEFAULT_PROFILE

    # write .env
    updates = {
        "REVIEW_PROFILE": profile,
        "LLM_PROVIDER": provider,
        "LLM_BASE_URL": prov.base_url,
        "LLM_MODEL": prov.default_model,
    }
    if new_key:
        updates["LLM_API_KEY"] = new_key
    _write_env(upsert_env(env_text, updates))
    saved = ", ключ сохранён" if new_key else ""
    print(f"   {ok('✓')} записал .env (провайдер={provider}, профиль={profile}{saved})")

    # 5. GitHub Actions
    print(bold("\n5) GitHub Actions"))
    key_for_secret = new_key or env_value(ENV_PATH.read_text(), "LLM_API_KEY")
    if not prov.needs_key:
        print(f"   {warn('!')} провайдер '{provider}' — локальная модель; "
              "GitHub Actions не достучится до localhost. "
              "Для авто-ревью выбери облачный провайдер.")
    if account and prov.needs_key and confirm(
        "   Настроить авто-ревью на PR через GitHub Actions?", default=True
    ):
        repo = ask("   Репозиторий (owner/name)", gh_default_repo())
        if repo:
            if key_for_secret and key_for_secret != "sk-xxx":
                r = _gh("secret", "set", "LLM_API_KEY", "--repo", repo,
                        stdin=key_for_secret)
                print(_step_status(r, f"секрет LLM_API_KEY → {repo}"))
            else:
                print(f"   {warn('!')} ключ неизвестен — задай секрет позже: "
                      f"gh secret set LLM_API_KEY --repo {repo}")
            for var, val in (("REVIEW_PROFILE", profile), ("LLM_PROVIDER", provider),
                             ("LLM_MODEL", prov.default_model), ("LLM_BASE_URL", prov.base_url)):
                r = _gh("variable", "set", var, "--repo", repo, "--body", val)
                print(_step_status(r, f"variable {var}={val}"))
            # The workflow file must live in the *target* repo, on its default
            # branch, or nothing runs. Commit it there via the API.
            if confirm("   Добавить workflow ai-review.yml в репозиторий?", default=True):
                r = _install_workflow(repo)
                print(_step_status(r, f"workflow {WORKFLOW_REL} → {repo}"))
                if r.returncode == 0:
                    print(ok("   Готово — открой PR, ревью запустится автоматически."))
                else:
                    print(f"   {warn('!')} не удалось закоммитить workflow "
                          "(ветка защищена?). Добавь файл вручную — "
                          f"{bold(f'{CMD} install-workflow --repo {repo}')} "
                          "покажет команду.")
            else:
                print(f"   {warn('!')} без workflow авто-ревью не запустится. "
                      f"Позже: {bold(f'{CMD} install-workflow --repo {repo}')}")
        else:
            print(f"   {warn('!')} репозиторий не указан — пропускаю")
    elif not account:
        print("   Пропущено (нужен авторизованный gh). "
              f"Позже: gh auth login, затем {CMD} setup")

    # summary
    print(bold("\n✅ Готово. Дальше:"))
    print(f"   {CMD} doctor      # проверить настройку")
    print(f"   {CMD} serve       # локальный webhook-сервис")
    print(f"   {CMD} serve и туннель (ngrok/smee) — см. README")
    return 0


# ── doctor ────────────────────────────────────────────────────────────
def cmd_doctor(_: argparse.Namespace) -> int:
    print(bold("\n🩺 AI PR Reviewer — проверка\n"))
    mode = "репозиторий" if paths.is_source_checkout() else "установлен (pipx)"
    print(f"   📁 конфиг: {ENV_PATH.parent}  ({mode})\n")
    env_text = ENV_PATH.read_text() if ENV_PATH.exists() else ""

    def check(label: str, good: bool, hint: str = "") -> None:
        icon = ok("✓") if good else err("✗")
        extra = "" if good else f"  → {hint}"
        print(f"   {icon} {label}{extra}")

    check(".env существует", ENV_PATH.exists(), f"запусти: {CMD} setup")
    llm = resolve_llm(
        provider=env_value(env_text, "LLM_PROVIDER") or None,
        api_key=env_value(env_text, "LLM_API_KEY"),
        base_url=env_value(env_text, "LLM_BASE_URL"),
        model=env_value(env_text, "LLM_MODEL"),
        json_mode=env_value(env_text, "LLM_JSON_MODE") or None,
    )
    check(f"провайдер: {llm.provider} ({llm.model})", is_known(llm.provider),
          f"неизвестный, доступны: {', '.join(available_providers())}")
    if llm.needs_key:
        check("ключ LLM задан", llm.api_key not in ("", "sk-xxx"), f"{CMD} setup")
    else:
        check(f"локальная модель на {llm.base_url}", True,
              "ключ не нужен; убедись, что сервер запущен")
    profile = env_value(env_text, "REVIEW_PROFILE") or DEFAULT_PROFILE
    names = available_profiles()
    label = f"{profile} (оркестратор)" if profile == AUTO_PROFILE else profile
    check(f"профиль ревью: {label}", profile in names or profile == AUTO_PROFILE,
          f"неизвестный профиль, доступны: {', '.join(names)}, {AUTO_PROFILE}")
    check("gh авторизован", gh_account() is not None, "gh auth login")
    check("workflow ai-review.yml", WORKFLOW.exists(), "восстанови .github/workflows/")
    print()
    return 0


# ── review (one-off from the terminal) ────────────────────────────────
def _gh_token() -> str:
    if not gh_available():
        return ""
    r = _gh("auth", "token")
    return r.stdout.strip() if r.returncode == 0 else ""


def _load_env_file() -> None:
    """Populate os.environ from .env (without overriding real env vars)."""
    if not ENV_PATH.exists():
        return
    text = ENV_PATH.read_text()
    for key in ("LLM_PROVIDER", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL",
                "LLM_JSON_MODE", "REVIEW_PROFILE", "GITHUB_TOKEN", "GITHUB_API"):
        value = env_value(text, key)
        if value and not os.getenv(key):
            os.environ[key] = value


def cmd_review(args: argparse.Namespace) -> int:
    from .runner import review_pr

    _load_env_file()

    repo = args.repo or gh_default_repo()
    if "/" not in repo:
        print(err("Не удалось определить репозиторий. Укажи --repo owner/name."))
        return 1
    owner, name = repo.split("/", 1)

    token = os.getenv("GITHUB_TOKEN") or _gh_token()
    if not token:
        print(err("Нет токена GitHub. Задай GITHUB_TOKEN или выполни gh auth login."))
        return 1

    llm = resolve_llm(
        provider=os.getenv("LLM_PROVIDER"),
        api_key=os.getenv("LLM_API_KEY", ""),
        base_url=os.getenv("LLM_BASE_URL", ""),
        model=os.getenv("LLM_MODEL", ""),
        json_mode=os.getenv("LLM_JSON_MODE"),
    )
    if llm.needs_key and (not llm.api_key or llm.api_key in ("sk-xxx", "")):
        print(err(f"Нет ключа для провайдера '{llm.provider}'. Запусти: {CMD} setup"))
        return 1

    profile = args.profile or os.getenv("REVIEW_PROFILE", DEFAULT_PROFILE)
    print(bold(f"\n🔍 Ревью {owner}/{name}#{args.pr} "
               f"(провайдер: {llm.provider}/{llm.model}, профиль: {profile})"
               f"{'  [dry-run]' if args.dry_run else ''}\n"))

    try:
        outcome = asyncio.run(review_pr(
            owner, name, args.pr,
            token=token,
            api_key=llm.api_key,
            base_url=llm.base_url,
            model=llm.model,
            provider=llm.kind,
            json_mode=llm.json_mode,
            profile=profile,
            api_base=os.getenv("GITHUB_API", "https://api.github.com"),
            allow_approve=args.approve,
            post=not args.dry_run,
        ))
    except Exception as e:  # noqa: BLE001 — surface the failure to the user
        print(err(f"Ошибка: {e}"))
        return 1

    r = outcome.result
    print(f"   вердикт: {bold(outcome.posted_event)}   inline-комментариев: {len(r.comments)}")
    if args.dry_run:
        print(f"\n{r.body}\n")
        for c in r.comments:
            print(f"   📍 {c['path']}:{c['line']}  {c['body']}")
    else:
        print(ok(f"   ✓ отправлено в https://github.com/{owner}/{name}/pull/{args.pr}"))
    return 0


# ── profile (switch review focus) ─────────────────────────────────────
def _current_profile() -> str:
    text = ENV_PATH.read_text() if ENV_PATH.exists() else ""
    return env_value(text, "REVIEW_PROFILE") or DEFAULT_PROFILE


def _read_focus(args: argparse.Namespace) -> str:
    """Resolve the focus text for a new profile: --focus, --from file, or prompt."""
    if args.focus:
        return args.focus.strip()
    if args.from_file:
        return Path(args.from_file).read_text().strip()
    if sys.stdin.isatty():
        print("   Опиши, что должен проверять профиль (заверши ввод Ctrl-D):")
        return sys.stdin.read().strip()
    return ""


def _add_profile(args: argparse.Namespace) -> int:
    name = args.add.lower()
    if is_builtin(name):
        print(err(f"'{name}' — встроенный профиль, его нельзя переопределить."))
        return 1
    path = custom_path(name)
    if path.exists():
        if sys.stdin.isatty() and not confirm(
            f"   Профиль '{name}' уже есть. Перезаписать?", default=False
        ):
            print("   отменено — для правки: "
                  f"{bold(f'{CMD} profile --edit {name}')}")
            return 0
        print(warn(f"   ⚠ перезаписываю существующий '{name}'"))
    focus = _read_focus(args)
    if not focus:
        print(err("Пустой focus — профиль не создан. Задай --focus или --from файл."))
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(focus + "\n")
    print(f"{ok('✓')} профиль {bold(name)} создан → {path}")
    print(f"   активировать: {bold(f'{CMD} profile {name}')}")
    return 0


def _edit_profile(args: argparse.Namespace) -> int:
    name = args.edit.lower()
    if is_builtin(name):
        print(err(f"'{name}' — встроенный профиль, "
                  "правится только в app/profiles.py."))
        return 1
    path = custom_path(name)

    # Non-interactive replacement: --focus / --from work like for --add.
    focus = ""
    if args.focus:
        focus = args.focus.strip()
    elif args.from_file:
        focus = Path(args.from_file).read_text().strip()
    if focus:
        existed = path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(focus + "\n")
        print(f"{ok('✓')} профиль {bold(name)} "
              f"{'обновлён' if existed else 'создан'} → {path}")
        return 0

    if not path.exists():
        print(err(f"Свой профиль '{name}' не найден. Создай: "
                  f"{CMD} profile --add {name}"))
        return 1

    # Interactive: open in $EDITOR when we have one and a real terminal.
    editor = os.getenv("EDITOR") or os.getenv("VISUAL")
    if editor and sys.stdin.isatty():
        subprocess.call([*editor.split(), str(path)])
        print(f"{ok('✓')} профиль {bold(name)} сохранён → {path}")
        return 0

    # No editor: show the current focus, read a replacement from stdin.
    print(f"   Текущий focus профиля {bold(name)}:\n")
    print(path.read_text().strip())
    print("\n   Введи новый focus (Ctrl-D — сохранить, пусто — отмена):")
    new_focus = sys.stdin.read().strip() if sys.stdin.isatty() else ""
    if not new_focus:
        print("   без изменений")
        return 0
    path.write_text(new_focus + "\n")
    print(f"{ok('✓')} профиль {bold(name)} обновлён → {path}")
    return 0


def _show_profile(args: argparse.Namespace) -> int:
    name = args.show.lower()
    profiles = load_profiles()
    if name not in profiles:
        print(err(f"Неизвестный профиль '{name}'. Доступны: {', '.join(profiles)}"))
        return 1
    kind = "встроенный" if is_builtin(name) else f"свой → {custom_path(name)}"
    print(bold(f"\nПрофиль {name} ({kind}):\n"))
    print(profiles[name].focus.strip())
    print()
    return 0


def _remove_profile(args: argparse.Namespace) -> int:
    name = args.remove.lower()
    if is_builtin(name):
        print(err(f"'{name}' — встроенный профиль, удалить нельзя."))
        return 1
    path = custom_path(name)
    if not path.exists():
        print(err(f"Свой профиль '{name}' не найден."))
        return 1
    path.unlink()
    print(f"{ok('✓')} профиль {bold(name)} удалён")
    return 0


def cmd_profile(args: argparse.Namespace) -> int:
    if args.add:
        return _add_profile(args)
    if args.edit:
        return _edit_profile(args)
    if args.show:
        return _show_profile(args)
    if args.remove:
        return _remove_profile(args)

    current = _current_profile()
    profiles = load_profiles()

    # No name → list what's available and which is active.
    if not args.name:
        print(bold("\nПрофили ревью:\n"))
        for name in profiles:
            kind = "встроенный" if is_builtin(name) else "свой"
            status = ok("● активен") if name == current else "○"
            print(f"   {name:<12}{kind:<14} {status}")
        auto_status = ok("● активен") if current == AUTO_PROFILE else "○"
        print(f"   {AUTO_PROFILE:<12}{'оркестратор':<14} {auto_status}  "
              "← сам определит направление(я) PR и запустит нужные профили")
        print(f"\nПереключить: {bold(f'{CMD} profile <имя>')}")
        print(f"Добавить свой: {bold(f'{CMD} profile --add <имя> --from focus.md')}\n")
        return 0

    name = args.name.lower()
    if name != AUTO_PROFILE and name not in profiles:
        print(err(f"Неизвестный профиль '{name}'. "
                  f"Доступны: {', '.join(profiles)}, {AUTO_PROFILE}"))
        return 1

    # Local switch (.env).
    base = ENV_PATH.read_text() if ENV_PATH.exists() else _base_env()
    _write_env(upsert_env(base, {"REVIEW_PROFILE": name}))
    print(f"{ok('✓')} профиль → {bold(name)} (.env)")
    if name == AUTO_PROFILE:
        print("   оркестратор сам определит направление(я) PR по diff и запустит "
              "подходящие профили (несколько при необходимости)")

    # Optionally sync to the GitHub Actions repo variable.
    repo = args.repo or gh_default_repo()
    if repo and gh_account():
        do_sync = args.sync or (
            sys.stdin.isatty()
            and confirm(f"Обновить и repo variable REVIEW_PROFILE в {repo}?", default=True)
        )
        if do_sync:
            r = _gh("variable", "set", "REVIEW_PROFILE", "--repo", repo, "--body", name)
            print(_step_status(r, f"variable REVIEW_PROFILE={name} → {repo}"))
    return 0


# ── provider (switch LLM backend) ─────────────────────────────────────
def _current_provider() -> str:
    text = ENV_PATH.read_text() if ENV_PATH.exists() else ""
    return env_value(text, "LLM_PROVIDER") or DEFAULT_PROVIDER


def cmd_provider(args: argparse.Namespace) -> int:
    current = _current_provider()

    # No name → list what's available and which is active.
    if not args.name:
        print(bold("\nПровайдеры LLM:\n"))
        for name in available_providers():
            p = get_provider(name)
            status = ok("● активен") if name == current else "○"
            key = "" if p.needs_key else "  (без ключа)"
            print(f"   {name:<10}{p.kind:<11}{p.default_model:<18} {status}{key}")
            print(f"   {'':<10}{p.base_url}")
        print(f"\nПереключить: {bold(f'{CMD} provider <имя>')}"
              f"  [--model M] [--base-url URL]")
        print(f"Ключ задаётся отдельно: {CMD} setup (или LLM_API_KEY в .env)\n")
        return 0

    name = args.name.lower()
    if not is_known(name):
        print(err(f"Неизвестный провайдер '{name}'. "
                  f"Доступны: {', '.join(available_providers())}"))
        return 1

    p = get_provider(name)
    updates = {
        "LLM_PROVIDER": name,
        "LLM_BASE_URL": args.base_url or p.base_url,
        "LLM_MODEL": args.model or p.default_model,
    }
    base = ENV_PATH.read_text() if ENV_PATH.exists() else _base_env()
    _write_env(upsert_env(base, updates))
    print(f"{ok('✓')} провайдер → {bold(name)} "
          f"({updates['LLM_MODEL']} @ {updates['LLM_BASE_URL']})")
    if p.needs_key:
        key = env_value(ENV_PATH.read_text(), "LLM_API_KEY")
        if not key or key == "sk-xxx":
            print(f"   {warn('!')} ключ не задан — впиши LLM_API_KEY или запусти "
                  f"{bold(f'{CMD} setup')}")
    else:
        print(f"   {ok('✓')} ключ не нужен (локальная модель). "
              f"Проверь, что сервер запущен на {updates['LLM_BASE_URL']}")
    return 0


# ── serve ─────────────────────────────────────────────────────────────
def cmd_serve(args: argparse.Namespace) -> int:
    cmd = [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(args.port)]
    if args.reload:
        cmd.append("--reload")
    print(bold(f"\n🚀 Запуск на http://localhost:{args.port}  (Ctrl+C для остановки)\n"))
    return subprocess.call(cmd, cwd=PKG_ROOT)


# ── update ────────────────────────────────────────────────────────────
def _symlink_into_install() -> Path | None:
    """~/.local/bin/pr-reviewer if it's a symlink pointing into our install."""
    link = Path.home() / ".local" / "bin" / "pr-reviewer"
    if not link.is_symlink():
        return None
    target = Path(os.readlink(link))
    base = str(paths.package_root())
    return link if str(target).startswith(base) else None


def cmd_update(_: argparse.Namespace) -> int:
    print(bold("\n⬆️  AI PR Reviewer — обновление\n"))
    if paths.is_source_checkout():
        repo = paths.package_root()
        print(f"   репозиторий: {repo}")
        rc = subprocess.call(["git", "-C", str(repo), "pull", "--ff-only"])
        if rc != 0:
            print(err("git pull не удался (локальные изменения или конфликт). "
                      "Разреши вручную и повтори."))
            return rc
        venv_pip = repo / ".venv" / "bin" / "pip"
        pip = [str(venv_pip)] if venv_pip.exists() else [sys.executable, "-m", "pip"]
        rc = subprocess.call([*pip, "install", "-q", "-e", str(repo)])
        if rc != 0:
            print(err("не удалось переустановить зависимости"))
            return rc
        print(f"\n{ok('✓')} обновлено. Проверь: {bold(f'{CMD} doctor')}")
        return 0

    # Installed as a tool (pipx).
    if shutil.which("pipx"):
        print("   обновляю через pipx…")
        rc = subprocess.call(["pipx", "upgrade", "ai-pr-reviewer"])
        if rc != 0:
            print(warn("   pipx upgrade не сработал — переустанавливаю из git…"))
            rc = subprocess.call(["pipx", "install", "--force", REPO_URL])
        if rc == 0:
            print(f"\n{ok('✓')} обновлено. Проверь: {bold('pr-reviewer doctor')}")
        return rc
    print("   установлено как пакет — обнови так:")
    print(f"   {bold('pipx upgrade ai-pr-reviewer')}")
    print(f"   # или: pipx install --force {REPO_URL}")
    return 0


# ── uninstall ─────────────────────────────────────────────────────────
def cmd_uninstall(args: argparse.Namespace) -> int:
    print(bold("\n🗑  AI PR Reviewer — удаление\n"))
    source = paths.is_source_checkout()
    force = getattr(args, "yes", False)

    def confirm_rm(msg: str) -> bool:
        return force or confirm(msg, default=False)

    # 1) global symlink — remove only if it points into our install
    link = _symlink_into_install()
    if link is not None:
        link.unlink()
        print(f"{ok('✓')} удалён симлинк {link}")

    # 2) config / user data
    if source:
        if ENV_PATH.exists() and confirm_rm(f"Удалить конфиг {ENV_PATH} (там ключ LLM)?"):
            ENV_PATH.unlink()
            print(f"{ok('✓')} удалён {ENV_PATH}")
    else:
        home = paths.home_dir()
        if home.exists() and confirm_rm(f"Удалить конфиг и свои профили ({home})?"):
            shutil.rmtree(home)
            print(f"{ok('✓')} удалён {home}")

    # 3) how to finish removing the program itself
    print(bold("\nЧтобы удалить сам сервис:"))
    if source:
        print("   ./uninstall.sh                        # удалит .venv и симлинк")
        print(f"   rm -rf {paths.package_root()}   # и папку репозитория")
    else:
        print("   pipx uninstall ai-pr-reviewer")
    return 0


# ── install-workflow ──────────────────────────────────────────────────
def cmd_install_workflow(args: argparse.Namespace) -> int:
    print(bold("\n⚙️  GitHub Actions — workflow авто-ревью\n"))
    if getattr(args, "print", False) or not gh_available():
        if not gh_available():
            print(f"   {warn('!')} gh не найден. Скопируй в {WORKFLOW_REL} "
                  "целевого репозитория:\n")
        print(EXTERNAL_WORKFLOW)
        return 0
    repo = args.repo or gh_default_repo()
    if "/" not in repo:
        print(err("Не удалось определить репозиторий. Укажи --repo owner/name."))
        return 1
    r = _install_workflow(repo)
    print(_step_status(r, f"workflow {WORKFLOW_REL} → {repo}"))
    if r.returncode == 0:
        print(ok("   Готово — открой PR, ревью запустится автоматически."))
        return 0
    print(f"   {warn('!')} не удалось закоммитить (ветка защищена?). "
          f"Добавь вручную в {WORKFLOW_REL}:\n")
    print(EXTERNAL_WORKFLOW)
    return 1


# ── uninstall-workflow ────────────────────────────────────────────────
def cmd_uninstall_workflow(args: argparse.Namespace) -> int:
    print(bold("\n🧹 GitHub Actions — удаление авто-ревью\n"))
    if not gh_available():
        print(err("gh не найден — удали workflow, секрет и переменные вручную "
                  "в настройках репозитория."))
        return 1
    repo = args.repo or gh_default_repo()
    if "/" not in repo:
        print(err("Не удалось определить репозиторий. Укажи --repo owner/name."))
        return 1
    if not getattr(args, "yes", False) and not confirm(
        f"   Удалить workflow, секрет и переменные ревью из {repo}?", default=False
    ):
        print("   отменено")
        return 0

    print(_step_status(_remove_workflow(repo), f"workflow {WORKFLOW_REL}"))
    print(_step_status(_gh("secret", "delete", ACTIONS_SECRET, "--repo", repo),
                       f"секрет {ACTIONS_SECRET}"))
    for var in ACTIONS_VARS:
        print(_step_status(_gh("variable", "delete", var, "--repo", repo),
                           f"variable {var}"))
    print(ok(f"\n✓ Готово — {repo} очищен от авто-ревью."))
    return 0


# ── entrypoint ────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pr-reviewer",
        description="AI PR Reviewer — AI код-ревью PR (Android по умолчанию, настраивается под любое направление).",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("setup", help="интерактивная настройка (ключ, профиль, Actions)")
    sub.add_parser("doctor", help="проверить, что всё настроено")

    p_review = sub.add_parser("review", help="разовое ревью PR из терминала")
    p_review.add_argument("--repo", default="", help="owner/name (по умолчанию — текущий репозиторий)")
    p_review.add_argument("--pr", type=int, required=True, help="номер pull request")
    p_review.add_argument("--profile", default="", help="профиль ревью (android/kmp)")
    p_review.add_argument("--approve", action="store_true", help="разрешить вердикт APPROVE")
    p_review.add_argument("--dry-run", action="store_true", help="показать ревью, не постить")

    p_profile = sub.add_parser("profile", help="показать/переключить/добавить профиль ревью")
    p_profile.add_argument("name", nargs="?", default="", help="имя профиля для переключения")
    p_profile.add_argument("--add", default="", metavar="NAME", help="создать свой профиль")
    p_profile.add_argument("--edit", default="", metavar="NAME", help="редактировать свой профиль ($EDITOR / --focus / --from)")
    p_profile.add_argument("--show", default="", metavar="NAME", help="показать focus профиля")
    p_profile.add_argument("--remove", default="", metavar="NAME", help="удалить свой профиль")
    p_profile.add_argument("--focus", default="", help="текст focus для --add/--edit (иначе --from или ввод)")
    p_profile.add_argument("--from", dest="from_file", default="", help="файл с текстом focus для --add/--edit")
    p_profile.add_argument("--repo", default="", help="owner/name для синхронизации repo variable")
    p_profile.add_argument("--sync", action="store_true", help="без вопроса обновить repo variable")

    p_provider = sub.add_parser("provider", help="показать/переключить LLM-провайдера (DeepSeek/OpenAI/Claude/локально)")
    p_provider.add_argument("name", nargs="?", default="", help="deepseek | openai | claude | local")
    p_provider.add_argument("--model", default="", help="переопределить модель")
    p_provider.add_argument("--base-url", dest="base_url", default="", help="переопределить endpoint (для local/self-hosted)")

    p_serve = sub.add_parser("serve", help="запустить webhook-сервис локально")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--reload", action="store_true", help="автоперезагрузка (dev)")

    p_wf = sub.add_parser("install-workflow", help="добавить GitHub Actions workflow авто-ревью в репозиторий")
    p_wf.add_argument("--repo", default="", help="owner/name (по умолчанию — текущий)")
    p_wf.add_argument("--print", action="store_true", help="только показать YAML, не коммитить")

    p_rmwf = sub.add_parser("uninstall-workflow", help="удалить авто-ревью из репозитория (workflow, секрет, переменные)")
    p_rmwf.add_argument("--repo", default="", help="owner/name (по умолчанию — текущий)")
    p_rmwf.add_argument("-y", "--yes", action="store_true", help="не спрашивать подтверждение")

    sub.add_parser("update", help="обновить сервис (git pull + переустановка или pipx upgrade)")

    p_uninstall = sub.add_parser("uninstall", help="удалить сервис (симлинк, конфиг; подскажет про .venv/pipx)")
    p_uninstall.add_argument("-y", "--yes", action="store_true", help="не спрашивать про удаление конфига")

    sub.add_parser("help", help="показать список команд и их описание")
    return parser


COMMANDS_HELP = """\
🤖 AI PR Reviewer — команды

  setup                 интерактивная настройка: провайдер LLM, ключ (скрытый
                        ввод), профиль ревью и, при желании, GitHub Actions
  doctor                проверить, что всё настроено (ключ, профиль, gh, workflow)
  review --pr N         разовое ревью pull request прямо из терминала
                          --repo owner/name   репозиторий (по умолчанию текущий)
                          --profile android|compose|kmp|auto
                          --dry-run           показать ревью, ничего не постя
                          --approve           разрешить вердикт APPROVE
  profile [имя]         показать или переключить профиль ревью
                          встроенные: android | compose | kmp (+ свои)
                          auto                  оркестратор: сам определит
                                                направление(я) PR и запустит
                                                нужные профили (или несколько)
                          --sync                обновить и repo variable (для Actions)
                          --add NAME --from f   создать свой профиль из файла
                          --add NAME --focus "" создать свой профиль из текста
                          --show NAME           показать focus профиля
                          --edit NAME           править свой профиль ($EDITOR / --focus / --from)
                          --remove NAME         удалить свой профиль
  provider [имя]        показать или переключить LLM-провайдера
                          deepseek | openai | claude | local
                          --model M             переопределить модель
                          --base-url URL        endpoint (для local/self-hosted)
  serve [--port --reload]   запустить webhook-сервис локально
  install-workflow      добавить GitHub Actions workflow авто-ревью в репозиторий
                          --repo owner/name   целевой репозиторий (по умолч. текущий)
                          --print             показать YAML, не коммитить
  uninstall-workflow    удалить авто-ревью из репозитория (workflow + секрет + переменные)
                          --repo owner/name   целевой репозиторий (по умолч. текущий)
                          -y                  без подтверждения
  update                обновить сервис (git pull + переустановка или pipx upgrade)
  uninstall [-y]        удалить сервис: симлинк и конфиг (подскажет про .venv/pipx)
  help                  этот экран

Примеры:
  pr-reviewer setup
  pr-reviewer update               # подтянуть свежую версию
  pr-reviewer uninstall            # удалить сервис
  pr-reviewer provider             # список провайдеров и активный
  pr-reviewer provider claude      # переключить на Claude API
  pr-reviewer provider local       # локальная модель (Ollama и т.п.)
  pr-reviewer profile compose      # переключить на Jetpack Compose
  pr-reviewer profile auto         # оркестратор — сам выберет профиль(и)
  pr-reviewer profile --add security --from security.md   # свой профиль
  pr-reviewer review --pr 1 --profile auto --dry-run
"""


def cmd_help(_: argparse.Namespace) -> int:
    # In a checkout the command is ./pr-reviewer (launcher), not a global name.
    text = COMMANDS_HELP if CMD == "pr-reviewer" else COMMANDS_HELP.replace(
        "pr-reviewer ", f"{CMD} ")
    print(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "setup": cmd_setup,
        "doctor": cmd_doctor,
        "review": cmd_review,
        "profile": cmd_profile,
        "provider": cmd_provider,
        "serve": cmd_serve,
        "install-workflow": cmd_install_workflow,
        "uninstall-workflow": cmd_uninstall_workflow,
        "update": cmd_update,
        "uninstall": cmd_uninstall,
        "help": cmd_help,
    }
    handler = handlers.get(args.command)
    if handler is None:
        print(COMMANDS_HELP)
        return 0
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())

"""Android PR Reviewer — friendly command-line interface.

Commands:
    pr-reviewer setup     interactive wizard: API key (hidden), profile, GitHub Actions
    pr-reviewer doctor     check that everything is configured
    pr-reviewer serve      run the webhook service locally

The setup wizard reads the DeepSeek key with getpass (input is hidden, like a
password prompt) and, when `gh` is authenticated, offers to wire up GitHub
Actions for you — setting the repo secret via stdin so the key never appears in
the process list.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .profiles import (
    DEFAULT_PROFILE,
    available_profiles,
    custom_path,
    is_builtin,
    load_profiles,
)

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
WORKFLOW = ROOT / ".github" / "workflows" / "ai-review.yml"


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


# ── setup wizard ──────────────────────────────────────────────────────
def cmd_setup(_: argparse.Namespace) -> int:
    print(bold("\n🤖 Android PR Reviewer — настройка\n"))
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

    # 2. DeepSeek key (hidden input)
    print(bold("\n2) Ключ DeepSeek"))
    current = env_value(env_text, "LLM_API_KEY")
    has_key = current not in ("", "sk-xxx")
    new_key = ""
    if has_key and not confirm("   Ключ уже есть в .env. Перезаписать?", default=False):
        print("   оставляю текущий")
    else:
        new_key = getpass.getpass("   Введите ключ DeepSeek (ввод скрыт): ").strip()
        while not new_key and confirm("   Пусто. Ввести снова?", default=True):
            new_key = getpass.getpass("   Введите ключ DeepSeek (ввод скрыт): ").strip()

    # 3. review profile
    print(bold("\n3) Профиль ревью"))
    names = available_profiles()
    for i, name in enumerate(names, 1):
        mark = " (по умолчанию)" if name == DEFAULT_PROFILE else ""
        print(f"   {i}. {name}{mark}")
    raw = ask("   Выбор", str(names.index(DEFAULT_PROFILE) + 1))
    try:
        profile = names[int(raw) - 1]
    except (ValueError, IndexError):
        profile = DEFAULT_PROFILE

    # write .env
    updates = {"REVIEW_PROFILE": profile}
    if new_key:
        updates["LLM_API_KEY"] = new_key
    ENV_PATH.write_text(upsert_env(env_text, updates))
    saved = ", ключ сохранён" if new_key else ""
    print(f"   {ok('✓')} записал .env (профиль={profile}{saved})")

    # 4. GitHub Actions
    print(bold("\n4) GitHub Actions"))
    key_for_secret = new_key or env_value(ENV_PATH.read_text(), "LLM_API_KEY")
    if account and confirm(
        "   Настроить авто-ревью на PR через GitHub Actions?", default=True
    ):
        repo = ask("   Репозиторий (owner/name)", gh_default_repo())
        if repo:
            if key_for_secret and key_for_secret != "sk-xxx":
                r = _gh("secret", "set", "DEEPSEEK_API_KEY", "--repo", repo,
                        stdin=key_for_secret)
                print(_step_status(r, f"секрет DEEPSEEK_API_KEY → {repo}"))
            else:
                print(f"   {warn('!')} ключ неизвестен — задай секрет позже: "
                      f"gh secret set DEEPSEEK_API_KEY --repo {repo}")
            r = _gh("variable", "set", "REVIEW_PROFILE", "--repo", repo,
                    "--body", profile)
            print(_step_status(r, f"variable REVIEW_PROFILE={profile}"))
            mark = ok("✓") if WORKFLOW.exists() else warn("!")
            state = "на месте" if WORKFLOW.exists() else "отсутствует (см. .github/workflows/)"
            print(f"   {mark} workflow ai-review.yml {state}")
            print(ok("   Готово — открой PR, ревью запустится автоматически."))
        else:
            print(f"   {warn('!')} репозиторий не указан — пропускаю")
    elif not account:
        print("   Пропущено (нужен авторизованный gh). "
              "Позже: gh auth login, затем pr-reviewer setup")

    # summary
    print(bold("\n✅ Готово. Дальше:"))
    print("   pr-reviewer doctor      # проверить настройку")
    print("   pr-reviewer serve       # локальный webhook-сервис")
    print("   pr-reviewer serve и туннель (ngrok/smee) — см. README")
    return 0


# ── doctor ────────────────────────────────────────────────────────────
def cmd_doctor(_: argparse.Namespace) -> int:
    print(bold("\n🩺 Android PR Reviewer — проверка\n"))
    env_text = ENV_PATH.read_text() if ENV_PATH.exists() else ""

    def check(label: str, good: bool, hint: str = "") -> None:
        icon = ok("✓") if good else err("✗")
        extra = "" if good else f"  → {hint}"
        print(f"   {icon} {label}{extra}")

    check(".env существует", ENV_PATH.exists(), "запусти: pr-reviewer setup")
    key = env_value(env_text, "LLM_API_KEY")
    check("ключ DeepSeek задан", key not in ("", "sk-xxx"), "pr-reviewer setup")
    profile = env_value(env_text, "REVIEW_PROFILE") or DEFAULT_PROFILE
    names = available_profiles()
    check(f"профиль ревью: {profile}", profile in names,
          f"неизвестный профиль, доступны: {', '.join(names)}")
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
    for key in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "REVIEW_PROFILE",
                "GITHUB_TOKEN", "GITHUB_API"):
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

    api_key = os.getenv("LLM_API_KEY", "")
    if not api_key or api_key == "sk-xxx":
        print(err("Нет ключа DeepSeek. Запусти: pr-reviewer setup"))
        return 1

    profile = args.profile or os.getenv("REVIEW_PROFILE", DEFAULT_PROFILE)
    print(bold(f"\n🔍 Ревью {owner}/{name}#{args.pr} (профиль: {profile})"
               f"{'  [dry-run]' if args.dry_run else ''}\n"))

    try:
        outcome = asyncio.run(review_pr(
            owner, name, args.pr,
            token=token,
            api_key=api_key,
            base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com"),
            model=os.getenv("LLM_MODEL", "deepseek-chat"),
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
    focus = _read_focus(args)
    if not focus:
        print(err("Пустой focus — профиль не создан. Задай --focus или --from файл."))
        return 1
    path = custom_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(focus + "\n")
    print(f"{ok('✓')} профиль {bold(name)} создан → {path}")
    print(f"   активировать: {bold(f'pr-reviewer profile {name}')}")
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
        print(f"\nПереключить: {bold('pr-reviewer profile <имя>')}")
        print(f"Добавить свой: {bold('pr-reviewer profile --add <имя> --from focus.md')}\n")
        return 0

    name = args.name.lower()
    if name not in profiles:
        print(err(f"Неизвестный профиль '{name}'. Доступны: {', '.join(profiles)}"))
        return 1

    # Local switch (.env).
    base = ENV_PATH.read_text() if ENV_PATH.exists() else _base_env()
    ENV_PATH.write_text(upsert_env(base, {"REVIEW_PROFILE": name}))
    print(f"{ok('✓')} профиль → {bold(name)} (.env)")

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


# ── serve ─────────────────────────────────────────────────────────────
def cmd_serve(args: argparse.Namespace) -> int:
    cmd = [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(args.port)]
    if args.reload:
        cmd.append("--reload")
    print(bold(f"\n🚀 Запуск на http://localhost:{args.port}  (Ctrl+C для остановки)\n"))
    return subprocess.call(cmd, cwd=ROOT)


# ── entrypoint ────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pr-reviewer",
        description="Android PR Reviewer — AI код-ревью для Android/KMP pull request'ов.",
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
    p_profile.add_argument("--remove", default="", metavar="NAME", help="удалить свой профиль")
    p_profile.add_argument("--focus", default="", help="текст focus для --add (иначе --from или ввод)")
    p_profile.add_argument("--from", dest="from_file", default="", help="файл с текстом focus для --add")
    p_profile.add_argument("--repo", default="", help="owner/name для синхронизации repo variable")
    p_profile.add_argument("--sync", action="store_true", help="без вопроса обновить repo variable")

    p_serve = sub.add_parser("serve", help="запустить webhook-сервис локально")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--reload", action="store_true", help="автоперезагрузка (dev)")

    sub.add_parser("help", help="показать список команд и их описание")
    return parser


COMMANDS_HELP = """\
🤖 Android PR Reviewer — команды

  setup                 интерактивная настройка: ключ DeepSeek (скрытый ввод),
                        профиль ревью и, при желании, GitHub Actions
  doctor                проверить, что всё настроено (ключ, профиль, gh, workflow)
  review --pr N         разовое ревью pull request прямо из терминала
                          --repo owner/name   репозиторий (по умолчанию текущий)
                          --profile android|compose|kmp
                          --dry-run           показать ревью, ничего не постя
                          --approve           разрешить вердикт APPROVE
  profile [имя]         показать или переключить профиль ревью
                          встроенные: android | compose | kmp (+ свои)
                          --sync                обновить и repo variable (для Actions)
                          --add NAME --from f   создать свой профиль из файла
                          --add NAME --focus "" создать свой профиль из текста
                          --remove NAME         удалить свой профиль
  serve [--port --reload]   запустить webhook-сервис локально
  help                  этот экран

Примеры:
  pr-reviewer setup
  pr-reviewer profile              # список и активный
  pr-reviewer profile compose      # переключить на Jetpack Compose
  pr-reviewer profile --add security --from security.md   # свой профиль
  pr-reviewer review --pr 1 --dry-run
  pr-reviewer review --repo Sermage/pr-reviewer --pr 1
"""


def cmd_help(_: argparse.Namespace) -> int:
    print(COMMANDS_HELP)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "setup": cmd_setup,
        "doctor": cmd_doctor,
        "review": cmd_review,
        "profile": cmd_profile,
        "serve": cmd_serve,
        "help": cmd_help,
    }
    handler = handlers.get(args.command)
    if handler is None:
        print(COMMANDS_HELP)
        return 0
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())

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
import getpass
import shutil
import subprocess
import sys
from pathlib import Path

from .profiles import DEFAULT_PROFILE, PROFILES

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
        print(f"   {warn('!')} gh установлен, но не авторизован — выполни: gh auth login")
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
    names = list(PROFILES)
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
    check(f"профиль ревью: {profile}", profile in PROFILES,
          f"неизвестный профиль, доступны: {', '.join(PROFILES)}")
    check("gh авторизован", gh_account() is not None, "gh auth login")
    check("workflow ai-review.yml", WORKFLOW.exists(), "восстанови .github/workflows/")
    print()
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
    p_serve = sub.add_parser("serve", help="запустить webhook-сервис локально")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--reload", action="store_true", help="автоперезагрузка (dev)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {"setup": cmd_setup, "doctor": cmd_doctor, "serve": cmd_serve}
    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        return 0
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())

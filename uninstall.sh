#!/usr/bin/env bash
# Uninstaller for the repository (source) install: removes the global symlink and
# the local .venv, and optionally your config (.env). Re-runnable (idempotent).
# Installed via pipx instead? Use: pipx uninstall ai-pr-reviewer
set -euo pipefail
cd "$(dirname "$0")"
REPO="$PWD"

ask() {  # ask "question" → 0 for yes, 1 otherwise (non-interactive: no)
  if [ -t 0 ] && [ -t 1 ]; then
    read -r -p "$1 [y/N] " a
    case "${a:-N}" in [Yy]*) return 0 ;; *) return 1 ;; esac
  fi
  return 1
}

echo "🗑  AI PR Reviewer — удаление (репозиторий)"
echo

# 1) global symlink — remove only if it points into THIS repo
LINK="${HOME}/.local/bin/pr-reviewer"
if [ -L "$LINK" ]; then
  target="$(readlink "$LINK")"
  case "$target" in
    "$REPO"/*) rm -f "$LINK"; echo "✓ удалён симлинк $LINK" ;;
    *) echo "! $LINK ведёт не в этот репозиторий ($target) — не трогаю" ;;
  esac
fi

# 2) local virtualenv
if [ -d .venv ]; then
  rm -rf .venv
  echo "✓ удалено окружение .venv"
fi

# 3) config / secrets — opt-in (default: keep)
if [ -f .env ]; then
  echo
  if ask "Удалить конфиг .env (там ключ LLM)?"; then
    rm -f .env
    echo "✓ .env удалён"
  else
    echo "  оставил .env (удали вручную при необходимости)"
  fi
fi

echo
echo "✓ Готово. Саму папку репозитория можно удалить: rm -rf \"$REPO\""
echo "  (ставил через pipx? тогда: pipx uninstall ai-pr-reviewer)"

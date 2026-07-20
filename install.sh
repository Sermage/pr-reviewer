#!/usr/bin/env bash
# One-shot installer: creates a local venv, installs the package, and offers
# to run the setup wizard. Re-runnable (idempotent).
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"

if [ ! -d .venv ]; then
  echo "→ создаю виртуальное окружение (.venv)…"
  "$PY" -m venv .venv
fi

echo "→ устанавливаю зависимости…"
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/pip install -q -e .
echo "✓ установлено"

# ./pr-reviewer (committed launcher) runs the CLI without activating the venv.
if [ -t 0 ] && [ -t 1 ]; then
  echo
  read -r -p "Запустить настройку сейчас (./pr-reviewer setup)? [Y/n] " ans
  case "${ans:-Y}" in
    [Nn]*) echo "Позже: ./pr-reviewer setup" ;;
    *) exec .venv/bin/pr-reviewer setup ;;
  esac
else
  echo "Дальше: ./pr-reviewer setup"
fi

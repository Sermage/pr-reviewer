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

# Global command: symlink the venv entry point into ~/.local/bin so the user can
# run `pr-reviewer` from anywhere (not only ./pr-reviewer here). We link the venv
# binary directly — its shebang is an absolute path to .venv/bin/python, so it
# works regardless of the current directory.
BIN_DIR="${HOME}/.local/bin"
LINK="$BIN_DIR/pr-reviewer"
TARGET="$PWD/.venv/bin/pr-reviewer"
mkdir -p "$BIN_DIR"
ln -sf "$TARGET" "$LINK"
echo "✓ команда pr-reviewer → $LINK"
case ":$PATH:" in
  *":$BIN_DIR:"*)
    echo "  доступна глобально: pr-reviewer <команда>" ;;
  *)
    echo "  ! $BIN_DIR не в PATH. Чтобы команда работала глобально, добавь:"
    echo "      echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc && source ~/.zshrc"
    echo "    (без этого используй ./pr-reviewer в этой папке)" ;;
esac

# ./pr-reviewer (committed launcher) also runs the CLI without activating the venv.
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

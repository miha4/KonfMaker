#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
VENV_DIR="${BACKEND_VENV_DIR:-$BACKEND_DIR/.venv}"

find_python() {
  if [[ -n "${PYTHON:-}" ]]; then
    printf '%s\n' "$PYTHON"
    return
  fi
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return
  fi
  if command -v python >/dev/null 2>&1; then
    command -v python
    return
  fi
  echo "Python 3 ni najden. Namesti Python 3 in poskusi ponovno." >&2
  exit 1
}

PYTHON_BIN="$(find_python)"

if [[ ! -d "$VENV_DIR" ]]; then
  echo "Creating backend virtual environment at $VENV_DIR ..."
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

VENV_PYTHON="$VENV_DIR/bin/python"
if [[ ! -x "$VENV_PYTHON" && -x "$VENV_DIR/Scripts/python.exe" ]]; then
  VENV_PYTHON="$VENV_DIR/Scripts/python.exe"
fi

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "Virtual environment Python ni najden v $VENV_DIR." >&2
  exit 1
fi

echo "Checking desktop backend dependencies..."
if ! "$VENV_PYTHON" - <<'PY' >/dev/null 2>&1
import fastapi
import ortools
import pydantic
import uvicorn
PY
then
  "$VENV_PYTHON" -m pip install -r "$BACKEND_DIR/requirements.txt"
fi

echo "Desktop backend is ready."

#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
VENV_DIR="${BACKEND_DESKTOP_BUNDLE_VENV_DIR:-$BACKEND_DIR/.desktop-venv}"

find_python() {
  if [[ -n "${PYTHON:-}" ]]; then
    printf '%s\n' "$PYTHON"
    return
  fi
  if [[ -x "$BACKEND_DIR/.venv/bin/python" ]]; then
    printf '%s\n' "$BACKEND_DIR/.venv/bin/python"
    return
  fi
  if [[ -x "$BACKEND_DIR/.venv/Scripts/python.exe" ]]; then
    printf '%s\n' "$BACKEND_DIR/.venv/Scripts/python.exe"
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

python_too_old() {
  "$1" - <<'PY'
import sys
raise SystemExit(0 if sys.version_info < (3, 10) else 1)
PY
}

PYTHON_BIN="$(find_python)"

if [[ -x "$VENV_DIR/bin/python" ]] && python_too_old "$VENV_DIR/bin/python"; then
  echo "Removing outdated backend bundle virtual environment at $VENV_DIR ..."
  rm -rf "$VENV_DIR"
fi

if [[ ! -d "$VENV_DIR" ]]; then
  echo "Creating backend bundle virtual environment at $VENV_DIR ..."
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

VENV_PYTHON="$VENV_DIR/bin/python"
if [[ ! -x "$VENV_PYTHON" && -x "$VENV_DIR/Scripts/python.exe" ]]; then
  VENV_PYTHON="$VENV_DIR/Scripts/python.exe"
fi

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "Bundle virtual environment Python ni najden v $VENV_DIR." >&2
  exit 1
fi

"$VENV_PYTHON" -m pip install --upgrade pip setuptools wheel
"$VENV_PYTHON" -m pip install -r "$BACKEND_DIR/requirements.txt" pyinstaller

cd "$ROOT_DIR"
"$VENV_PYTHON" -m PyInstaller \
  --clean \
  --noconfirm \
  --onefile \
  --name atcconfmaker-engine \
  --paths "$BACKEND_DIR" \
  --collect-all ortools \
  --collect-submodules uvicorn \
  --collect-submodules fastapi \
  --collect-submodules pydantic \
  --distpath "$BACKEND_DIR/dist" \
  --workpath "$BACKEND_DIR/build" \
  "$ROOT_DIR/electron/backend_entry.py"

echo "Backend sidecar built in $BACKEND_DIR/dist"

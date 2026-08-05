#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
VENV_DIR="${BACKEND_VENV_DIR:-}"
BACKEND_HOST="${BACKEND_HOST:-0.0.0.0}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_HOST="${FRONTEND_HOST:-0.0.0.0}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

python_is_supported() {
  local candidate="$1"
  [[ -x "$candidate" ]] && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

find_python() {
  local candidate
  if [[ -n "${PYTHON:-}" ]] && python_is_supported "$PYTHON"; then
    printf '%s\n' "$PYTHON"
    return
  fi
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      candidate="$(command -v "$candidate")"
      if python_is_supported "$candidate"; then
        printf '%s\n' "$candidate"
        return
      fi
    fi
  done
  echo "Python 3.10+ ni najden. Namesti Python 3.12 in poskusi ponovno." >&2
  exit 1
}

if [[ -z "$VENV_DIR" ]]; then
  if python_is_supported "$BACKEND_DIR/.desktop-venv/bin/python"; then
    VENV_DIR="$BACKEND_DIR/.desktop-venv"
  else
    VENV_DIR="$BACKEND_DIR/.venv"
  fi
fi

cleanup() {
  if [[ -n "${BACKEND_PID:-}" ]] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    kill "$BACKEND_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

if python_is_supported "$VENV_DIR/bin/python"; then
  PYTHON_BIN="$VENV_DIR/bin/python"
else
  PYTHON_BIN="$(find_python)"
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "npm ni najden. Namesti Node.js LTS in poskusi ponovno." >&2
  exit 1
fi

if [[ ! -d "$VENV_DIR" ]]; then
  echo "Creating backend virtual environment at $VENV_DIR ..."
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

VENV_PYTHON="$VENV_DIR/bin/python"
if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "Virtual environment Python ni najden na $VENV_PYTHON." >&2
  echo "Izbriši $VENV_DIR in ponovno zaženi skripto." >&2
  exit 1
fi
if ! python_is_supported "$VENV_PYTHON"; then
  echo "Virtual environment $VENV_DIR uporablja prestar Python. Zahtevan je Python 3.10+." >&2
  exit 1
fi

echo "Checking backend dependencies..."
if ! "$VENV_PYTHON" - <<'PY' >/dev/null 2>&1
import fastapi
import pydantic
import uvicorn
PY
then
  "$VENV_PYTHON" -m pip install -r "$BACKEND_DIR/requirements.txt"
fi

if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
  echo "Installing frontend dependencies..."
  (cd "$FRONTEND_DIR" && npm install)
fi

echo "Starting backend on http://127.0.0.1:$BACKEND_PORT ..."
(cd "$BACKEND_DIR" && "$VENV_PYTHON" -m uvicorn app.main:app --reload --host "$BACKEND_HOST" --port "$BACKEND_PORT") &
BACKEND_PID=$!

echo "Waiting for backend health check..."
for _ in {1..40}; do
  if "$VENV_PYTHON" - <<PY >/dev/null 2>&1
from urllib.request import urlopen
urlopen('http://127.0.0.1:$BACKEND_PORT/api/health', timeout=1).read()
PY
  then
    break
  fi
  sleep 0.25
done

echo "Starting frontend on http://127.0.0.1:$FRONTEND_PORT ..."
echo "Local Mac: open http://localhost:$FRONTEND_PORT"
echo "Codespaces: open the forwarded URL for port $FRONTEND_PORT"
(cd "$FRONTEND_DIR" && VITE_API_PROXY_TARGET="http://127.0.0.1:$BACKEND_PORT" npm run dev -- --host "$FRONTEND_HOST" --port "$FRONTEND_PORT")

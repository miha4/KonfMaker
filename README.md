# KonfMaker

KonfMaker is a web-based planning prototype for calculating maximum sector hours for an ATC daily configuration.

## Stack

- Frontend: React + TypeScript + Vite
- Backend: Python + FastAPI

## Requirements

Install these once on your machine:

- **Python 3.11+** (macOS: `python3 --version`)
- **Node.js LTS + npm** (macOS: `node --version` and `npm --version`)

The project creates and reuses a backend virtual environment at `backend/.venv`. That folder is ignored by git.

## One-command start: local Mac or GitHub Codespaces

From the repository root:

```bash
npm start
```

This runs `./scripts/start-dev.sh`, which:

1. finds `python3` / `python`,
2. creates `backend/.venv` if it does not exist,
3. installs missing backend dependencies from `backend/requirements.txt`,
4. installs frontend dependencies if `frontend/node_modules` is missing,
5. starts FastAPI on port `8000`,
6. starts Vite on port `5173`.

Open the app at:

- **Local Mac:** <http://localhost:5173>
- **Codespaces:** forwarded URL for port `5173`

The frontend calls relative `/api` URLs. Vite proxies those calls to `http://127.0.0.1:8000`, so both local and Codespaces development use the same browser-safe path.

### Useful environment overrides

If ports are busy, you can override them:

```bash
BACKEND_PORT=8010 FRONTEND_PORT=5174 npm start
```

If you want to choose a specific Python executable:

```bash
PYTHON=/opt/homebrew/bin/python3 npm start
```

If you want the virtual environment somewhere else:

```bash
BACKEND_VENV_DIR=/path/to/venv npm start
```

## Manual backend start

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Manual frontend start

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>. During development Vite proxies `/api` requests to `http://127.0.0.1:8000`.

## GitHub Codespaces notes

The recommended Codespaces setup is to open only the forwarded URL for port `5173`; the Vite dev server forwards `/api` requests to FastAPI internally. You can still override the API base URL manually if needed:

```bash
cd frontend
VITE_API_BASE_URL=https://your-codespace-name-8000.app.github.dev npm run dev
```

The backend also allows browser requests from GitHub's forwarded `*.app.github.dev` URLs during development, but the Vite proxy path is preferred because it is same-origin from the browser's point of view.

## Current MVP

The implemented program is **Kalkulator sektorskih ur**. It supports:

- calculating coverage from entered staff counts,
- calculating a generated staff plan from requested hourly sector openness,
- APS/ACS/FL licence split,
- paired lower/upper sector assignments,
- editable shift and rest rules.

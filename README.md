# KonfMaker

KonfMaker is a web-based planning prototype for calculating maximum sector hours for an ATC daily configuration.

## Stack

- Frontend: React + TypeScript + Vite
- Backend: Python + FastAPI

## Run locally

### Quick start in GitHub Codespaces

From the repository root, run one command:

```bash
./scripts/start-codespaces.sh
```

The script prepares the backend virtual environment if needed, installs missing frontend packages, starts the backend on port `8000`, and starts the frontend on port `5173`. In the **Ports** panel, open the forwarded URL for port `5173`.

### Manual backend start

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### Manual frontend start

```bash
cd frontend
npm install
npm run dev
```

The frontend expects the API at `http://localhost:8000` by default. Override with `VITE_API_BASE_URL` if needed.

### GitHub Codespaces notes

The frontend automatically detects GitHub's forwarded `*.app.github.dev` URL and calls the matching backend URL on port `8000`. You can still override this manually if needed:

```bash
cd frontend
VITE_API_BASE_URL=https://your-codespace-name-8000.app.github.dev npm run dev
```

The backend allows browser requests from GitHub's forwarded `*.app.github.dev` URLs during development.

## Current MVP

The first implemented program is **Kalkulator sektorskih ur**. It accepts total people, FL count, ACS count, FMP requirement and editable fixed rules. It returns feasibility, estimated maximum sector hours, a generated virtual staff list, shift distribution and hourly coverage.

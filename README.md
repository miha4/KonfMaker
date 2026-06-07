# KonfMaker

KonfMaker is a web-based planning prototype for calculating maximum sector hours for an ATC daily configuration.

## Stack

- Frontend: React + TypeScript + Vite
- Backend: Python + FastAPI

## Run locally

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The frontend expects the API at `http://localhost:8000` by default. Override with `VITE_API_BASE_URL` if needed.

### GitHub Codespaces

When running in Codespaces, start both ports:

1. Start the backend on port `8000`.
2. Start the frontend on port `5173`.
3. Open the forwarded frontend URL for port `5173`.

The frontend automatically detects GitHub's forwarded `*.app.github.dev` URL and calls the matching backend URL on port `8000`. You can still override this manually if needed:

```bash
cd frontend
VITE_API_BASE_URL=https://your-codespace-name-8000.app.github.dev npm run dev
```

The backend allows browser requests from GitHub's forwarded `*.app.github.dev` URLs during development.

## Current MVP

The first implemented program is **Kalkulator sektorskih ur**. It accepts total people, FL count, ACS count, FMP requirement and editable fixed rules. It returns feasibility, estimated maximum sector hours, a generated virtual staff list, shift distribution and hourly coverage.

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

## Current MVP

The first implemented program is **Kalkulator sektorskih ur**. It accepts total people, FL count, ACS count, FMP requirement and editable fixed rules. It returns feasibility, estimated maximum sector hours, a generated virtual staff list, shift distribution and hourly coverage.

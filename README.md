# KonfMaker

KonfMaker is a web-based planning prototype for calculating maximum sector hours for an ATC daily configuration.

## Stack

- Frontend: React + TypeScript + Vite
- Backend: Python + FastAPI

## Run locally

### Quick start in GitHub Codespaces

From the repository root, run one command:

```bash
npm start
```

The same startup flow is also available directly as `./scripts/start-codespaces.sh`.

The script prepares the backend virtual environment if needed, installs missing frontend packages, starts the backend on port `8000`, and starts the frontend on port `5173`. In the **Ports** panel, open the forwarded URL for port `5173`. The frontend calls `/api` on its own origin and Vite proxies those requests to the backend, which avoids GitHub Codespaces cross-port CORS issues.

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

During development the frontend calls relative `/api` URLs. Vite proxies those requests to `http://127.0.0.1:8000`, so local and Codespaces development do not require the browser to call a separate backend origin. Override with `VITE_API_BASE_URL` only if you intentionally want to bypass the Vite proxy.

### GitHub Codespaces notes

The recommended Codespaces setup is to open only the forwarded URL for port `5173`; the Vite dev server forwards `/api` requests to FastAPI internally. You can still override this manually if needed:

```bash
cd frontend
VITE_API_BASE_URL=https://your-codespace-name-8000.app.github.dev npm run dev
```

The backend also allows browser requests from GitHub's forwarded `*.app.github.dev` URLs during development, but the Vite proxy path is preferred because it is same-origin from the browser's point of view.

## Current MVP

The first implemented program is **Kalkulator sektorskih ur**. It accepts total people, FL count, ACS count, FMP requirement and editable fixed rules. It returns feasibility, estimated maximum sector hours, a generated virtual staff list, shift distribution and hourly coverage.

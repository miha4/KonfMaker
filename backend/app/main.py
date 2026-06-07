from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .calculator import DEFAULT_SHIFTS, calculate
from .models import CalculatorRequest

app = FastAPI(title="KonfMaker API", version="0.1.0")

# Development CORS is intentionally permissive because this prototype has no
# cookie/session based authentication. In GitHub Codespaces the recommended
# path is the Vite /api proxy (same browser origin), but these headers also keep
# manual VITE_API_BASE_URL setups working when the frontend and backend are
# opened on different forwarded ports.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https://.*\.app\.github\.dev|http://localhost:\d+|http://127\.0\.0\.1:\d+",
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=False,
    max_age=86400,
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/default-settings")
def default_settings() -> dict[str, object]:
    return {
        "max_sectors_per_hour": 5,
        "max_consecutive_work_hours": 2,
        "rest_after_max_consecutive_hours": 1,
        "include_required_shift_leaders": True,
        "required_night_fl_count": 4,
        "shifts": [shift.model_dump() for shift in DEFAULT_SHIFTS],
    }


@app.post("/api/calculate-sector-hours")
def calculate_sector_hours(request: CalculatorRequest):
    return calculate(request)

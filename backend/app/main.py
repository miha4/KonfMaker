from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .calculator import DEFAULT_SHIFTS, calculate
from .models import CalculatorRequest

app = FastAPI(title="KonfMaker API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_origin_regex=r"https://.*\.app\.github\.dev",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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

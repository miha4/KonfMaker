from fastapi import FastAPI, Request, Response

from .calculator import DEFAULT_SHIFTS, calculate
from .models import CalculatorRequest

app = FastAPI(title="KonfMaker API", version="0.1.0")


def development_cors_headers(request: Request) -> dict[str, str]:
    origin = request.headers.get("origin") or "*"
    requested_headers = request.headers.get("access-control-request-headers") or "*"
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": requested_headers,
        "Access-Control-Max-Age": "86400",
        "Vary": "Origin",
    }


@app.middleware("http")
async def add_development_cors_headers(request: Request, call_next):
    # Codespaces exposes frontend and backend on different forwarded hosts.
    # This MVP API does not use cookies or credentialed requests, so the
    # development server can safely allow browser API calls from those hosts.
    if request.method == "OPTIONS":
        return Response(status_code=204, headers=development_cors_headers(request))

    response = await call_next(request)
    for header, value in development_cors_headers(request).items():
        response.headers.setdefault(header, value)
    return response


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

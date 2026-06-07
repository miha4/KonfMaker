from fastapi.testclient import TestClient

from app.calculator import DEFAULT_SHIFTS, calculate
from app.main import app
from app.models import CalculatorRequest, CalculatorSettings


def make_request(total=28, fl=12, acs=16, fmp=True):
    return CalculatorRequest(
        total_people=total,
        fl_count=fl,
        acs_count=acs,
        include_fmp=fmp,
        settings=CalculatorSettings(
            max_sectors_per_hour=5,
            max_consecutive_work_hours=2,
            rest_after_max_consecutive_hours=1,
            include_required_shift_leaders=True,
            required_night_fl_count=4,
            shifts=DEFAULT_SHIFTS,
        ),
    )


def test_calculator_requires_minimum_fl():
    result = calculate(make_request(total=28, fl=5, acs=23, fmp=True))
    assert result.feasible is False
    assert result.minimum_required_fl == 7


def test_calculator_returns_generated_people_and_hours():
    result = calculate(make_request())
    assert result.feasible is True
    assert result.max_sector_hours > 0
    assert len(result.people) == 28
    assert sum(item.total for item in result.shift_summary) == 28
    assert len(result.hourly_coverage) == 24


def test_api_calculate_sector_hours():
    client = TestClient(app)
    response = client.post("/api/calculate-sector-hours", json=make_request().model_dump())
    assert response.status_code == 200
    data = response.json()
    assert data["feasible"] is True
    assert data["minimum_required_fl"] == 7

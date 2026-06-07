from fastapi.testclient import TestClient

import app.calculator as calculator_module
from app.calculator import DEFAULT_SHIFTS, calculate
from app.main import app
from app.models import CalculatorRequest, CalculatorSettings


def make_request(total=28, fl=12, aps=0, acs=16, fmp=True, requested_sector_counts=None):
    return CalculatorRequest(
        total_people=total,
        fl_count=fl,
        aps_count=aps,
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
        requested_sector_counts=requested_sector_counts,
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


def test_codespaces_origin_is_allowed_for_cors_preflight():
    client = TestClient(app)
    response = client.options(
        "/api/default-settings",
        headers={
            "Origin": "https://example-codespace-5173.app.github.dev",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://example-codespace-5173.app.github.dev"
    assert "GET" in response.headers["access-control-allow-methods"]


def test_codespaces_origin_is_allowed_on_api_response():
    client = TestClient(app)
    response = client.get(
        "/api/default-settings",
        headers={"Origin": "https://example-codespace-5173.app.github.dev"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://example-codespace-5173.app.github.dev"


def test_default_night_shift_is_limited_to_v3_plus_three_a21():
    result = calculate(make_request())
    assert result.feasible is True
    assert sum(1 for person in result.people if person.shift == "A21") == 4
    assert result.minimum_required_fl == 7
    assert result.max_sector_hours < 120


def test_workers_are_only_scheduled_inside_their_shift_hours():
    result = calculate(make_request())
    people_by_id = {person.id: person for person in result.people}
    shift_by_code = {shift.code: shift for shift in DEFAULT_SHIFTS}

    for slot, coverage in enumerate(result.hourly_coverage):
        for worker_id in coverage.workers:
            person = people_by_id[worker_id]
            shift = shift_by_code[person.shift]
            valid_slots = {
                ((shift.start_hour + offset - 7) % 24) for offset in range(shift.duration_hours)
            }
            assert slot in valid_slots


def test_hourly_coverage_includes_sector_slots():
    result = calculate(make_request())
    assert all(len(hour.sector_workers) == 5 for hour in result.hourly_coverage)

    for hour in result.hourly_coverage:
        assigned_pairs = hour.sector_workers[: hour.open_sectors]
        flattened_workers = [
            worker
            for sector in assigned_pairs
            if sector is not None
            for worker in (sector.lower_worker, sector.upper_worker)
        ]
        assert flattened_workers == hour.workers
        assert all(sector is None for sector in hour.sector_workers[hour.open_sectors :])


def test_each_open_sector_has_lower_and_upper_qualified_controllers():
    result = calculate(make_request(total=28, fl=12, aps=6, acs=10))
    people_by_id = {person.id: person for person in result.people}

    for hour in result.hourly_coverage:
        for sector in hour.sector_workers[: hour.open_sectors]:
            assert sector is not None
            assert people_by_id[sector.lower_worker].license in {"APS", "FL"}
            assert people_by_id[sector.upper_worker].license in {"ACS", "FL"}
            assert sector.lower_worker != sector.upper_worker


def test_requested_sector_counts_limit_open_sectors_by_hour():
    requested = [0] * 24
    requested[0] = 2
    requested[1] = 4

    result = calculate(make_request(requested_sector_counts=requested))

    assert result.hourly_coverage[0].open_sectors <= 2
    assert result.hourly_coverage[1].open_sectors <= 4
    assert all(hour.open_sectors == 0 for hour in result.hourly_coverage[2:])
    assert result.max_sector_hours <= sum(requested)


def test_generator_runs_final_scheduler_once(monkeypatch):
    calls = 0
    original_build_schedule = calculator_module.build_schedule

    def counted_build_schedule(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_build_schedule(*args, **kwargs)

    monkeypatch.setattr(calculator_module, "build_schedule", counted_build_schedule)

    result = calculate(make_request())

    assert result.feasible is True
    assert calls == 1

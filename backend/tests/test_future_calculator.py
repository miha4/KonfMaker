import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.future_calculator import (
    FutureCalculatorRequest,
    calculate_future_sector_hours,
    rest_slots_for_work_slots,
    work_rest_transitions,
)
from app.models import ShiftRule


client = TestClient(app)


def request_for(demand: list[int], **overrides: object) -> FutureCalculatorRequest:
    values: dict[str, object] = {
        "calculation_mode": "staff_to_coverage",
        "total_people": 4,
        "fl_count": 4,
        "aps_count": 0,
        "acs_count": 0,
        "requested_sector_counts": demand,
        "shifts": [ShiftRule(code="A7", start_hour=7, duration_hours=8)],
        "min_continuous_work_minutes": 60,
        "max_continuous_work_minutes": 120,
        "rest_ratio_percent": 50,
        "allow_quarter_hour_shift_starts": False,
        "time_limit_seconds": 10,
    }
    values.update(overrides)
    return FutureCalculatorRequest(**values)


def test_proportional_rest_examples() -> None:
    assert rest_slots_for_work_slots(8, 50) == 4
    assert rest_slots_for_work_slots(6, 50) == 3
    assert rest_slots_for_work_slots(4, 50) == 2
    transitions, max_state = work_rest_transitions(8, 50)
    assert (8, 0, 11) in transitions
    assert (4, 0, 9) in transitions
    assert max_state == 12
    minimum_transitions, _ = work_rest_transitions(8, 50, min_work_slots=4)
    assert (3, 0, 10) not in minimum_transitions
    assert (4, 0, 9) in minimum_transitions


def test_future_request_requires_96_quarter_hours() -> None:
    with pytest.raises(ValidationError, match="96 četrturnih"):
        request_for([1] * 24)


def test_future_calculator_covers_quarter_hour_profile_and_respects_rest() -> None:
    demand = [0] * 96
    demand[0:8] = [1] * 8
    demand[12:16] = [1] * 4

    result = calculate_future_sector_hours(request_for(demand))

    assert result.feasible is True
    assert result.covered_sector_hours == 3
    assert result.requested_sector_hours == 3
    assert result.active_people <= 4
    assert all(block.duration_minutes <= 120 for person in result.people for block in person.blocks)
    assert all(block.duration_minutes >= 60 for person in result.people for block in person.blocks)
    assert all(
        block.required_rest_minutes == block.duration_minutes / 2
        for person in result.people
        for block in person.blocks
    )
    for slot in result.coverage:
        sector_workers = [
            worker
            for sector in slot.sectors
            for worker in (sector.lower_worker, sector.upper_worker)
        ]
        assert len(slot.sectors) == slot.open_sectors
        assert len(sector_workers) == len(set(sector_workers))
        assert set(sector_workers) == set(slot.workers)
        assert set(slot.resting_workers).isdisjoint(slot.workers)


def test_future_calculator_publishes_live_incumbent_preview() -> None:
    demand = [0] * 96
    demand[0:8] = [1] * 8
    previews = []

    result = calculate_future_sector_hours(request_for(demand), incumbent_callback=previews.append)

    assert previews
    assert previews[0].solver_status == "FEASIBLE"
    assert previews[0].coverage[0].sectors
    assert previews[-1].covered_quarter_slots <= result.covered_quarter_slots
    assert any("sprotni predogled" in note.lower() for note in previews[0].notes)


def test_future_calculator_reports_shortfall_with_too_few_people() -> None:
    demand = [1] * 32 + [0] * 64
    result = calculate_future_sector_hours(
        request_for(
            demand,
            total_people=2,
            fl_count=2,
            shifts=[ShiftRule(code="A7", start_hour=7, duration_hours=8)],
        )
    )

    assert result.feasible is False
    assert result.covered_quarter_slots < result.requested_quarter_slots
    assert result.missing_sector_hours > 0


def test_future_sector_preview_preserves_sector_license_rules() -> None:
    demand = [0] * 96
    demand[0:4] = [2] * 4
    result = calculate_future_sector_hours(
        request_for(
            demand,
            total_people=4,
            fl_count=0,
            aps_count=2,
            acs_count=2,
        )
    )

    assert result.feasible is True
    first_slot = result.coverage[0]
    assert [sector.sector_name for sector in first_slot.sectors] == ["LOWER", "UPPER"]
    assert {first_slot.sectors[0].lower_worker, first_slot.sectors[0].upper_worker} == {"APS1", "APS2"}
    assert {first_slot.sectors[1].lower_worker, first_slot.sectors[1].upper_worker} == {"ACS1", "ACS2"}


def test_open_sector_mode_reports_minimum_active_people_from_available_pool() -> None:
    demand = [0] * 96
    demand[0:4] = [1] * 4
    result = calculate_future_sector_hours(
        request_for(demand, calculation_mode="demand_to_staff", total_people=6, fl_count=6)
    )

    assert result.feasible is True
    assert result.planned_people == 2
    assert result.active_people == 2
    assert result.available_people == 6


def test_future_calculator_job_endpoint_returns_result() -> None:
    demand = [0] * 96
    demand[0:4] = [1] * 4
    request = request_for(demand, calculation_mode="demand_to_staff", total_people=6, fl_count=6)

    started_response = client.post("/api/jobs/future-calculator", json=request.model_dump(mode="json"))
    assert started_response.status_code == 200
    job_id = started_response.json()["job_id"]

    for _ in range(100):
        status = client.get(f"/api/jobs/{job_id}").json()
        if status["status"] in {"finished", "failed"}:
            break
        time.sleep(0.05)
    assert status["status"] == "finished"

    result_response = client.get(f"/api/jobs/{job_id}/result")
    assert result_response.status_code == 200
    assert result_response.json()["covered_sector_hours"] == 1

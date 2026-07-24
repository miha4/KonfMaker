import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.airport_calculator import (
    AIRPORT_SHIFT_CATALOG,
    AirportCalculatorRequest,
    AirportShift,
    calculate_airport_schedule,
    interval_slots,
    shift_presence_slots,
)
from app.main import app


client = TestClient(app)


def airport_request(**overrides: object) -> AirportCalculatorRequest:
    values: dict[str, object] = {
        "airport": "POW",
        "total_people": 2,
        "opening_start": "10:00",
        "opening_end": "10:15",
        "continuous_24_hours": False,
        "require_assistant_presence": True,
        "avoid_split_shifts": True,
        "explore_opening_extension": False,
        "time_limit_seconds": 10,
    }
    values.update(overrides)
    return AirportCalculatorRequest(**values)


def test_shift_catalog_uses_operational_rows_without_vi_duplicates() -> None:
    assert [shift.code for shift in AIRPORT_SHIFT_CATALOG["BRN"]] == [
        "B7",
        "B8",
        "B9",
        "BD",
        "B14",
        "B17",
        "B21",
    ]
    assert "MDA" in {shift.code for shift in AIRPORT_SHIFT_CATALOG["MBX"]}
    assert "PDI" in {shift.code for shift in AIRPORT_SHIFT_CATALOG["POW"]}
    assert "CKX" in {shift.code for shift in AIRPORT_SHIFT_CATALOG["CEK"]}


def test_split_shift_catalog_matches_break_columns() -> None:
    expected_breaks = {
        "BRN": {"BD": ("13:00", "15:45")},
        "MBX": {
            "MDA": ("11:00", "16:45"),
            "MDB": ("12:00", "15:45"),
            "MDC": ("12:00", "16:00"),
            "MDT": ("13:00", "15:00"),
            "MDD": ("12:30", "16:00"),
            "MDU": ("15:00", "17:00"),
        },
        "POW": {
            "PDC": ("12:00", "16:00"),
            "PDD": ("11:45", "13:45"),
            "PDI": ("12:30", "16:00"),
            "PDH": ("12:30", "16:00"),
        },
        "CEK": {
            "CDC": ("12:15", "16:45"),
            "CDF": ("13:00", "15:00"),
            "CDD": ("13:15", "17:45"),
        },
    }

    actual_breaks = {
        airport: {
            shift.code: (shift.break_start, shift.break_end)
            for shift in shifts
            if shift.break_start is not None
        }
        for airport, shifts in AIRPORT_SHIFT_CATALOG.items()
    }

    assert actual_breaks == expected_breaks


def test_split_shift_break_is_not_presence_or_in_house_rest() -> None:
    shift = AirportShift("PX", "09:00", "19:00", "13:00", "15:45")
    slots = shift_presence_slots(shift)

    assert interval_slots("09:00", "13:00") <= slots
    assert interval_slots("15:45", "19:00") <= slots
    assert slots.isdisjoint(interval_slots("13:00", "15:45"))


def test_solver_excludes_split_shift_break_from_presence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        AIRPORT_SHIFT_CATALOG,
        "POW",
        (AirportShift("PX", "09:00", "19:00", "13:00", "15:45"),),
    )

    result = calculate_airport_schedule(
        airport_request(
            total_people=1,
            opening_start="12:45",
            opening_end="16:15",
            require_assistant_presence=False,
        )
    )

    assert result.covered_minutes == 45
    assert result.active_people == 1
    person = result.people[0]
    assert [(block.start, block.end) for block in person.shift_segments] == [
        ("09:00", "13:00"),
        ("15:45", "19:00"),
    ]
    assert interval_slots("13:00", "15:45").isdisjoint(person.presence_slots)
    gap_slot = next(slot for slot in result.coverage if slot.start == "14:00")
    assert gap_slot.present_workers == []
    assert gap_slot.break_workers == []


def test_cross_midnight_interval_contains_both_sides_of_midnight() -> None:
    slots = interval_slots("20:45", "07:00")

    assert 83 in slots
    assert 95 in slots
    assert 0 in slots
    assert 27 in slots
    assert 28 not in slots


def test_request_requires_quarter_hour_times() -> None:
    with pytest.raises(ValidationError, match="15-minutna"):
        airport_request(opening_start="06:10")


def test_preparation_slot_can_support_but_cannot_control() -> None:
    result = calculate_airport_schedule(airport_request())

    assert result.feasible is True
    assert result.covered_minutes == 15
    assert result.active_people == 2
    assert all(
        block.duration_minutes <= 180
        for person in result.people
        for block in person.duty_blocks
    )
    for person in result.people:
        assert len(person.preparation_slots) == 1
        assert person.preparation_slots[0] not in person.controller_slots
    opening_slot = next(slot for slot in result.coverage if slot.start == "10:00")
    assert opening_slot.controller_id is not None
    assert opening_slot.assistant_id is not None
    assert len(opening_slot.duty_workers) == 2
    controller = next(person for person in result.people if person.id == opening_slot.controller_id)
    assistant = next(person for person in result.people if person.id == opening_slot.assistant_id)
    assert controller.shift_start != "10:00"
    assert assistant.shift_start == "10:00"
    assert opening_slot.slot in assistant.preparation_slots
    assert opening_slot.slot in assistant.assistant_slots
    assert opening_slot.slot not in assistant.controller_slots
    assert {
        opening_slot.controller_id,
        opening_slot.assistant_id,
    } == set(opening_slot.duty_workers)


def test_pow_weekday_opening_requires_three_operational_controllers() -> None:
    two_people = calculate_airport_schedule(
        airport_request(
            total_people=2,
            opening_start="10:00",
            opening_end="18:30",
        )
    )
    three_people = calculate_airport_schedule(
        airport_request(
            total_people=3,
            opening_start="10:00",
            opening_end="18:30",
        )
    )

    assert two_people.feasible is False
    assert two_people.missing_minutes > 0
    assert three_people.feasible is True
    assert three_people.active_people == 3
    assert all(
        len(slot.duty_workers) == 2
        and slot.controller_id is not None
        and slot.assistant_id is not None
        for slot in three_people.coverage
        if slot.is_open
    )
    assert all(
        block.duration_minutes <= 180
        for person in three_people.people
        for block in person.duty_blocks
    )
    controller_minutes = [
        person.controller_minutes
        for person in three_people.people
    ]
    assert max(controller_minutes) - min(controller_minutes) <= 15
    assert all(
        block.duration_minutes >= 30
        for person in three_people.people
        for block in person.controller_blocks
    )


def test_same_shift_can_be_assigned_to_all_required_people(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        AIRPORT_SHIFT_CATALOG,
        "POW",
        (AirportShift("PX", "09:45", "18:30"),),
    )

    result = calculate_airport_schedule(
        airport_request(
            total_people=3,
            opening_start="10:00",
            opening_end="18:30",
        )
    )

    assert result.feasible is True
    assert result.active_people == 3
    assert {person.shift for person in result.people} == {"PX"}


def test_selected_duplicate_shifts_produce_maximum_contiguous_opening(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        AIRPORT_SHIFT_CATALOG,
        "POW",
        (AirportShift("PX", "09:45", "18:30"),),
    )

    result = calculate_airport_schedule(
        airport_request(
            total_people=1,
            calculation_mode="selected_shifts",
            fixed_shift_counts={"PX": 3},
            explore_opening_extension=True,
        )
    )

    assert result.calculation_mode == "selected_shifts"
    assert result.feasible is True
    assert result.available_people == 3
    assert result.active_people == 3
    assert result.opening_start == "10:00"
    assert result.opening_end == "18:30"
    assert result.covered_minutes == 8 * 60 + 30
    assert result.requested_minutes == result.covered_minutes
    assert result.missing_minutes == 0
    assert result.opening_extension is None
    assert {person.shift for person in result.people} == {"PX"}
    assert all(slot.is_covered for slot in result.coverage if slot.is_open)
    for person in result.people:
        break_slots: set[int] = set()
        for block in person.break_blocks:
            break_slots.update(interval_slots(block.start, block.end))
        assert set(person.preparation_slots).isdisjoint(break_slots)


def test_selected_shifts_reject_unknown_shift() -> None:
    with pytest.raises(ValidationError, match="Neznana izmena"):
        airport_request(
            calculation_mode="selected_shifts",
            fixed_shift_counts={"NOPE": 2},
        )


def test_solver_avoids_shift_time_after_airport_closes_when_not_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        AIRPORT_SHIFT_CATALOG,
        "POW",
        (
            AirportShift("ALIGNED", "09:45", "18:30"),
            AirportShift("LATE", "12:00", "20:15"),
        ),
    )

    result = calculate_airport_schedule(
        airport_request(
            total_people=3,
            opening_start="10:00",
            opening_end="18:30",
        )
    )

    assert result.feasible is True
    assert result.active_people == 3
    assert {person.shift for person in result.people} == {"ALIGNED"}
    assert all(person.shift_end == "18:30" for person in result.people)


def test_split_shift_preference_can_be_enabled_or_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        AIRPORT_SHIFT_CATALOG,
        "POW",
        (
            AirportShift("SPLIT", "09:00", "19:00", "12:00", "13:00"),
            AirportShift("PLAIN", "09:00", "19:00"),
        ),
    )

    preferred = calculate_airport_schedule(
        airport_request(avoid_split_shifts=True)
    )
    unrestricted = calculate_airport_schedule(
        airport_request(avoid_split_shifts=False)
    )

    assert {person.shift for person in preferred.people} == {"PLAIN"}
    assert {person.shift for person in unrestricted.people} == {"SPLIT"}


def test_opening_extension_is_contiguous_and_keeps_requested_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        AIRPORT_SHIFT_CATALOG,
        "POW",
        (AirportShift("PX", "09:15", "12:30"),),
    )

    result = calculate_airport_schedule(
        airport_request(
            total_people=2,
            opening_start="10:00",
            opening_end="12:00",
            explore_opening_extension=True,
            time_limit_seconds=3,
        )
    )

    assert result.feasible is True
    assert result.covered_minutes == result.requested_minutes
    assert result.opening_extension is not None
    assert result.opening_extension.suggested_start == "09:30"
    assert result.opening_extension.suggested_end == "12:30"
    assert result.opening_extension.before_minutes == 30
    assert result.opening_extension.after_minutes == 30
    assert result.extended_variant is not None
    assert result.extended_variant.opening_start == "09:30"
    assert result.extended_variant.opening_end == "12:30"
    assert result.extended_variant.opening_minutes == 180
    assert all(
        slot.is_covered
        for slot in result.extended_variant.coverage
        if slot.is_open
    )
    assert all(
        slot.is_covered
        for slot in result.coverage
        if slot.is_open
    )


def test_opening_extension_can_be_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        AIRPORT_SHIFT_CATALOG,
        "POW",
        (AirportShift("PX", "07:45", "20:30"),),
    )

    result = calculate_airport_schedule(
        airport_request(
            total_people=3,
            opening_start="08:30",
            opening_end="20:00",
            explore_opening_extension=False,
        )
    )

    assert result.opening_extension is None
    open_slots = [slot for slot in result.coverage if slot.is_open]
    assert len(open_slots) == 46
    assert any(slot.start == "08:30" for slot in open_slots)
    assert any(slot.start == "19:45" for slot in open_slots)
    assert not any(slot.start in {"08:15", "20:00"} for slot in open_slots)


def test_one_person_reports_partial_coverage_when_assistant_is_required() -> None:
    result = calculate_airport_schedule(airport_request(total_people=1))

    assert result.feasible is False
    assert result.covered_minutes == 0
    assert result.missing_minutes == result.requested_minutes
    assert result.active_people == 0
    assert any("najmanj dve" in warning for warning in result.warnings)


def test_three_hour_limit_applies_without_assistant_rule() -> None:
    result = calculate_airport_schedule(
        airport_request(
            total_people=1,
            opening_start="10:00",
            opening_end="14:00",
            require_assistant_presence=False,
        )
    )

    assert result.feasible is False
    assert result.covered_minutes == 180
    assert max(
        block.duration_minutes
        for person in result.people
        for block in person.duty_blocks
    ) == 180


def test_airport_endpoint_returns_schedule() -> None:
    response = client.post(
        "/api/airport-calculator",
        json=airport_request().model_dump(mode="json"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["feasible"] is True
    assert payload["airport"] == "POW"
    assert payload["people"]

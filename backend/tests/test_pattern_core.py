from fastapi.testclient import TestClient

from app.calculator import DEFAULT_SHIFTS
from app.main import app
from app.models import CalculatorRequest, CalculatorSettings
from app.pattern_core import (
    build_pattern_library,
    calculate_pattern_minimum,
    can_use_pattern_minimum_core,
    generate_shift_patterns,
    hour_index,
    load_or_build_pattern_library,
)


def make_pattern_request(
    requested_sector_counts: list[int],
    *,
    fl_ratio: int = 50,
    aps_ratio: int = 0,
    acs_ratio: int = 50,
) -> CalculatorRequest:
    return CalculatorRequest(
        calculation_mode="demand_to_staff",
        total_people=0,
        fl_count=fl_ratio,
        aps_count=aps_ratio,
        acs_count=acs_ratio,
        include_fmp=False,
        settings=CalculatorSettings(
            max_sectors_per_hour=5,
            max_consecutive_work_hours=2,
            rest_after_max_consecutive_hours=1,
            cp_sat_time_limit_seconds=3,
            cp_sat_no_improvement_seconds=0,
            cp_sat_acceptable_sector_gap=0,
            cp_sat_min_auto_stop_coverage_percent=95,
            include_required_shift_leaders=False,
            required_night_fl_count=0,
            v1_sector_limit=1,
            v2_sector_limit=1,
            v3_sector_limit=2,
            fmp_sector_limit=6,
            shifts=DEFAULT_SHIFTS,
            officer_shifts=[],
        ),
        requested_sector_counts=requested_sector_counts,
        fixed_staff=[],
        locked_staff=[],
        officer_staff=[],
        office_pool=[],
        include_pareto=False,
        prefer_minimal_fl=False,
    )


def test_pattern_generator_allows_late_start_patterns():
    shift = next(item for item in DEFAULT_SHIFTS if item.code == "A9")
    patterns = generate_shift_patterns(shift, max_consecutive=2, rest_after_max=1)

    assert (0, 1, 1, 0, 1, 1, 0, 1) in patterns
    assert all("111" not in "".join(str(bit) for bit in pattern) for pattern in patterns)


def test_pattern_library_blocks_shift_leader_edge_slots():
    request = make_pattern_request([0] * 24)
    request = request.model_copy(
        update={
            "settings": request.settings.model_copy(
                update={
                    "include_required_shift_leaders": True,
                    "v1_sector_limit": 7,
                    "v2_sector_limit": 7,
                    "v3_sector_limit": 7,
                }
            )
        }
    )
    library = build_pattern_library(request)
    forbidden_by_role = {
        "V1": {hour_index(7), hour_index(13)},
        "V2": {hour_index(14), hour_index(20)},
        "V3": {hour_index(21)},
    }

    for role, forbidden_slots in forbidden_by_role.items():
        role_patterns = [pattern for pattern in library.patterns if pattern.role == role]
        assert role_patterns
        assert all(not forbidden_slots.intersection(pattern.slots) for pattern in role_patterns)


def test_pattern_core_proves_two_people_for_single_all_sector_hour():
    requested = [0] * 24
    requested[2] = 1
    request = make_pattern_request(requested, fl_ratio=80, aps_ratio=0, acs_ratio=0)

    assert can_use_pattern_minimum_core(request)
    result = calculate_pattern_minimum(request)

    assert result.feasible is True
    assert result.planned_people == 2
    assert result.max_sector_hours == 1
    assert result.hourly_coverage[2].open_sectors == 1
    assert len(result.hourly_coverage[2].workers) == 2
    assert result.scheduled_person_hours == 2
    assert result.total_person_capacity_hours == 2
    assert any("Minimum je dokazan" in note for note in result.notes)
    assert any("exact-cover" in note for note in result.notes)


def test_pattern_core_uses_acs_for_upper_sector_when_ratio_allows_it():
    requested = [0] * 24
    requested[3] = 2
    request = make_pattern_request(requested, fl_ratio=50, aps_ratio=0, acs_ratio=50)

    result = calculate_pattern_minimum(request)
    selected_licenses = {person.license for person in result.people}

    assert result.feasible is True
    assert result.planned_people == 4
    assert {"FL", "ACS"}.issubset(selected_licenses)


def test_pattern_core_supports_fmp_and_shift_leaders_without_same_hour_overlap():
    requested = [0] * 24
    requested[3] = 1
    requested[4] = 1
    request = make_pattern_request(requested, fl_ratio=80, aps_ratio=0, acs_ratio=0)
    request = request.model_copy(
        update={
            "include_fmp": True,
            "fmp_shift_mode": "auto",
            "settings": request.settings.model_copy(
                update={
                    "include_required_shift_leaders": True,
                    "v1_sector_limit": 1,
                    "v2_sector_limit": 1,
                    "v3_sector_limit": 4,
                    "fmp_sector_limit": 6,
                }
            ),
        }
    )

    assert can_use_pattern_minimum_core(request)
    result = calculate_pattern_minimum(request)
    role_by_id = {person.id: person.role for person in result.people}

    assert result.feasible is True
    assert any(person.role == "FMP" for person in result.people)
    assert any(person.role in {"V1", "V2", "V3"} for person in result.people)
    for coverage in result.hourly_coverage:
        roles = {role_by_id.get(worker_id) for worker_id in coverage.workers}
        assert not ("FMP" in roles and roles.intersection({"V1", "V2", "V3"}))


def test_pattern_library_is_cached_by_rule_signature(monkeypatch, tmp_path):
    cache_path = tmp_path / "patterns.json"
    monkeypatch.setenv("KONFMAKER_PATTERN_CACHE_PATH", str(cache_path))
    requested = [0] * 24
    requested[3] = 2
    request = make_pattern_request(requested)

    first = load_or_build_pattern_library(request)
    second = load_or_build_pattern_library(request)

    assert cache_path.exists()
    assert first.cache_status == "generated"
    assert second.cache_status == "hit"
    assert second.rule_signature == first.rule_signature
    assert second.pattern_count == first.pattern_count


def test_pattern_library_profile_and_regenerate_endpoints(monkeypatch, tmp_path):
    cache_path = tmp_path / "patterns.json"
    monkeypatch.setenv("KONFMAKER_PATTERN_CACHE_PATH", str(cache_path))
    requested = [0] * 24
    requested[3] = 2
    payload = make_pattern_request(requested).model_dump()
    client = TestClient(app)

    profile_response = client.post("/api/pattern-library/profile", json=payload)
    assert profile_response.status_code == 200
    profile = profile_response.json()
    assert cache_path.exists()
    assert profile["pattern_count"] > 0
    assert profile["cache_status"] == "generated"
    assert profile["cache_path"] == str(cache_path)
    assert profile["patterns_by_shift"]["A9"] > 0
    assert profile["patterns_by_license"]["FL"] > 0
    assert profile["patterns_by_role"]["regular"] > 0

    regenerate_response = client.post("/api/pattern-library/regenerate", json=payload)
    assert regenerate_response.status_code == 200
    regenerated = regenerate_response.json()
    assert regenerated["cache_status"] == "regenerated"
    assert regenerated["rule_signature"] == profile["rule_signature"]
    assert regenerated["pattern_count"] == profile["pattern_count"]

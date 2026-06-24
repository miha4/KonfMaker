import time
import zipfile
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.calculator import (
    DEFAULT_OFFICER_SHIFTS,
    DEFAULT_SHIFTS,
    SolverSnapshot,
    add_fixed_staff_people,
    add_locked_staff_people,
    calculate,
    calculate_pareto,
    candidate_pool_from_configuration,
    configuration_seed_candidate_pools,
    coverage_shortfall_warning,
    hour_index,
    PersonState,
    role_allows_sector_slot,
    shift_map_for_request,
)
from app.config_library import complete_configuration, manual_configuration_one_down, settings_for_manual_schedule_evaluation
from app.main import app
from app.models import CalculatorRequest, CalculatorResponse, CalculatorSettings, CompleteConfigurationRequest


def write_minimal_config_workbook(path, sheet_name="25n5"):
    def inline_cell(reference: str, value: str) -> str:
        return f'<c r="{reference}" t="inlineStr"><is><t>{value}</t></is></c>'

    def number_cell(reference: str, value: int) -> str:
        return f'<c r="{reference}"><v>{value}</v></c>'

    rows = [
        '<row r="1">'
        f'{inline_cell("AR1", sheet_name)}'
        f'{inline_cell("AU1", "LOC")}'
        f'{inline_cell("AW1", "ALL")}'
        f'{inline_cell("AY1", "LOWER")}'
        f'{inline_cell("BA1", "UPPER")}'
        '</row>',
        '<row r="2">'
        f'{inline_cell("AR2", "V1")}'
        f'{inline_cell("AS2", "Vi1")}'
        f'{number_cell("AT2", 1)}'
        f'{inline_cell("AU2", "7.00 - 8.00")}'
        f'{number_cell("AV2", 2)}'
        f'{inline_cell("AY2", "B")}'
        f'{inline_cell("AZ2", "C")}'
        f'{inline_cell("BA2", "D")}'
        f'{inline_cell("BB2", "V1")}'
        '</row>',
        '<row r="3">'
        f'{inline_cell("AR3", "B")}'
        f'{inline_cell("AS3", "A7")}'
        f'{number_cell("AT3", 1)}'
        f'{inline_cell("AU3", "8.00 - 9.00")}'
        f'{number_cell("AV3", 0)}'
        '</row>',
    ]
    for row_index in range(4, 26):
        rows.append(
            f'<row r="{row_index}">'
            f'{inline_cell(f"AU{row_index}", f"{row_index}.00 - {row_index + 1}.00")}'
            f'{number_cell(f"AV{row_index}", 0)}'
            '</row>'
        )

    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(rows)}</sheetData>'
        '</worksheet>'
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets>'
        f'<sheet name="{sheet_name}" sheetId="1" r:id="rId1"/>'
        '</sheets>'
        '</workbook>'
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        '</Relationships>'
    )
    with zipfile.ZipFile(path, "w") as workbook:
        workbook.writestr("xl/workbook.xml", workbook_xml)
        workbook.writestr("xl/_rels/workbook.xml.rels", rels_xml)
        workbook.writestr("xl/worksheets/sheet1.xml", sheet_xml)


def make_request(
    total=28,
    fl=12,
    aps=0,
    acs=16,
    fmp=True,
    requested_sector_counts=None,
    calculation_mode="staff_to_coverage",
    fixed_staff=None,
    include_required_shift_leaders=True,
    include_night_fl_requirement=True,
    required_night_fl_count=4,
    cp_sat_time_limit_seconds=3,
    cp_sat_no_improvement_seconds=180,
    cp_sat_acceptable_sector_gap=0,
    cp_sat_min_auto_stop_coverage_percent=95,
    v1_sector_limit=5,
    v2_sector_limit=5,
    v3_sector_limit=7,
    fmp_sector_limit=6,
    locked_staff=None,
    officer_staff=None,
    office_pool=None,
    license_mix_percent=None,
    include_pareto=False,
    prefer_minimal_fl=False,
):
    return CalculatorRequest(
        calculation_mode=calculation_mode,
        total_people=total,
        fl_count=fl,
        aps_count=aps,
        acs_count=acs,
        include_fmp=fmp,
        settings=CalculatorSettings(
            max_sectors_per_hour=5,
            max_consecutive_work_hours=2,
            rest_after_max_consecutive_hours=1,
            cp_sat_time_limit_seconds=cp_sat_time_limit_seconds,
            cp_sat_no_improvement_seconds=cp_sat_no_improvement_seconds,
            cp_sat_acceptable_sector_gap=cp_sat_acceptable_sector_gap,
            cp_sat_min_auto_stop_coverage_percent=cp_sat_min_auto_stop_coverage_percent,
            include_required_shift_leaders=include_required_shift_leaders,
            include_night_fl_requirement=include_night_fl_requirement,
            required_night_fl_count=required_night_fl_count,
            v1_sector_limit=v1_sector_limit,
            v2_sector_limit=v2_sector_limit,
            v3_sector_limit=v3_sector_limit,
            fmp_sector_limit=fmp_sector_limit,
            shifts=DEFAULT_SHIFTS,
            officer_shifts=DEFAULT_OFFICER_SHIFTS,
        ),
        requested_sector_counts=requested_sector_counts,
        fixed_staff=fixed_staff or [],
        locked_staff=locked_staff or [],
        officer_staff=officer_staff or [],
        office_pool=office_pool or [],
        license_mix_percent=license_mix_percent,
        include_pareto=include_pareto,
        prefer_minimal_fl=prefer_minimal_fl,
    )


def test_calculator_requires_minimum_fl():
    result = calculate(make_request(total=28, fl=5, acs=23, fmp=True))
    assert result.feasible is False
    assert result.minimum_required_fl == 7


def test_calculator_returns_generated_people_and_hours():
    result = calculate(make_request())
    assert result.max_sector_hours > 0
    assert 0 < len(result.people) <= 28
    assert sum(item.total for item in result.shift_summary) == len(result.people)
    assert len(result.hourly_coverage) == 24
    assert result.planned_people == len(result.people)
    assert result.active_people + result.unused_people == result.planned_people
    assert result.scheduled_person_hours == sum(person.sector_hours for person in result.people)
    assert result.total_person_capacity_hours == sum(person.max_sector_hours for person in result.people)
    assert result.utilization_percent > 0


def test_api_calculate_sector_hours():
    client = TestClient(app)
    response = client.post("/api/calculate-sector-hours", json=make_request().model_dump())
    assert response.status_code == 200
    data = response.json()
    assert data["minimum_required_fl"] == 7
    assert data["planned_people"] == 28


def test_calculation_job_flow_returns_status_and_result():
    client = TestClient(app)
    response = client.post("/api/jobs/calculate-sector-hours", json=make_request().model_dump())
    assert response.status_code == 200
    started = response.json()
    assert started["status"] == "queued"
    assert started["job_id"]

    final_status = None
    for _ in range(120):
        status_response = client.get(f"/api/jobs/{started['job_id']}")
        assert status_response.status_code == 200
        status = status_response.json()
        assert status["status"] in {"queued", "running", "finished", "failed"}
        assert 0 <= status["progress"] <= 100
        assert "elapsed_seconds" in status
        assert "best_result_available" in status
        assert "best_result_version" in status
        assert "solver_optimality_gap_percent" in status
        assert "solver_stop_reason" in status
        assert "solver_sector_gap_to_best_bound" in status
        if status["status"] in {"finished", "failed"}:
            final_status = status
            break
        time.sleep(0.1)

    assert final_status is not None
    assert final_status["status"] == "finished"

    result_response = client.get(f"/api/jobs/{started['job_id']}/result")
    assert result_response.status_code == 200
    data = result_response.json()
    assert "feasible" in data
    assert "hourly_coverage" in data
    assert data["minimum_required_fl"] == 7


def test_pareto_job_flow_returns_points():
    client = TestClient(app)
    requested = [0] * 24
    requested[1] = 1
    response = client.post(
        "/api/jobs/pareto-analysis",
        json=make_request(
            total=4,
            fl=4,
            aps=0,
            acs=0,
            fmp=False,
            requested_sector_counts=requested,
            include_required_shift_leaders=False,
            required_night_fl_count=0,
            cp_sat_time_limit_seconds=4,
        ).model_dump(),
    )
    assert response.status_code == 200
    started = response.json()

    final_status = None
    for _ in range(80):
        status_response = client.get(f"/api/jobs/{started['job_id']}")
        assert status_response.status_code == 200
        status = status_response.json()
        if status["status"] in {"finished", "failed"}:
            final_status = status
            break
        time.sleep(0.1)

    assert final_status is not None
    assert final_status["status"] == "finished"

    result_response = client.get(f"/api/jobs/{started['job_id']}/result")
    assert result_response.status_code == 200
    data = result_response.json()
    assert data["requested_sector_hours"] == 1
    assert data["points"]
    assert any(point["coverage_percent"] == 100 for point in data["points"])


def test_calculator_publishes_cp_sat_incumbents():
    incumbents = []
    requested = [0] * 24
    requested[1] = 1

    result = calculate(
        make_request(
            total=8,
            fl=8,
            aps=0,
            acs=0,
            requested_sector_counts=requested,
            include_required_shift_leaders=False,
            required_night_fl_count=0,
            cp_sat_time_limit_seconds=3,
        ),
        incumbent_callback=lambda response, snapshot: incumbents.append((response, snapshot)),
    )

    assert incumbents
    best_response, solver_snapshot = incumbents[-1]
    assert best_response.max_sector_hours <= result.max_sector_hours
    assert solver_snapshot is not None
    assert solver_snapshot.status == "FEASIBLE"
    assert solver_snapshot.solution_count >= 1


def test_configuration_seed_accepts_nearby_larger_manual_config(monkeypatch, tmp_path):
    library_path = tmp_path / "configs.csv"
    library_path.write_text(
        ";25n5\n"
        "MODEL_MAX_SH;64\n"
        "A7;25\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("KONFMAKER_CONFIG_LIBRARY_CSV", str(library_path))
    requested = [0] * 24
    requested[0] = 5
    requested[1] = 5
    requested[2] = 5
    requested[3] = 5
    requested[4] = 5
    requested[5] = 5
    requested[6] = 5
    requested[7] = 5
    requested[8] = 5
    requested[9] = 5
    requested[10] = 5
    requested[11] = 5
    requested[12] = 5

    seeds = configuration_seed_candidate_pools(
        [],
        set(),
        make_request(
            total=0,
            fl=50,
            aps=0,
            acs=50,
            fmp=False,
            requested_sector_counts=requested,
            calculation_mode="demand_to_staff",
            include_required_shift_leaders=False,
            required_night_fl_count=0,
        ),
        people_limit=24,
        requested_sector_hours=sum(requested),
    )

    assert seeds
    assert seeds[0][0].startswith("25n5")
    assert len(seeds[0][1]) == 25


def test_manual_configuration_endpoints_return_detail(monkeypatch, tmp_path):
    library_path = tmp_path / "configs.csv"
    library_path.write_text(
        ";25n5\n"
        "MODEL_MAX_SH;64\n"
        "MODEL_STATUS;OK\n"
        "MODEL_SECONDS;0.12\n"
        "A7;2\n"
        "APSA7;1\n"
        "ACSA7;1\n"
        "A7o;1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("KONFMAKER_CONFIG_LIBRARY_CSV", str(library_path))
    monkeypatch.setenv("KONFMAKER_USER_CONFIG_LIBRARY_JSON", str(tmp_path / "user_configs.json"))
    client = TestClient(app)

    list_response = client.get("/api/manual-configurations")
    assert list_response.status_code == 200
    library = list_response.json()
    assert library["source_path"] == str(library_path)
    assert library["configurations"][0]["name"] == "25n5"
    assert library["configurations"][0]["model_max_sector_hours"] == 63
    assert library["configurations"][0]["excel_sector_hours"] == 63
    assert library["configurations"][0]["model_reported_sector_hours"] == 64
    assert library["configurations"][0]["parsed_total"] == 3

    detail_response = client.get("/api/manual-configurations/1")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["name"] == "25n5"
    assert detail["model_max_sector_hours"] == 63
    assert detail["excel_sector_hours"] == 63
    assert detail["model_reported_sector_hours"] == 64
    assert detail["license_counts"] == {"FL": 1, "APS": 1, "ACS": 1}
    assert len(detail["fixed_staff"]) == 2
    assert len(detail["officer_staff"]) == 1
    assert any(row["shift"] == "A7" and row["hour_slots"] for row in detail["staff_rows"])


def test_one_down_preserves_enabled_manual_officer_shift(monkeypatch):
    captured_requests = []

    def fake_detail(_configuration_id):
        return {
            "id": "manual-office",
            "name": "24o4z",
            "source_type": "excel",
            "parsed_total": 4,
            "license_counts": {"FL": 4, "APS": 0, "ACS": 0},
            "officer_staff": [{"count": 1, "license": "FL", "shift": "A8o"}],
            "manual_schedule": {
                "people": [],
                "hourly_coverage": [{"hour": f"{index}", "open_sectors": 0} for index in range(24)],
            },
        }

    def fake_calculate(request, **_kwargs):
        captured_requests.append(request)
        return CalculatorResponse(
            feasible=True,
            max_sector_hours=0,
            requested_sector_hours=0,
            missing_sector_hours=0,
            minimum_required_fl=0,
            planned_people=0,
            active_people=0,
            unused_people=0,
            people=[],
            shift_summary=[],
            hourly_coverage=[],
            notes=[],
            warnings=[],
        )

    monkeypatch.setattr("app.config_library.manual_configuration_detail", fake_detail)
    monkeypatch.setattr("app.config_library.calculate", fake_calculate)

    response = manual_configuration_one_down("manual-office", time_limit_seconds=1)

    assert response["selected_variant"] == "same_inputs"
    assert captured_requests
    first_request = captured_requests[0]
    assert first_request.total_people == 2
    assert [item.model_dump() for item in first_request.officer_staff] == [
        {"count": 1, "license": "FL", "shift": "A8o"}
    ]


def test_one_down_respects_disabled_saved_officer_shift(monkeypatch):
    captured_requests = []

    def fake_detail(_configuration_id):
        return {
            "id": "manual-office",
            "name": "24o4z",
            "source_type": "excel",
            "parsed_total": 4,
            "license_counts": {"FL": 4, "APS": 0, "ACS": 0},
            "officer_staff": [{"count": 1, "license": "FL", "shift": "A8o"}],
            "manual_schedule": {
                "people": [],
                "hourly_coverage": [{"hour": f"{index}", "open_sectors": 0} for index in range(24)],
            },
        }

    def fake_calculate(request, **_kwargs):
        captured_requests.append(request)
        return CalculatorResponse(
            feasible=True,
            max_sector_hours=0,
            requested_sector_hours=0,
            missing_sector_hours=0,
            minimum_required_fl=0,
            planned_people=0,
            active_people=0,
            unused_people=0,
            people=[],
            shift_summary=[],
            hourly_coverage=[],
            notes=[],
            warnings=[],
        )

    settings = make_request(
        include_required_shift_leaders=False,
        include_night_fl_requirement=False,
    ).settings.model_copy(
        update={
            "officer_shifts": [
                shift.model_copy(update={"enabled": False}) if shift.code == "A8o" else shift
                for shift in DEFAULT_OFFICER_SHIFTS
            ]
        }
    )

    monkeypatch.setattr("app.config_library.manual_configuration_detail", fake_detail)
    monkeypatch.setattr("app.config_library.calculate", fake_calculate)

    manual_configuration_one_down("manual-office", time_limit_seconds=1, settings_override=settings)

    assert captured_requests
    first_request = captured_requests[0]
    assert first_request.total_people == 3
    assert first_request.officer_staff == []


def test_user_configuration_can_be_deleted(monkeypatch, tmp_path):
    monkeypatch.setenv("KONFMAKER_USER_CONFIG_LIBRARY_JSON", str(tmp_path / "user_configs.json"))
    client = TestClient(app)
    result = calculate(
        make_request(
            total=2,
            fl=2,
            aps=0,
            acs=0,
            fmp=False,
            requested_sector_counts=[1] + [0] * 23,
            include_required_shift_leaders=False,
            include_night_fl_requirement=False,
            required_night_fl_count=0,
            cp_sat_time_limit_seconds=2,
            cp_sat_no_improvement_seconds=0,
        )
    )
    assert result.feasible

    save_response = client.post(
        "/api/manual-configurations/user",
        json={"name": "Test user config", "result": result.model_dump(mode="json")},
    )
    assert save_response.status_code == 200
    saved = save_response.json()
    saved_id = saved["id"]
    assert saved_id.startswith("user:")

    library_response = client.get("/api/manual-configurations")
    assert any(item["id"] == saved_id for item in library_response.json()["configurations"])

    delete_response = client.delete(f"/api/manual-configurations/{saved_id}")
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] is True

    library_after_delete = client.get("/api/manual-configurations").json()
    assert not any(item["id"] == saved_id for item in library_after_delete["configurations"])
    assert client.get(f"/api/manual-configurations/{saved_id}").status_code == 404

    excel_delete_response = client.delete("/api/manual-configurations/1")
    assert excel_delete_response.status_code == 400


def test_manual_configuration_detail_includes_excel_schedule(monkeypatch, tmp_path):
    library_path = tmp_path / "configs.csv"
    library_path.write_text(
        ";25n5\n"
        "MODEL_MAX_SH;64\n"
        "A7;2\n",
        encoding="utf-8",
    )
    workbook_path = tmp_path / "Konfiguracije OKZP.xlsx"
    write_minimal_config_workbook(workbook_path)
    monkeypatch.setenv("KONFMAKER_CONFIG_LIBRARY_CSV", str(library_path))
    monkeypatch.setenv("KONFMAKER_CONFIG_WORKBOOK_XLSX", str(workbook_path))
    client = TestClient(app)

    list_response = client.get("/api/manual-configurations")
    assert list_response.status_code == 200
    library = list_response.json()
    assert library["workbook_path"] == str(workbook_path)
    assert library["configurations"][0]["has_manual_schedule"] is True

    detail_response = client.get("/api/manual-configurations/1")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    schedule = detail["manual_schedule"]
    assert schedule["source_path"] == str(workbook_path)
    assert schedule["max_sector_hours"] == 2
    assert schedule["scheduled_person_hours"] == 4
    assert schedule["people"][0] == {
        "label": "V1",
        "shift": "Vi1",
        "sector_hours": 1,
        "role": "V1",
        "source": "regular",
    }
    first_hour = schedule["hourly_coverage"][0]
    assert first_hour["hour"] == "7.00 - 8.00"
    assert first_hour["open_sectors"] == 2
    assert first_hour["sector_workers"][1] == {
        "sector_name": "LOWER",
        "lower_worker": "B",
        "upper_worker": "C",
    }


def test_manual_configuration_focus_audit_endpoint(monkeypatch, tmp_path):
    library_path = tmp_path / "configs.csv"
    library_path.write_text(
        ";25n5\n"
        "MODEL_MAX_SH;2\n"
        "MODEL_STATUS;OK\n"
        "Vi1;1\n"
        "Vi2;1\n"
        "Vi3;1\n"
        "A7;4\n",
        encoding="utf-8",
    )
    workbook_path = tmp_path / "Konfiguracije OKZP.xlsx"
    write_minimal_config_workbook(workbook_path)
    monkeypatch.setenv("KONFMAKER_CONFIG_LIBRARY_CSV", str(library_path))
    monkeypatch.setenv("KONFMAKER_CONFIG_WORKBOOK_XLSX", str(workbook_path))
    client = TestClient(app)

    response = client.get("/api/manual-configurations/focus-audit?names=25n5&time_limit_seconds=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["focus_names"] == ["25n5"]
    row = payload["rows"][0]
    assert row["name"] == "25n5"
    assert row["exists"] is True
    assert row["manual_sector_hours"] == 2
    assert row["model_sector_hours"] == 2
    assert row["model_missing_sector_hours"] == 0
    assert row["status"] == "covered"
    first_hour = row["hourly_comparison"][0]
    assert first_hour["manual"] == 2
    assert first_hour["model"] == 2
    assert first_hour["manual_sectors"] == ["LOWER", "UPPER"]
    assert "sectors" in first_hour
    assert first_hour["sectors"][1]["sector_name"] == "LOWER"
    assert first_hour["sectors"][1]["manual"] == "B / C"


def test_shortfall_warning_distinguishes_unproven_gap_from_impossible():
    warning = coverage_shortfall_warning(
        65,
        66,
        SolverSnapshot(status="FEASIBLE", best_bound_sector_hours=66, sector_gap_to_best_bound=1),
        "Ni mogoče.",
    )
    assert warning is not None
    assert "še ni dokazal" in warning
    assert "Ni mogoče" not in warning

    proven_warning = coverage_shortfall_warning(
        65,
        66,
        SolverSnapshot(status="OPTIMAL", best_bound_sector_hours=65, sector_gap_to_best_bound=0),
        "Ni mogoče.",
    )
    assert proven_warning == "Ni mogoče."


def test_running_job_result_returns_best_incumbent_and_cancel_keeps_it():
    client = TestClient(app)
    response = client.post(
        "/api/jobs/calculate-sector-hours",
        json=make_request(cp_sat_time_limit_seconds=5).model_dump(),
    )
    assert response.status_code == 200
    started = response.json()

    status = None
    for _ in range(80):
        status_response = client.get(f"/api/jobs/{started['job_id']}")
        assert status_response.status_code == 200
        status = status_response.json()
        if status["best_result_available"] or status["status"] in {"finished", "failed"}:
            break
        time.sleep(0.1)

    assert status is not None
    assert status["best_result_available"] is True

    result_response = client.get(f"/api/jobs/{started['job_id']}/result")
    assert result_response.status_code == 200
    best_data = result_response.json()
    assert "hourly_coverage" in best_data

    if status["status"] not in {"finished", "failed"}:
        cancel_response = client.post(f"/api/jobs/{started['job_id']}/cancel")
        assert cancel_response.status_code == 200

    final_status = status
    for _ in range(80):
        status_response = client.get(f"/api/jobs/{started['job_id']}")
        assert status_response.status_code == 200
        final_status = status_response.json()
        if final_status["status"] in {"finished", "failed"}:
            break
        time.sleep(0.1)

    assert final_status["status"] == "finished"
    assert final_status["best_result_available"] is True


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


def test_default_settings_include_officer_shifts():
    client = TestClient(app)
    response = client.get("/api/default-settings")

    assert response.status_code == 200
    data = response.json()
    assert data["cp_sat_no_improvement_seconds"] == 180
    assert data["cp_sat_acceptable_sector_gap"] == 0
    assert data["cp_sat_min_auto_stop_coverage_percent"] == 95
    assert data["v1_sector_limit"] == 1
    assert data["v2_sector_limit"] == 1
    assert data["v3_sector_limit"] == 4
    assert data["fmp_sector_limit"] == 6
    shift_codes = [shift["code"] for shift in data["shifts"]]
    assert shift_codes[0] == "A6"
    assert next(shift for shift in data["shifts"] if shift["code"] == "A6")["duration_hours"] == 8
    officer_shift_codes = [shift["code"] for shift in data["officer_shifts"]]
    assert officer_shift_codes == ["A6o", "A7o", "A8o", "A9o", "A10o", "A11o", "A14o"]
    assert next(shift for shift in data["officer_shifts"] if shift["code"] == "A14o")["duration_hours"] == 7


def test_default_night_shift_is_limited_to_v3_plus_three_a21():
    result = calculate(make_request())
    assert sum(1 for person in result.people if person.role == "V3" and person.shift == "A21" and person.license == "FL") == 1
    assert sum(1 for person in result.people if person.shift == "A21" and person.license == "FL" and person.role != "V3") == 3
    assert sum(1 for person in result.people if person.shift == "A21") > 3
    assert any(row.shift == "A21" and row.fl == 3 for row in result.shift_summary)
    assert any(row.shift == "V3/A21" and row.fl == 1 for row in result.shift_summary)
    assert result.minimum_required_fl == 7
    assert result.max_sector_hours < 120


def test_shift_leaders_stay_required_when_night_a21_requirement_is_disabled():
    result = calculate(
        make_request(
            include_night_fl_requirement=False,
            required_night_fl_count=0,
        )
    )

    assert any(person.role == "V1" and person.shift == "A7" and person.license == "FL" for person in result.people)
    assert any(person.role == "V2" and person.shift == "A14" and person.license == "FL" for person in result.people)
    assert any(person.role == "V3" and person.shift == "A21" and person.license == "FL" for person in result.people)
    assert any("Nočna A21 FL zahteva je izklopljena" in note for note in result.notes)
    assert not any("Nočna FL zahteva je spremenjena" in warning for warning in result.warnings)


def test_required_shift_leaders_use_vi_display_ids():
    result = calculate(
        make_request(
            include_night_fl_requirement=False,
            required_night_fl_count=0,
        )
    )

    ids_by_role = {person.role: person.id for person in result.people if person.role in {"V1", "V2", "V3"}}
    assert ids_by_role == {"V1": "Vi1", "V2": "Vi2", "V3": "Vi3"}


def test_complete_configuration_can_relax_license_ratio_to_close_shortfall():
    base_request = make_request(
        total=2,
        fl=1,
        aps=0,
        acs=1,
        fmp=False,
        requested_sector_counts=[1] + [0] * 23,
        include_required_shift_leaders=False,
        include_night_fl_requirement=False,
        required_night_fl_count=0,
        cp_sat_time_limit_seconds=2,
        cp_sat_no_improvement_seconds=0,
    )
    current_result = calculate(base_request)

    response = complete_configuration(
        CompleteConfigurationRequest(
            request=base_request,
            current_result=current_result,
            time_limit_seconds=2,
        )
    )

    assert response["calculator"]["feasible"] is True
    assert response["calculator"]["model_sector_hours"] == 1
    assert response["calculator"]["missing_sector_hours"] == 0
    assert response["target_people"] == 2
    result = response["calculator"]["result"]
    assert sum(1 for person in result["people"] if person["license"] == "FL") == 2


def test_fixed_a21_v3_covers_mandatory_v3_shift():
    result = calculate(
        make_request(
            total=3,
            fl=3,
            aps=0,
            acs=0,
            fmp=False,
            requested_sector_counts=[0] * 24,
            fixed_staff=[
                {"count": 1, "license": "FL", "shift": "A7", "role": "V1"},
                {"count": 1, "license": "FL", "shift": "A14", "role": "V2"},
                {"count": 1, "license": "FL", "shift": "A21", "role": "V3"},
            ],
            include_required_shift_leaders=True,
            include_night_fl_requirement=False,
            required_night_fl_count=0,
        )
    )

    assert result.minimum_required_fl == 3
    assert any(person.role == "V3" and person.shift == "A21" for person in result.people)
    assert not any("FL: vpisano 3, potrebno 4" in note for note in result.notes)


def test_configuration_seed_a21_v3_covers_required_v3_shift():
    required = PersonState(id="required-v3", license="FL", shift="V3", role="V3")
    configuration = SimpleNamespace(
        fixed_staff=[SimpleNamespace(count=1, license="FL", shift="A21", role="V3")],
        officer_staff=[],
    )

    seed_people, required_indexes = candidate_pool_from_configuration([required], {0}, configuration)

    assert len(seed_people) == 1
    assert required_indexes == {0}
    assert seed_people[0].shift == "V3"


def test_configuration_seed_ignores_disabled_shift():
    configuration = SimpleNamespace(
        fixed_staff=[
            SimpleNamespace(count=1, license="FL", shift="A11", role=None),
            SimpleNamespace(count=2, license="ACS", shift="A12", role=None),
        ],
        officer_staff=[],
    )

    seed_people, required_indexes = candidate_pool_from_configuration(
        [],
        set(),
        configuration,
        allowed_regular_shift_codes={"A11"},
    )

    assert required_indexes == set()
    assert [person.shift for person in seed_people] == ["A11"]


def test_disabled_regular_shift_is_skipped_for_fixed_and_locked_staff():
    settings = make_request(
        include_required_shift_leaders=False,
        include_night_fl_requirement=False,
    ).settings.model_copy(
        update={
            "shifts": [
                shift.model_copy(update={"enabled": False}) if shift.code == "A12" else shift
                for shift in DEFAULT_SHIFTS
            ]
        }
    )
    request = make_request(
        total=2,
        fl=1,
        aps=0,
        acs=1,
        include_required_shift_leaders=False,
        include_night_fl_requirement=False,
        fixed_staff=[{"count": 1, "license": "ACS", "shift": "A12", "role": None}],
        locked_staff=[{"count": 1, "license": "FL", "shift": "A12", "role": None, "label": "M"}],
    ).model_copy(update={"settings": settings})

    fixed_people, next_id, fixed_notes = add_fixed_staff_people(request, [], 0)
    locked_people, _, locked_notes = add_locked_staff_people(request, fixed_people, next_id)

    assert fixed_people == []
    assert locked_people == []
    assert "izmena ni aktivna" in " ".join([*fixed_notes, *locked_notes])


def test_staff_to_coverage_keeps_office_pool_as_fallback_only():
    result = calculate(
        make_request(
            total=2,
            fl=2,
            aps=0,
            acs=0,
            fmp=False,
            requested_sector_counts=[1] + [0] * 23,
            include_required_shift_leaders=False,
            include_night_fl_requirement=False,
            office_pool=[{"count": 1, "license": "FL"}],
            cp_sat_time_limit_seconds=3,
            cp_sat_no_improvement_seconds=0,
        )
    )

    assert result.max_sector_hours == 1
    assert not any(person.source == "office-pool" for person in result.people)
    assert any("Operativni office pool ni bil uporabljen" in note for note in result.notes)


def test_demand_to_staff_uses_office_pool_as_last_resort():
    result = calculate(
        make_request(
            total=1,
            fl=0,
            aps=0,
            acs=0,
            fmp=False,
            calculation_mode="demand_to_staff",
            requested_sector_counts=[1] + [0] * 23,
            include_required_shift_leaders=False,
            include_night_fl_requirement=False,
            office_pool=[{"count": 1, "license": "FL"}],
            license_mix_percent={"fl": 100, "aps": 0, "acs": 0},
            cp_sat_time_limit_seconds=3,
            cp_sat_no_improvement_seconds=0,
        )
    )

    assert result.max_sector_hours == 1
    assert any(person.source == "office-pool" and person.sector_hours > 0 for person in result.people)
    assert any("Operativni office pool je bil uporabljen šele kot fallback" in note for note in result.notes)


def test_required_role_sector_limits_are_respected():
    result = calculate(
        make_request(
            v1_sector_limit=1,
            v2_sector_limit=2,
            v3_sector_limit=1,
            fmp_sector_limit=2,
            requested_sector_counts=[3] * 24,
        )
    )

    role_limits = {"V1": 1, "V2": 2, "V3": 1, "FMP": 2}
    role_people = {person.role: person for person in result.people if person.role in role_limits}
    assert set(role_people) == set(role_limits)
    for role, limit in role_limits.items():
        assert role_people[role].sector_hours <= limit
        assert role_people[role].max_sector_hours <= limit


def test_manual_schedule_role_limits_do_not_lower_defaults():
    settings = settings_for_manual_schedule_evaluation(
        {
            "people": [
                {"role": "V1", "sector_hours": 3},
                {"role": "V2", "sector_hours": 0},
                {"role": "V3", "sector_hours": 1},
            ]
        }
    )

    assert settings.v1_sector_limit == 3
    assert settings.v2_sector_limit == 1
    assert settings.v3_sector_limit == 4


def test_locked_staff_preserves_label_and_counts_against_people():
    requested = [0] * 24
    requested[3] = 1

    result = calculate(
        make_request(
            total=2,
            fl=2,
            aps=0,
            acs=0,
            fmp=False,
            requested_sector_counts=requested,
            include_required_shift_leaders=False,
            required_night_fl_count=0,
            locked_staff=[{"count": 1, "license": "FL", "shift": "A10", "role": None, "label": "H"}],
        )
    )

    locked_person = next(person for person in result.people if person.source == "what-if")
    assert locked_person.id == "H"
    assert locked_person.shift == "A10"
    assert locked_person.license == "FL"
    assert result.planned_people <= 2
    assert any("What-if je zaklenil 1" in note for note in result.notes)


def test_fixed_night_staff_plus_v3_satisfies_mandatory_night_need():
    result = calculate(
        make_request(
            fixed_staff=[{"count": 3, "license": "FL", "shift": "A21", "role": "noc"}],
            include_required_shift_leaders=True,
            required_night_fl_count=4,
        )
    )

    assert sum(1 for person in result.people if person.role == "V3" and person.shift == "A21" and person.license == "FL") == 1
    assert sum(1 for person in result.people if person.shift == "A21" and person.license == "FL" and person.role != "V3") == 3
    assert sum(1 for person in result.people if person.shift == "A21" and person.license == "FL" and person.role is None) == 0
    assert any(row.shift == "noc/A21" and row.fl == 3 for row in result.shift_summary)
    assert any(row.shift == "V3/A21" and row.fl == 1 for row in result.shift_summary)
    assert any("pokrile 3 obveznih/nočnih mest" in note for note in result.notes)
    assert not any("omejuje A21" in warning for warning in result.warnings)


def test_workers_are_only_scheduled_inside_their_shift_hours():
    request = make_request()
    result = calculate(request)
    people_by_id = {person.id: person for person in result.people}
    shift_by_code = shift_map_for_request(request)

    for slot, coverage in enumerate(result.hourly_coverage):
        for worker_id in coverage.workers:
            person = people_by_id[worker_id]
            shift = shift_by_code[person.shift]
            valid_slots = {
                ((shift.start_hour + offset - 7) % 24) for offset in range(shift.duration_hours)
            }
            assert slot in valid_slots


def test_shift_leaders_are_not_scheduled_on_forbidden_edge_hours():
    requested = [0] * 24
    for slot in (0, 6, 7, 13, 14):
        requested[slot] = 1

    request = make_request(
        total=12,
        fl=12,
        aps=0,
        acs=0,
        fmp=False,
        requested_sector_counts=requested,
        include_required_shift_leaders=True,
        include_night_fl_requirement=False,
        required_night_fl_count=0,
        v1_sector_limit=7,
        v2_sector_limit=7,
        v3_sector_limit=7,
    )
    result = calculate(request)
    people_by_id = {person.id: person for person in result.people}
    shift_by_code = shift_map_for_request(request)

    forbidden_by_role = {}
    for role in ("V1", "V2", "V3"):
        person = next(person for person in result.people if person.role == role)
        slots = [
            ((shift_by_code[person.shift].start_hour + offset - 7) % 24)
            for offset in range(shift_by_code[person.shift].duration_hours)
        ]
        forbidden_by_role[role] = {slots[0]} if role == "V3" else {slots[0], slots[-1]}

    assert result.max_sector_hours == sum(requested)
    for slot, coverage in enumerate(result.hourly_coverage):
        for worker_id in coverage.workers:
            role = people_by_id[worker_id].role
            assert slot not in forbidden_by_role.get(role, set())


def test_v2_can_work_penultimate_shift_hour():
    request = make_request()
    shift_map = shift_map_for_request(request)
    person = PersonState(id="B", license="FL", shift="A14", role="V2")

    assert role_allows_sector_slot(person, hour_index(14), shift_map) is False
    assert role_allows_sector_slot(person, hour_index(19), shift_map) is True
    assert role_allows_sector_slot(person, hour_index(20), shift_map) is False


def test_consecutive_hour_pair_swaps_controller_and_assistant_seats():
    requested = [0] * 24
    requested[0] = 1
    requested[1] = 1

    result = calculate(
        make_request(
            total=2,
            fl=2,
            aps=0,
            acs=0,
            fmp=False,
            requested_sector_counts=requested,
            include_required_shift_leaders=False,
            required_night_fl_count=0,
            cp_sat_time_limit_seconds=3,
        )
    )

    first_sector = next(sector for sector in result.hourly_coverage[0].sector_workers if sector is not None)
    second_sector = next(sector for sector in result.hourly_coverage[1].sector_workers if sector is not None)

    assert first_sector.sector_name == second_sector.sector_name
    assert first_sector.lower_worker == second_sector.upper_worker
    assert first_sector.upper_worker == second_sector.lower_worker


def test_consecutive_two_hour_block_prefers_same_sector_before_switching():
    requested = [0] * 24
    requested[0] = 2
    requested[1] = 2

    result = calculate(
        make_request(
            total=4,
            fl=4,
            aps=0,
            acs=0,
            fmp=False,
            requested_sector_counts=requested,
            include_required_shift_leaders=False,
            required_night_fl_count=0,
            cp_sat_time_limit_seconds=3,
        )
    )

    first_hour = {
        sector.sector_name: {sector.lower_worker, sector.upper_worker}
        for sector in result.hourly_coverage[0].sector_workers
        if sector is not None
    }
    second_hour = {
        sector.sector_name: {sector.lower_worker, sector.upper_worker}
        for sector in result.hourly_coverage[1].sector_workers
        if sector is not None
    }

    assert first_hour == second_hour


def test_hourly_coverage_includes_sector_slots():
    result = calculate(make_request())
    assert all(len(hour.sector_workers) == 6 for hour in result.hourly_coverage)

    for hour in result.hourly_coverage:
        assigned_pairs = [sector for sector in hour.sector_workers if sector is not None]
        assert len(assigned_pairs) == hour.open_sectors
        flattened_workers = [
            worker
            for sector in assigned_pairs
            for worker in (sector.lower_worker, sector.upper_worker)
        ]
        assert flattened_workers == hour.workers


def test_each_open_sector_has_sector_specific_qualified_controllers():
    result = calculate(make_request(total=28, fl=12, aps=6, acs=10))
    people_by_id = {person.id: person for person in result.people}

    for hour in result.hourly_coverage:
        for sector in [item for item in hour.sector_workers if item is not None]:
            first_license = people_by_id[sector.lower_worker].license
            second_license = people_by_id[sector.upper_worker].license
            if sector.sector_name == "ALL":
                assert first_license == "FL"
                assert second_license == "FL"
            elif sector.sector_name == "LOWER":
                assert first_license in {"APS", "FL"}
                assert second_license in {"APS", "FL"}
            else:
                assert first_license in {"ACS", "FL"}
                assert second_license in {"ACS", "FL"}
            assert sector.lower_worker != sector.upper_worker


def test_single_open_sector_is_all_and_requires_two_fl_controllers():
    requested = [0] * 24
    requested[1] = 1

    result = calculate(make_request(total=8, fl=8, aps=0, acs=0, requested_sector_counts=requested))
    people_by_id = {person.id: person for person in result.people}
    assigned_sectors = [sector for sector in result.hourly_coverage[1].sector_workers if sector is not None]

    assert len(assigned_sectors) == 1
    sector = assigned_sectors[0]
    assert sector.sector_name == "ALL"
    assert people_by_id[sector.lower_worker].license == "FL"
    assert people_by_id[sector.upper_worker].license == "FL"
    assert sector.lower_worker != sector.upper_worker


def test_multi_sector_profile_uses_excel_sector_names():
    requested = [0] * 24
    requested[1] = 1
    requested[2] = 2
    requested[3] = 3
    requested[4] = 4
    requested[5] = 5

    result = calculate(make_request(total=28, fl=12, aps=6, acs=10, requested_sector_counts=requested))

    assert [sector.sector_name for sector in result.hourly_coverage[1].sector_workers if sector is not None] == ["ALL"]
    assert [sector.sector_name for sector in result.hourly_coverage[2].sector_workers if sector is not None] == [
        "LOWER",
        "UPPER",
    ]
    assert [sector.sector_name for sector in result.hourly_coverage[3].sector_workers if sector is not None] == [
        "LOWER",
        "UPPER",
        "TOP",
    ]
    assert [sector.sector_name for sector in result.hourly_coverage[4].sector_workers if sector is not None] == [
        "LOWER",
        "UPPER",
        "HIGH",
        "TOP",
    ]
    assert [sector.sector_name for sector in result.hourly_coverage[5].sector_workers if sector is not None] == [
        "LOWER",
        "UPPER",
        "MID",
        "HIGH",
        "TOP",
    ]


def test_requested_sector_counts_limit_open_sectors_by_hour():
    requested = [0] * 24
    requested[0] = 2
    requested[1] = 4

    result = calculate(make_request(requested_sector_counts=requested))

    assert result.hourly_coverage[0].open_sectors <= 2
    assert result.hourly_coverage[1].open_sectors <= 4
    assert all(hour.open_sectors == 0 for hour in result.hourly_coverage[2:])
    assert result.max_sector_hours <= sum(requested)


def test_generator_uses_cp_sat_scheduler():
    result = calculate(make_request())

    assert result.max_sector_hours > 0
    assert any("CP-SAT" in note for note in result.notes)


def test_demand_to_staff_generates_people_for_requested_opening():
    requested = [1] * 24

    result = calculate(make_request(total=0, fl=0, aps=0, acs=0, requested_sector_counts=requested, calculation_mode="demand_to_staff"))

    assert len(result.people) > 0
    assert result.requested_sector_hours == sum(requested)
    assert result.max_sector_hours > 0
    assert result.minimum_required_fl >= 0


def test_demand_to_staff_minimizes_people_for_simple_all_sector():
    requested = [0] * 24
    requested[1] = 1

    result = calculate(
        make_request(
            total=0,
            fl=0,
            aps=0,
            acs=0,
            fmp=False,
            requested_sector_counts=requested,
            calculation_mode="demand_to_staff",
            include_required_shift_leaders=False,
            required_night_fl_count=0,
        )
    )

    assert result.feasible is True
    assert result.planned_people == 2
    assert result.max_sector_hours == 1
    assert all(person.license == "FL" for person in result.people)


def test_minimum_staff_license_counts_are_ratio_not_caps():
    requested = [0] * 24
    requested[1] = 1

    result = calculate(
        make_request(
            total=0,
            fl=1,
            aps=0,
            acs=1,
            fmp=False,
            requested_sector_counts=requested,
            calculation_mode="demand_to_staff",
            include_required_shift_leaders=False,
            required_night_fl_count=0,
            prefer_minimal_fl=False,
        )
    )

    assert result.feasible is True
    assert result.planned_people == 2
    assert result.max_sector_hours == 1
    assert sum(1 for person in result.people if person.license == "FL") == 2
    assert any("kot razmerje licenc" in note for note in result.notes)


def test_demand_with_people_limit_uses_license_percent_not_caps():
    requested = [0] * 24
    requested[2] = 2

    result = calculate(
        make_request(
            total=4,
            fl=0,
            aps=0,
            acs=0,
            fmp=False,
            requested_sector_counts=requested,
            calculation_mode="demand_to_staff",
            include_required_shift_leaders=False,
            required_night_fl_count=0,
            license_mix_percent={"fl": 50, "aps": 0, "acs": 50},
            prefer_minimal_fl=False,
        )
    )

    assert result.feasible is True
    assert result.planned_people == 4
    assert result.max_sector_hours == 2
    assert sum(1 for person in result.people if person.license == "FL") == 2
    assert sum(1 for person in result.people if person.license == "ACS") == 2
    assert any("mehko ciljno razmerje" in note for note in result.notes)


def test_fixed_staff_is_included_in_staff_to_coverage_plan():
    result = calculate(
        make_request(
            total=30,
            fl=12,
            aps=0,
            acs=18,
            fixed_staff=[{"count": 2, "license": "ACS", "shift": "A12", "role": "FIX"}],
        )
    )

    assert len(result.people) <= 30
    assert sum(1 for person in result.people if person.role == "FIX" and person.shift == "A12" and person.license == "ACS") == 2
    assert any(row.shift == "FIX/A12" and row.acs == 2 for row in result.shift_summary)


def test_fixed_staff_counts_must_fit_entered_license_totals():
    result = calculate(
        make_request(
            total=28,
            fl=12,
            aps=0,
            acs=16,
            fixed_staff=[{"count": 17, "license": "ACS", "shift": "A21", "role": "FIX"}],
        )
    )

    assert result.feasible is False
    assert result.people == []
    assert "ACS" in " ".join(result.notes)


def test_fixed_staff_is_seeded_in_demand_to_staff_mode():
    requested = [1] * 24

    result = calculate(
        make_request(
            total=0,
            fl=0,
            aps=0,
            acs=0,
            requested_sector_counts=requested,
            calculation_mode="demand_to_staff",
            fixed_staff=[{"count": 1, "license": "ACS", "shift": "A12", "role": "FIX"}],
        )
    )

    assert any(person.role == "FIX" and person.shift == "A12" and person.license == "ACS" for person in result.people)


def test_fixed_shift_entry_caps_generated_shift_total():
    result = calculate(
        make_request(
            fixed_staff=[{"count": 3, "license": "FL", "shift": "A21"}],
            include_required_shift_leaders=True,
            required_night_fl_count=4,
        )
    )

    assert any(row.shift == "A21" and row.total <= 3 for row in result.shift_summary)
    assert any(row.shift == "V3/A21" and row.fl == 1 for row in result.shift_summary)
    assert not any("omejuje A21" in warning for warning in result.warnings)


def test_pareto_accepts_three_fixed_a21_plus_mandatory_v3():
    requested = [3] * 24
    result = calculate_pareto(
        make_request(
            requested_sector_counts=requested,
            fixed_staff=[{"count": 3, "license": "FL", "shift": "A21"}],
            include_required_shift_leaders=True,
            required_night_fl_count=4,
            cp_sat_time_limit_seconds=1,
        )
    )

    assert result.requested_sector_hours == 72
    assert result.points
    assert max(point.max_sector_hours for point in result.points) > 0
    assert not any("omejuje A21" in warning for warning in result.warnings)


def test_officers_can_cover_missing_all_sector_as_part_of_entered_staff():
    requested = [0] * 24
    requested[1] = 1

    result = calculate(
        make_request(
            total=2,
            fl=2,
            aps=0,
            acs=0,
            fmp=False,
            requested_sector_counts=requested,
            include_required_shift_leaders=False,
            required_night_fl_count=0,
            officer_staff=[{"count": 2, "license": "FL", "shift": "A8o"}],
        )
    )

    assert result.max_sector_hours == 1
    used_officers = [person for person in result.people if person.source == "officer"]
    assert len(used_officers) == 2
    assert all(person.license == "FL" and person.shift == "A8o" for person in used_officers)
    assert any("officerjev" in note for note in result.notes)


def test_concrete_officers_are_selected_even_when_regular_staff_can_cover_same_sector():
    requested = [0] * 24
    requested[1] = 1

    result = calculate(
        make_request(
            total=4,
            fl=4,
            aps=0,
            acs=0,
            fmp=False,
            requested_sector_counts=requested,
            include_required_shift_leaders=False,
            required_night_fl_count=0,
            officer_staff=[{"count": 2, "license": "FL", "shift": "A8o"}],
        )
    )

    assert result.max_sector_hours == 1
    selected_officers = [person for person in result.people if person.source == "officer"]
    assert len(selected_officers) == 2
    assert all(person.shift == "A8o" for person in selected_officers)


def test_office_pool_recommends_minimum_operational_office_shift():
    requested = [0] * 24
    requested[1] = 1

    result = calculate(
        make_request(
            total=1,
            fl=1,
            aps=0,
            acs=0,
            fmp=False,
            requested_sector_counts=requested,
            include_required_shift_leaders=False,
            required_night_fl_count=0,
            office_pool=[{"count": 1, "license": "FL"}],
        )
    )

    active_pool_people = [
        person
        for person in result.people
        if person.source == "office-pool" and person.sector_hours > 0
    ]

    assert result.max_sector_hours == 1
    assert result.planned_people == 2
    assert len(active_pool_people) == 1
    assert active_pool_people[0].license == "FL"
    assert active_pool_people[0].shift.endswith("o")
    assert any("Priporočene operativne office izmene" in note for note in result.notes)


def test_pareto_analysis_reports_coverage_percent_by_people_limit():
    requested = [0] * 24
    requested[1] = 1

    result = calculate_pareto(
        make_request(
            total=4,
            fl=4,
            aps=0,
            acs=0,
            fmp=False,
            requested_sector_counts=requested,
            include_required_shift_leaders=False,
            required_night_fl_count=0,
            cp_sat_time_limit_seconds=4,
            include_pareto=True,
        )
    )

    assert result.points
    assert result.points[0].people_limit == 1
    assert result.points[0].coverage_percent == 0
    feasible_points = [point for point in result.points if point.feasible]
    assert feasible_points[0].people_limit == 2
    assert feasible_points[0].coverage_percent == 100

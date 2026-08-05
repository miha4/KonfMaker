from io import BytesIO
import zipfile

from fastapi.testclient import TestClient

from app.analysis import XlsxReader
from app.main import app
from app.models import (
    CalculatorResponse,
    CalculatorWorkbookRequest,
    HourlyCoverage,
    ParetoPoint,
    SectorAssignment,
    ShiftRule,
    ShiftSummary,
    VirtualPerson,
)
from app.result_workbook import build_result_workbook


def workbook_request() -> CalculatorWorkbookRequest:
    people = [
        VirtualPerson(
            id="A",
            license="FL",
            shift="A7",
            sector_hours=1,
            max_sector_hours=5,
            utilization_percent=20,
            used_as_sector_controller=True,
        ),
        VirtualPerson(
            id="B",
            license="APS",
            shift="A7",
            sector_hours=1,
            max_sector_hours=5,
            utilization_percent=20,
            used_as_sector_controller=True,
        ),
    ]
    hours = [
        HourlyCoverage(
            hour=f"{(7 + slot) % 24:02d}:00–{(8 + slot) % 24:02d}:00",
            open_sectors=1 if slot == 0 else 0,
            workers=["A", "B"] if slot == 0 else [],
            sector_workers=[
                SectorAssignment(
                    sector_name="LOWER",
                    lower_worker="A",
                    upper_worker="B",
                )
                if slot == 0
                else None,
                None,
                None,
                None,
                None,
                None,
            ],
        )
        for slot in range(24)
    ]
    result = CalculatorResponse(
        feasible=True,
        max_sector_hours=1,
        requested_sector_hours=1,
        solver_status="OPTIMAL",
        solver_solution_count=1,
        solver_stop_reason="Optimum je dokazan.",
        minimum_required_fl=0,
        planned_people=2,
        active_people=2,
        unused_people=0,
        scheduled_person_hours=2,
        total_person_capacity_hours=10,
        utilization_percent=20,
        people=people,
        shift_summary=[
            ShiftSummary(shift="A7", fl=1, aps=1, acs=0, total=2),
        ],
        hourly_coverage=hours,
        pareto_points=[
            ParetoPoint(
                people_limit=2,
                planned_people=2,
                active_people=2,
                max_sector_hours=1,
                requested_sector_hours=1,
                coverage_percent=100,
                scheduled_person_hours=2,
                total_person_capacity_hours=10,
                utilization_percent=20,
                feasible=True,
                solver_status="OPTIMAL",
            ),
        ],
        notes=["Testna opomba."],
        warnings=[],
    )
    return CalculatorWorkbookRequest(
        result=result,
        name="Testna konfiguracija",
        target_demand=[1, *([0] * 23)],
        shifts=[
            ShiftRule(code="A7", start_hour=7, duration_hours=8),
        ],
    )


def test_result_workbook_contains_current_export_and_okzp_layout():
    content = build_result_workbook(workbook_request())

    reader = XlsxReader(content)
    assert reader.sheet_names() == ["KONFIGURACIJA", "OKZP RAZPORED"]
    values = set(reader.sheet("KONFIGURACIJA").cells.values())
    assert {
        "URNI SEKTORSKI RAZPORED",
        "IZID",
        "Predlagana sestava izmen",
        "Ljudje in obremenitve",
        "Pareto analiza",
        "A\nFL · A7",
        "B\nAPS · A7",
    }.issubset(values)
    okzp_values = set(reader.sheet("OKZP RAZPORED").cells.values())
    assert {
        "Testna konfiguracija",
        "ID",
        "IZM.",
        "SH",
        "LOC",
        "LOWER",
        "SEKTORSKE URE",
        "OPOMBE",
    }.issubset(okzp_values)

    with zipfile.ZipFile(BytesIO(content)) as archive:
        styles = archive.read("xl/styles.xml").decode("utf-8")
        workbook = archive.read("xl/workbook.xml").decode("utf-8")
        worksheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
        okzp_worksheet = archive.read("xl/worksheets/sheet2.xml").decode("utf-8")
        assert "FFFF9F91" in styles
        assert "FFB7D8FF" in styles
        assert "FF00D974" in styles
        assert '<dxfs count="5">' in styles
        assert "<mergeCells" in worksheet
        assert 'orientation="landscape"' in worksheet
        assert worksheet.index("<sheetFormatPr") < worksheet.index("<cols>")
        assert '<conditionalFormatting sqref="E3:E26">' in okzp_worksheet
        assert '<f>COUNTIF($F$3:$Q$26,A3)</f>' in okzp_worksheet
        assert '<f>COUNTA(F3:Q3)/2</f>' in okzp_worksheet
        assert '<f>SUMPRODUCT(--({"FL","APS"}="FL"))</f>' in okzp_worksheet
        assert '<dimension ref="A1:Q29"/>' in okzp_worksheet
        assert 'showGridLines="0"' in okzp_worksheet
        assert "_xlnm.Print_Area" in workbook


def test_okzp_layout_keeps_people_beyond_the_24_hour_grid():
    request = workbook_request()
    request.result.people = [
        VirtualPerson(
            id=f"K{index}",
            license="ACS",
            shift="A7",
            sector_hours=0,
            max_sector_hours=5,
        )
        for index in range(1, 30)
    ]
    request.result.planned_people = 29
    request.result.active_people = 29
    request.result.unused_people = 29

    content = build_result_workbook(request)
    sheet = XlsxReader(content).sheet("OKZP RAZPORED")

    assert sheet.value(31, 1) == "K29"
    assert sheet.value(33, 1) == "KONTROLORJI"
    assert sheet.value(34, 1) == 29


def test_result_workbook_endpoint_returns_xlsx():
    response = TestClient(app).post(
        "/api/calculator/export",
        json=workbook_request().model_dump(mode="json"),
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert response.content.startswith(b"PK")
    assert XlsxReader(response.content).sheet_names() == [
        "KONFIGURACIJA",
        "OKZP RAZPORED",
    ]

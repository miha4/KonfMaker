import zipfile

from fastapi import Body, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .analysis import WorkbookPayload, export_model_analysis, run_model_analysis, workbook_profile
from .calibration import run_focus_soft_calibration
from .calculator import DEFAULT_OFFICER_SHIFTS, DEFAULT_SHIFTS, calculate
from .config_library import (
    compare_result_to_configurations,
    complete_configuration,
    delete_user_configuration,
    manual_configuration_audit,
    manual_configuration_detail,
    manual_configuration_library,
    manual_configuration_one_down,
    save_user_configuration,
)
from .future_calculator import FutureCalculatorRequest, calculate_future_sector_hours
from .jobs import (
    JobNotFoundError,
    cancel_job,
    create_calculation_job,
    create_complete_configuration_job,
    create_future_calculation_job,
    create_one_down_job,
    create_pareto_job,
    get_job_result,
    get_job_status,
    list_jobs,
)
from .models import (
    CalculatorRequest,
    CompareConfigurationRequest,
    CompleteConfigurationRequest,
    DEFAULT_INCLUDE_NIGHT_FL_REQUIREMENT,
    DEFAULT_REQUIRED_NIGHT_FL_COUNT,
    ManualConfigurationOneDownRequest,
    SaveUserConfigurationRequest,
)
from .pattern_core import pattern_library_profile

app = FastAPI(title="KonfMaker API", version="0.1.0")


class FocusCalibrationRequest(BaseModel):
    names: list[str] = Field(default_factory=list)
    time_limit_seconds: int = 3
    apply_on_success: bool = True

# Development CORS is intentionally permissive because this prototype has no
# cookie/session based authentication. In GitHub Codespaces the recommended
# path is the Vite /api proxy (same browser origin), but these headers also keep
# manual VITE_API_BASE_URL setups working when the frontend and backend are
# opened on different forwarded ports.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["null"],
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
        "cp_sat_time_limit_seconds": 600,
        "cp_sat_no_improvement_seconds": 180,
        "cp_sat_acceptable_sector_gap": 0,
        "cp_sat_min_auto_stop_coverage_percent": 95,
        "include_required_shift_leaders": True,
        "include_night_fl_requirement": DEFAULT_INCLUDE_NIGHT_FL_REQUIREMENT,
        "required_night_fl_count": DEFAULT_REQUIRED_NIGHT_FL_COUNT,
        "v1_sector_limit": 1,
        "v2_sector_limit": 1,
        "v3_sector_limit": 4,
        "fmp_sector_limit": 6,
        "shifts": [shift.model_dump() for shift in DEFAULT_SHIFTS],
        "officer_shifts": [shift.model_dump() for shift in DEFAULT_OFFICER_SHIFTS],
    }


@app.post("/api/calculate-sector-hours")
def calculate_sector_hours(request: CalculatorRequest):
    return calculate(request)


@app.post("/api/future-calculator")
def future_calculator(request: FutureCalculatorRequest):
    return calculate_future_sector_hours(request)


@app.post("/api/complete-configuration")
def complete_current_configuration(request: CompleteConfigurationRequest) -> dict[str, object]:
    try:
        return complete_configuration(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/pattern-library/profile")
def inspect_pattern_library(request: CalculatorRequest) -> dict[str, object]:
    return pattern_library_profile(request)


@app.post("/api/pattern-library/regenerate")
def regenerate_pattern_library(request: CalculatorRequest) -> dict[str, object]:
    return pattern_library_profile(request, regenerate=True)


@app.get("/api/manual-configurations")
def list_manual_configurations() -> dict[str, object]:
    return manual_configuration_library()


@app.post("/api/manual-configurations/user")
def create_user_configuration(request: SaveUserConfigurationRequest) -> dict[str, object]:
    try:
        return save_user_configuration(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Shranjevanje uporabniške konfiguracije ni uspelo: {exc}") from exc


@app.post("/api/manual-configurations/compare-result")
def compare_current_result_to_manual_configurations(request: CompareConfigurationRequest) -> dict[str, object]:
    return compare_result_to_configurations(request)


@app.delete("/api/manual-configurations/{configuration_id}")
def delete_manual_configuration(configuration_id: str) -> dict[str, object]:
    try:
        return delete_user_configuration(configuration_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/manual-configurations/focus-audit")
def inspect_manual_configuration_focus_audit(
    time_limit_seconds: int = 3,
    names: str | None = None,
) -> dict[str, object]:
    selected_names = [part.strip() for part in names.split(",") if part.strip()] if names else None
    return manual_configuration_audit(selected_names, time_limit_seconds=time_limit_seconds)


@app.post("/api/manual-configurations/focus-calibration")
def calibrate_manual_configuration_focus(request: FocusCalibrationRequest) -> dict[str, object]:
    selected_names = [name.strip() for name in request.names if name.strip()] or None
    if selected_names is not None and len(selected_names) < 1:
        raise HTTPException(status_code=400, detail="Za kalibracijo izberi vsaj eno konfiguracijo.")
    return run_focus_soft_calibration(
        selected_names,
        time_limit_seconds=request.time_limit_seconds,
        apply_on_success=request.apply_on_success,
    )


@app.post("/api/manual-configurations/{configuration_id}/one-down")
def inspect_manual_configuration_one_down(
    configuration_id: str,
    time_limit_seconds: int = 3,
    request: ManualConfigurationOneDownRequest | None = Body(default=None),
) -> dict[str, object]:
    try:
        return manual_configuration_one_down(
            configuration_id,
            time_limit_seconds=request.time_limit_seconds if request else time_limit_seconds,
            settings_override=request.settings if request else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/manual-configurations/{configuration_id}")
def inspect_manual_configuration(configuration_id: str) -> dict[str, object]:
    try:
        return manual_configuration_detail(configuration_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/model-analysis/profile")
def inspect_model_workbook(request: WorkbookPayload) -> dict[str, object]:
    try:
        return workbook_profile(request)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/model-analysis/run")
def run_uploaded_model_analysis(request: WorkbookPayload) -> dict[str, object]:
    try:
        return run_model_analysis(request)
    except (KeyError, ValueError, zipfile.BadZipFile) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/model-analysis/export")
def export_uploaded_model_analysis(request: WorkbookPayload):
    try:
        content = export_model_analysis(request)
    except (KeyError, ValueError, zipfile.BadZipFile) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="konfmaker_model_analysis.xlsx"'},
    )


@app.post("/api/jobs/calculate-sector-hours")
def start_calculation_job(request: CalculatorRequest) -> dict[str, str]:
    return create_calculation_job(request)


@app.post("/api/jobs/future-calculator")
def start_future_calculation_job(request: FutureCalculatorRequest) -> dict[str, str]:
    return create_future_calculation_job(request)


@app.post("/api/jobs/complete-configuration")
def start_complete_configuration_job(request: CompleteConfigurationRequest) -> dict[str, str]:
    return create_complete_configuration_job(request)


@app.post("/api/jobs/manual-configurations/{configuration_id}/one-down")
def start_manual_configuration_one_down_job(
    configuration_id: str,
    time_limit_seconds: int = 8,
    request: ManualConfigurationOneDownRequest | None = Body(default=None),
) -> dict[str, str]:
    return create_one_down_job(
        configuration_id,
        time_limit_seconds=request.time_limit_seconds if request else time_limit_seconds,
        settings_override=request.settings if request else None,
    )


@app.post("/api/jobs/pareto-analysis")
def start_pareto_job(request: CalculatorRequest) -> dict[str, str]:
    return create_pareto_job(request)


@app.get("/api/jobs")
def calculation_jobs() -> list[dict[str, object]]:
    return list_jobs()


@app.get("/api/jobs/{job_id}")
def calculation_job_status(job_id: str) -> dict[str, object]:
    try:
        return get_job_status(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Job ne obstaja.") from exc


@app.get("/api/jobs/{job_id}/result")
def calculation_job_result(job_id: str):
    try:
        return get_job_result(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Job ne obstaja.") from exc


@app.post("/api/jobs/{job_id}/cancel")
def cancel_calculation_job(job_id: str) -> dict[str, object]:
    try:
        return cancel_job(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Job ne obstaja.") from exc

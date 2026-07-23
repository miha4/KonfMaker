from __future__ import annotations

import json
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from time import monotonic
from uuid import uuid4

from .calculator import CALC_PHASE_PROGRESS_PREFIX, CalculationCancelled, SolverSnapshot, calculate, calculate_pareto
from .config_library import complete_configuration, manual_configuration_one_down
from .future_calculator import FutureCalculatorRequest, FutureCalculatorResponse, calculate_future_sector_hours
from .models import CalculatorRequest, CalculatorResponse, CalculatorSettings, CompleteConfigurationRequest, ParetoPoint, ParetoResponse
from .pattern_core import PATTERN_PROGRESS_PREFIX

JobStatus = str

JOB_TTL = timedelta(hours=4)
WARM_START_SNAPSHOT_TTL = timedelta(hours=1)

_executor = ThreadPoolExecutor(max_workers=1)
_lock = Lock()
_jobs: dict[str, CalculationJob] = {}
_warm_start_snapshots: dict[str, WarmStartSnapshot] = {}


class JobNotFoundError(KeyError):
    pass


@dataclass
class WarmStartSnapshot:
    snapshot_id: str
    source_job_id: str
    payload: dict[str, object]
    created_at: datetime
    updated_at: datetime
    consumer_job_id: str | None = None


@dataclass
class CalculationJob:
    job_id: str
    kind: str
    status: JobStatus
    progress: int
    message: str
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: CalculatorResponse | ParetoResponse | FutureCalculatorResponse | None = None
    result_version: int = 0
    best_result_updated_at: datetime | None = None
    solver_status: str | None = None
    solver_solution_count: int = 0
    solver_objective_value: float | None = None
    solver_best_objective_bound: float | None = None
    solver_optimality_gap_percent: float | None = None
    solver_stop_reason: str | None = None
    solver_best_bound_sector_hours: int | None = None
    solver_sector_gap_to_best_bound: int | None = None
    calculation_phase: str | None = None
    calculation_phase_label: str | None = None
    calculation_phase_detail: str | None = None
    calculation_next_step: str | None = None
    pattern_phase: str | None = None
    pattern_current_people_limit: int | None = None
    pattern_lower_bound: int | None = None
    pattern_upper_bound: int | None = None
    pattern_limit_index: int | None = None
    pattern_limit_count: int | None = None
    pattern_checked_limits: list[dict[str, object]] | None = None
    pattern_pattern_count: int | None = None
    pattern_cache_status: str | None = None
    pattern_cache_path: str | None = None
    pattern_estimate_low_seconds: int | None = None
    pattern_estimate_high_seconds: int | None = None
    pattern_proven_minimum: bool | None = None
    warm_start_snapshot_id: str | None = None
    consumed_warm_start_snapshot_id: str | None = None
    error: str | None = None
    future: Future[None] | None = None
    cancel_requested: bool = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clamp_progress(progress: int) -> int:
    return max(0, min(100, int(progress)))


def _elapsed_seconds(job: CalculationJob) -> float:
    started_at = job.started_at or job.created_at
    finished_at = job.finished_at or _now()
    return round((finished_at - started_at).total_seconds(), 1)


def _best_pareto_point(result: ParetoResponse) -> ParetoPoint | None:
    if not result.points:
        return None
    return max(
        result.points,
        key=lambda point: (
            point.max_sector_hours,
            point.coverage_percent,
            -point.people_limit,
            point.utilization_percent,
        ),
    )


def _calculator_result_rank(result: CalculatorResponse) -> tuple[int, int, int, int, int]:
    used_officers = sum(
        1
        for person in result.people
        if person.source in {"officer", "office-pool"} and person.sector_hours > 0
    )
    return (
        result.max_sector_hours,
        -result.crisis_exception_hours,
        -used_officers,
        -result.planned_people,
        result.utilization_percent,
    )


def _future_result_rank(result: FutureCalculatorResponse) -> tuple[int, int]:
    return result.covered_quarter_slots, -result.active_people


def _warm_start_payload_from_result(result: CalculatorResponse) -> dict[str, object]:
    return {
        "people": [person.model_dump(mode="json") for person in result.people],
        "hourly_coverage": [hour.model_dump(mode="json") for hour in result.hourly_coverage],
    }


def _refresh_warm_start_snapshot_locked(
    job: CalculationJob,
    result: CalculatorResponse | ParetoResponse | FutureCalculatorResponse | None,
) -> None:
    if not isinstance(result, CalculatorResponse):
        return
    if not result.people or not result.hourly_coverage:
        return

    snapshot_id = job.warm_start_snapshot_id or uuid4().hex
    now = _now()
    existing_snapshot = _warm_start_snapshots.get(snapshot_id)
    job.warm_start_snapshot_id = snapshot_id
    _warm_start_snapshots[snapshot_id] = WarmStartSnapshot(
        snapshot_id=snapshot_id,
        source_job_id=job.job_id,
        payload=_warm_start_payload_from_result(result),
        created_at=existing_snapshot.created_at if existing_snapshot is not None else now,
        updated_at=now,
        consumer_job_id=existing_snapshot.consumer_job_id if existing_snapshot is not None else None,
    )


def _delete_consumed_warm_start_snapshot_locked(job: CalculationJob) -> None:
    snapshot_id = job.consumed_warm_start_snapshot_id
    if snapshot_id is None:
        return
    snapshot = _warm_start_snapshots.get(snapshot_id)
    if snapshot is not None and snapshot.consumer_job_id == job.job_id:
        del _warm_start_snapshots[snapshot_id]
    job.consumed_warm_start_snapshot_id = None


def _consume_warm_start_snapshot_locked(job: CalculationJob, request: CalculatorRequest) -> CalculatorRequest:
    snapshot_id = request.warm_start_snapshot_id
    if not snapshot_id:
        return request

    snapshot = _warm_start_snapshots.get(snapshot_id)
    if snapshot is None:
        return request.model_copy(update={"warm_start_snapshot_id": None})

    snapshot.consumer_job_id = job.job_id
    job.consumed_warm_start_snapshot_id = snapshot_id
    return request.model_copy(
        update={
            "warm_start": snapshot.payload,
            "warm_start_snapshot_id": None,
        }
    )


def _status_payload(job: CalculationJob) -> dict[str, object]:
    best_result = job.result
    if isinstance(best_result, CalculatorResponse):
        best_max_sector_hours = best_result.max_sector_hours
        best_requested_sector_hours = best_result.requested_sector_hours
        best_missing_sector_hours = best_result.missing_sector_hours
        best_planned_people = best_result.planned_people
        best_utilization_percent = best_result.utilization_percent
    elif isinstance(best_result, ParetoResponse):
        best_point = _best_pareto_point(best_result)
        best_max_sector_hours = best_point.max_sector_hours if best_point is not None else None
        best_requested_sector_hours = best_result.requested_sector_hours
        best_missing_sector_hours = best_point.missing_sector_hours if best_point is not None else None
        best_planned_people = best_point.people_limit if best_point is not None else None
        best_utilization_percent = best_point.utilization_percent if best_point is not None else None
    elif isinstance(best_result, FutureCalculatorResponse):
        best_max_sector_hours = best_result.covered_sector_hours
        best_requested_sector_hours = best_result.requested_sector_hours
        best_missing_sector_hours = best_result.missing_sector_hours
        best_planned_people = best_result.planned_people
        best_utilization_percent = None
    else:
        best_max_sector_hours = None
        best_requested_sector_hours = None
        best_missing_sector_hours = None
        best_planned_people = None
        best_utilization_percent = None

    return {
        "job_id": job.job_id,
        "kind": job.kind,
        "status": job.status,
        "progress": job.progress,
        "message": job.message,
        "elapsed_seconds": _elapsed_seconds(job),
        "error": job.error,
        "cancel_requested": job.cancel_requested,
        "best_result_available": best_result is not None,
        "best_result_version": job.result_version,
        "best_max_sector_hours": best_max_sector_hours,
        "best_requested_sector_hours": best_requested_sector_hours,
        "best_missing_sector_hours": best_missing_sector_hours,
        "best_planned_people": best_planned_people,
        "best_utilization_percent": best_utilization_percent,
        "solver_status": job.solver_status,
        "solver_solution_count": job.solver_solution_count,
        "solver_objective_value": job.solver_objective_value,
        "solver_best_objective_bound": job.solver_best_objective_bound,
        "solver_optimality_gap_percent": job.solver_optimality_gap_percent,
        "solver_stop_reason": job.solver_stop_reason,
        "solver_best_bound_sector_hours": job.solver_best_bound_sector_hours,
        "solver_sector_gap_to_best_bound": job.solver_sector_gap_to_best_bound,
        "calculation_phase": job.calculation_phase,
        "calculation_phase_label": job.calculation_phase_label,
        "calculation_phase_detail": job.calculation_phase_detail,
        "calculation_next_step": job.calculation_next_step,
        "pattern_phase": job.pattern_phase,
        "pattern_current_people_limit": job.pattern_current_people_limit,
        "pattern_lower_bound": job.pattern_lower_bound,
        "pattern_upper_bound": job.pattern_upper_bound,
        "pattern_limit_index": job.pattern_limit_index,
        "pattern_limit_count": job.pattern_limit_count,
        "pattern_checked_limits": job.pattern_checked_limits or [],
        "pattern_pattern_count": job.pattern_pattern_count,
        "pattern_cache_status": job.pattern_cache_status,
        "pattern_cache_path": job.pattern_cache_path,
        "pattern_estimate_low_seconds": job.pattern_estimate_low_seconds,
        "pattern_estimate_high_seconds": job.pattern_estimate_high_seconds,
        "pattern_proven_minimum": job.pattern_proven_minimum,
        "warm_start_snapshot_id": job.warm_start_snapshot_id,
    }


def _cleanup_old_jobs_locked() -> None:
    cutoff = _now() - JOB_TTL
    expired_job_ids = [
        job_id
        for job_id, job in _jobs.items()
        if job.finished_at is not None and job.finished_at < cutoff
    ]
    for job_id in expired_job_ids:
        del _jobs[job_id]

    snapshot_cutoff = _now() - WARM_START_SNAPSHOT_TTL
    expired_snapshot_ids = [
        snapshot_id
        for snapshot_id, snapshot in _warm_start_snapshots.items()
        if snapshot.consumer_job_id is None and snapshot.updated_at < snapshot_cutoff
    ]
    for snapshot_id in expired_snapshot_ids:
        del _warm_start_snapshots[snapshot_id]


def _mark_failed_locked(job: CalculationJob, message: str, error: str | None = None) -> None:
    job.status = "failed"
    job.message = message
    job.error = error or message
    job.finished_at = _now()
    _delete_consumed_warm_start_snapshot_locked(job)


def _store_solver_snapshot_locked(job: CalculationJob, solver_snapshot: SolverSnapshot | None) -> None:
    if solver_snapshot is None:
        return
    job.solver_status = solver_snapshot.status
    job.solver_solution_count = max(job.solver_solution_count, solver_snapshot.solution_count)
    job.solver_objective_value = solver_snapshot.objective_value
    job.solver_best_objective_bound = solver_snapshot.best_objective_bound
    job.solver_optimality_gap_percent = solver_snapshot.optimality_gap_percent
    job.solver_stop_reason = solver_snapshot.stop_reason
    job.solver_best_bound_sector_hours = solver_snapshot.best_bound_sector_hours
    job.solver_sector_gap_to_best_bound = solver_snapshot.sector_gap_to_best_bound


def _store_result_solver_fields_locked(
    job: CalculationJob,
    result: CalculatorResponse | ParetoResponse | FutureCalculatorResponse,
) -> None:
    if isinstance(result, CalculatorResponse):
        job.solver_status = result.solver_status
        job.solver_solution_count = max(job.solver_solution_count, result.solver_solution_count)
        job.solver_objective_value = None
        job.solver_best_objective_bound = None
        job.solver_optimality_gap_percent = result.solver_optimality_gap_percent
        job.solver_stop_reason = result.solver_stop_reason
        job.solver_best_bound_sector_hours = result.solver_upper_bound_sector_hours
        job.solver_sector_gap_to_best_bound = result.solver_gap_to_upper_bound
        return

    if isinstance(result, FutureCalculatorResponse):
        job.solver_status = result.solver_status
        job.solver_stop_reason = result.solver_stop_reason
        job.solver_best_bound_sector_hours = result.solver_upper_bound_quarter_slots
        job.solver_sector_gap_to_best_bound = result.solver_gap_quarter_slots
        return

    best_point = _best_pareto_point(result)
    if best_point is None:
        return
    job.solver_status = best_point.solver_status
    job.solver_solution_count = max(job.solver_solution_count, best_point.solver_solution_count)
    job.solver_optimality_gap_percent = best_point.solver_optimality_gap_percent
    job.solver_stop_reason = best_point.solver_stop_reason


def _result_summary(result: CalculatorResponse | ParetoResponse | FutureCalculatorResponse, prefix: str) -> str:
    if isinstance(result, ParetoResponse):
        best_point = _best_pareto_point(result)
        if best_point is None:
            return f"{prefix}: Pareto analiza ni našla izvedljivih točk."
        return (
            f"{prefix}: najboljše {best_point.max_sector_hours}/{result.requested_sector_hours} sektorskih ur "
            f"({best_point.coverage_percent}%) pri limitu {best_point.people_limit} ljudi."
        )
    if isinstance(result, FutureCalculatorResponse):
        return (
            f"{prefix}: {result.covered_sector_hours:g}/{result.requested_sector_hours:g} sektorskih ur, "
            f"manjka {result.missing_sector_hours:g}; {result.active_people} aktivnih ljudi."
        )
    return (
        f"{prefix}: {result.max_sector_hours}/{result.requested_sector_hours} sektorskih ur, "
        f"manjka {result.missing_sector_hours}; {result.planned_people} ljudi, "
        f"izkoriščenost {result.utilization_percent}%."
    )


def _finish_with_result_locked(
    job: CalculationJob,
    result: CalculatorResponse | ParetoResponse | FutureCalculatorResponse,
    message: str,
) -> None:
    if (
        isinstance(result, CalculatorResponse)
        and isinstance(job.result, CalculatorResponse)
        and _calculator_result_rank(job.result) > _calculator_result_rank(result)
    ):
        result = job.result
    elif (
        isinstance(result, FutureCalculatorResponse)
        and isinstance(job.result, FutureCalculatorResponse)
        and _future_result_rank(job.result) > _future_result_rank(result)
    ):
        result = job.result
    job.status = "finished"
    job.progress = 100
    job.result = result
    job.result_version += 1
    job.best_result_updated_at = _now()
    job.finished_at = job.best_result_updated_at
    job.message = message
    job.error = None
    _store_result_solver_fields_locked(job, result)
    _refresh_warm_start_snapshot_locked(job, result)
    _delete_consumed_warm_start_snapshot_locked(job)


def _finish_with_best_result_locked(job: CalculationJob, message: str) -> None:
    if job.result is None:
        _mark_failed_locked(job, "Izračun je bil preklican pred prvo najdeno rešitvijo.", message)
        return
    job.status = "finished"
    job.progress = 100
    job.finished_at = _now()
    job.message = _result_summary(job.result, message)
    job.error = None
    _refresh_warm_start_snapshot_locked(job, job.result)
    _delete_consumed_warm_start_snapshot_locked(job)


def _parse_pattern_progress(message: str) -> tuple[str, dict[str, object] | None]:
    if not message.startswith(PATTERN_PROGRESS_PREFIX):
        return message, None
    try:
        payload = json.loads(message[len(PATTERN_PROGRESS_PREFIX):])
    except json.JSONDecodeError:
        return message, None
    if not isinstance(payload, dict):
        return message, None
    clean_message = payload.get("message")
    return str(clean_message) if clean_message is not None else message, payload


def _parse_calculation_phase_progress(message: str) -> tuple[str, dict[str, object] | None]:
    if not message.startswith(CALC_PHASE_PROGRESS_PREFIX):
        return message, None
    try:
        payload = json.loads(message[len(CALC_PHASE_PROGRESS_PREFIX):])
    except json.JSONDecodeError:
        return message, None
    if not isinstance(payload, dict):
        return message, None
    clean_message = payload.get("message")
    return str(clean_message) if clean_message is not None else message, payload


def _infer_calculation_phase_payload(message: str) -> dict[str, object] | None:
    normalized = message.lower()
    if "zadnja možnost" in normalized or "office pool" in normalized or "operativni office" in normalized:
        return {
            "phase": "office_fallback",
            "label": "Zadnja možnost: office fallback",
            "detail": message,
            "next_step": "Office osebe se preverijo šele po rednih možnostih oziroma na izrecno zahtevo.",
        }
    if "one-down" in normalized:
        return {
            "phase": "one_down",
            "label": "One-down preverjanje",
            "detail": message,
            "next_step": "Model preizkuša iste vhodne podatke z enim človekom manj in nato lažje vzvode.",
        }
    if "dopolnitev" in normalized or "polne konfiguracije" in normalized:
        return {
            "phase": "completion",
            "label": "Dopolnitev konfiguracije",
            "detail": message,
            "next_step": "Model išče najblažji vzvod, ki zapre manjkajoče sektorske ure.",
        }
    if "feasibility" in normalized or "preverja, ali je možnih" in normalized:
        return {
            "phase": "regular_feasibility",
            "label": "Redna faza: dokaz polne pokritosti",
            "detail": message,
            "next_step": "Najprej se preverja, ali lahko redne izmene pokrijejo vse zahtevane ure.",
        }
    if "cp-sat" in normalized or "optimizira" in normalized or "maksimizira" in normalized:
        return {
            "phase": "regular_optimization",
            "label": "Redna faza: CP-SAT optimizacija",
            "detail": message,
            "next_step": "Solver išče najboljšo redno razporeditev brez operativnega office poola.",
        }
    if "pripravljam" in normalized or "validiram" in normalized or "gradim" in normalized:
        return {
            "phase": "preparation",
            "label": "Priprava modela",
            "detail": message,
            "next_step": "Sestavljajo se kandidati, pravila, obvezne vloge in omejitve.",
        }
    return None


def _store_calculation_phase_locked(job: CalculationJob, payload: dict[str, object] | None) -> None:
    if payload is None:
        return
    phase = payload.get("phase")
    if isinstance(phase, str):
        job.calculation_phase = phase
    label = payload.get("label")
    if isinstance(label, str):
        job.calculation_phase_label = label
    detail = payload.get("detail") or payload.get("message")
    if isinstance(detail, str):
        job.calculation_phase_detail = detail
    next_step = payload.get("next_step")
    job.calculation_next_step = next_step if isinstance(next_step, str) else None


def _int_payload(payload: dict[str, object], key: str) -> int | None:
    value = payload.get(key)
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_payload(payload: dict[str, object], key: str) -> float | None:
    value = payload.get(key)
    if isinstance(value, bool) or value is None:
        return None
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


def _store_pattern_progress_locked(job: CalculationJob, payload: dict[str, object] | None) -> None:
    if payload is None:
        return
    phase = payload.get("phase")
    if isinstance(phase, str):
        job.pattern_phase = phase
    for payload_key, attr_name in (
        ("people_limit", "pattern_current_people_limit"),
        ("lower_bound", "pattern_lower_bound"),
        ("upper_bound", "pattern_upper_bound"),
        ("limit_index", "pattern_limit_index"),
        ("limit_count", "pattern_limit_count"),
        ("pattern_count", "pattern_pattern_count"),
        ("estimate_low_seconds", "pattern_estimate_low_seconds"),
        ("estimate_high_seconds", "pattern_estimate_high_seconds"),
    ):
        value = _int_payload(payload, payload_key)
        if value is not None:
            setattr(job, attr_name, value)

    cache_status = payload.get("cache_status")
    if isinstance(cache_status, str):
        job.pattern_cache_status = cache_status
    cache_path = payload.get("cache_path")
    if isinstance(cache_path, str):
        job.pattern_cache_path = cache_path
    proven_minimum = payload.get("proven_minimum")
    if isinstance(proven_minimum, bool):
        job.pattern_proven_minimum = proven_minimum

    if phase == "limit_done":
        people_limit = _int_payload(payload, "people_limit")
        if people_limit is None:
            return
        limit_status = payload.get("limit_status")
        entry = {
            "people_limit": people_limit,
            "status": str(limit_status) if limit_status is not None else "UNKNOWN",
            "elapsed_seconds": _float_payload(payload, "elapsed_seconds"),
        }
        checked_limits = list(job.pattern_checked_limits or [])
        checked_limits = [item for item in checked_limits if item.get("people_limit") != people_limit]
        checked_limits.append(entry)
        checked_limits.sort(key=lambda item: int(item.get("people_limit", 0)))
        job.pattern_checked_limits = checked_limits


def _update_progress(job_id: str, progress: int, message: str) -> None:
    clean_message, phase_payload = _parse_calculation_phase_progress(message)
    clean_message, pattern_payload = _parse_pattern_progress(clean_message)
    if phase_payload is None:
        phase_payload = _infer_calculation_phase_payload(clean_message)
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            raise CalculationCancelled("Izračun ni več na voljo.")
        if job.cancel_requested:
            raise CalculationCancelled("Izračun je bil preklican.")
        job.status = "running"
        job.progress = max(job.progress, _clamp_progress(progress))
        job.message = clean_message
        _store_calculation_phase_locked(job, phase_payload)
        _store_pattern_progress_locked(job, pattern_payload)


def _update_incumbent(job_id: str, result: CalculatorResponse, solver_snapshot: SolverSnapshot | None) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job is None or job.status in {"finished", "failed"}:
            return
        _store_solver_snapshot_locked(job, solver_snapshot)
        if isinstance(job.result, CalculatorResponse) and _calculator_result_rank(job.result) >= _calculator_result_rank(result):
            return
        job.result = result
        job.result_version += 1
        job.best_result_updated_at = _now()
        _refresh_warm_start_snapshot_locked(job, result)
        job.status = "running"
        job.progress = max(job.progress, 90 if result.missing_sector_hours == 0 else 80)
        job.message = _result_summary(result, "Najboljša najdena rešitev")


def _update_future_incumbent(job_id: str, result: FutureCalculatorResponse) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job is None or job.status in {"finished", "failed"} or job.cancel_requested:
            return
        if isinstance(job.result, FutureCalculatorResponse) and _future_result_rank(job.result) >= _future_result_rank(result):
            return
        job.result = result
        job.result_version += 1
        job.best_result_updated_at = _now()
        job.status = "running"
        job.progress = max(job.progress, 25)
        job.solver_solution_count += 1
        _store_result_solver_fields_locked(job, result)
        job.message = _result_summary(result, "Najboljša sprotna rešitev")


def _is_cancel_requested(job_id: str) -> bool:
    with _lock:
        job = _jobs.get(job_id)
        return True if job is None else job.cancel_requested


def _run_calculation_job(job_id: str, request: CalculatorRequest) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        if job.cancel_requested:
            _mark_failed_locked(job, "Izračun je bil preklican.", "Izračun je bil preklican.")
            return
        job.status = "running"
        job.progress = max(job.progress, 5)
        job.message = "Začenjam izračun."
        job.started_at = _now()

    calculation_deadline = monotonic() + request.settings.cp_sat_time_limit_seconds

    def calculation_should_stop() -> bool:
        return _is_cancel_requested(job_id) or monotonic() >= calculation_deadline

    try:
        result = calculate(
            request,
            progress_callback=lambda progress, message: _update_progress(job_id, progress, message),
            cancel_callback=calculation_should_stop,
            incumbent_callback=lambda result, snapshot: _update_incumbent(job_id, result, snapshot),
        )
        with _lock:
            job = _jobs.get(job_id)
            if job is None:
                return
            if job.cancel_requested:
                _finish_with_result_locked(
                    job,
                    result,
                    _result_summary(result, "Optimizacija je bila prekinjena; uporabljena je najboljša najdena rešitev"),
                )
                return
            if result.missing_sector_hours > 0:
                message = f"Končano, manjka še {result.missing_sector_hours} sektorskih ur."
            else:
                message = "Izračun je končan."
            _finish_with_result_locked(job, result, message)
    except CalculationCancelled as exc:
        stopped_on_global_time_limit = monotonic() >= calculation_deadline
        with _lock:
            job = _jobs.get(job_id)
            if job is not None:
                if stopped_on_global_time_limit and not job.cancel_requested:
                    job.solver_stop_reason = (
                        "skupni časovni limit izračuna "
                        f"({request.settings.cp_sat_time_limit_seconds} s) se je iztekel"
                    )
                    _finish_with_best_result_locked(
                        job,
                        "Skupni časovni limit se je iztekel; uporabljena je najboljša najdena rešitev",
                    )
                    return
                _finish_with_best_result_locked(
                    job,
                    "Optimizacija je bila prekinjena; uporabljena je najboljša najdena rešitev",
                )
    except Exception as exc:  # noqa: BLE001 - the job API must expose calculation failures.
        with _lock:
            job = _jobs.get(job_id)
            if job is not None:
                _mark_failed_locked(job, "Izračun se je ustavil zaradi napake.", str(exc))


def _run_future_calculation_job(job_id: str, request: FutureCalculatorRequest) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        if job.cancel_requested:
            _mark_failed_locked(job, "15-minutni izračun je bil preklican.", "Izračun je bil preklican.")
            return
        job.status = "running"
        job.progress = 10
        job.message = "CP-SAT računa 96 četrturnih terminov."
        job.started_at = _now()

    try:
        result = calculate_future_sector_hours(
            request,
            cancel_callback=lambda: _is_cancel_requested(job_id),
            incumbent_callback=lambda result: _update_future_incumbent(job_id, result),
        )
        with _lock:
            job = _jobs.get(job_id)
            if job is None:
                return
            if job.cancel_requested and result.solver_status not in {"FEASIBLE", "OPTIMAL"}:
                prefix = "15-minutni izračun je bil prekinjen pred prvo najdeno rešitvijo"
            elif job.cancel_requested:
                prefix = "15-minutni izračun je bil prekinjen; uporabljena je najboljša najdena rešitev"
            else:
                prefix = "15-minutni izračun je končan"
            _finish_with_result_locked(job, result, _result_summary(result, prefix))
    except Exception as exc:  # noqa: BLE001 - the job API must expose calculation failures.
        with _lock:
            job = _jobs.get(job_id)
            if job is not None:
                _mark_failed_locked(job, "15-minutni izračun se je ustavil zaradi napake.", str(exc))


def _run_pareto_job(job_id: str, request: CalculatorRequest) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        if job.cancel_requested:
            _mark_failed_locked(job, "Pareto analiza je bila preklicana.", "Pareto analiza je bila preklicana.")
            return
        job.status = "running"
        job.progress = max(job.progress, 5)
        job.message = "Začenjam Pareto analizo."
        job.started_at = _now()

    try:
        result = calculate_pareto(
            request,
            progress_callback=lambda progress, message: _update_progress(job_id, progress, message),
            cancel_callback=lambda: _is_cancel_requested(job_id),
        )
        with _lock:
            job = _jobs.get(job_id)
            if job is None:
                return
            if job.cancel_requested:
                _finish_with_result_locked(
                    job,
                    result,
                    _result_summary(result, "Pareto analiza je bila prekinjena; uporabljene so najdene točke"),
                )
                return
            _finish_with_result_locked(job, result, _result_summary(result, "Pareto analiza je končana"))
    except CalculationCancelled as exc:
        with _lock:
            job = _jobs.get(job_id)
            if job is not None:
                _finish_with_best_result_locked(
                    job,
                    "Pareto analiza je bila prekinjena; uporabljene so najdene točke",
                )
    except Exception as exc:  # noqa: BLE001 - the job API must expose calculation failures.
        with _lock:
            job = _jobs.get(job_id)
            if job is not None:
                _mark_failed_locked(job, "Pareto analiza se je ustavila zaradi napake.", str(exc))


def _run_complete_configuration_job(job_id: str, request: CompleteConfigurationRequest) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        if job.cancel_requested:
            _mark_failed_locked(job, "Dopolnitev je bila preklicana.", "Dopolnitev je bila preklicana.")
            return
        job.status = "running"
        job.progress = max(job.progress, 5)
        job.message = "Začenjam dopolnitev do polne konfiguracije."
        job.started_at = _now()

    try:
        response = complete_configuration(
            request,
            progress_callback=lambda progress, message: _update_progress(job_id, progress, message),
            cancel_callback=lambda: _is_cancel_requested(job_id),
            variant_result_callback=lambda result: _update_incumbent(job_id, result, None),
        )
        result = CalculatorResponse.model_validate(response["calculator"]["result"])
        with _lock:
            job = _jobs.get(job_id)
            if job is None:
                return
            if job.cancel_requested:
                _finish_with_result_locked(
                    job,
                    result,
                    _result_summary(result, "Dopolnitev je bila prekinjena; uporabljena je najboljša najdena rešitev"),
                )
                return
            selected_label = response.get("selected_variant_label", "izbrana varianta")
            message = (
                f"Dopolnitev končana: {result.max_sector_hours}/{result.requested_sector_hours} SH, "
                f"manjka {result.missing_sector_hours}. Vzvod: {selected_label}."
            )
            _finish_with_result_locked(job, result, message)
    except CalculationCancelled as exc:
        with _lock:
            job = _jobs.get(job_id)
            if job is not None:
                _finish_with_best_result_locked(
                    job,
                    "Dopolnitev je bila prekinjena; uporabljena je najboljša najdena rešitev",
                )
    except Exception as exc:  # noqa: BLE001 - the job API must expose calculation failures.
        with _lock:
            job = _jobs.get(job_id)
            if job is not None:
                _mark_failed_locked(job, "Dopolnitev se je ustavila zaradi napake.", str(exc))


def _run_one_down_job(
    job_id: str,
    configuration_id: str,
    time_limit_seconds: int,
    settings_override: CalculatorSettings | None,
) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        if job.cancel_requested:
            _mark_failed_locked(job, "One-down je bil preklican.", "One-down je bil preklican.")
            return
        job.status = "running"
        job.progress = max(job.progress, 5)
        job.message = "Začenjam one-down preverjanje."
        job.started_at = _now()

    try:
        response = manual_configuration_one_down(
            configuration_id,
            time_limit_seconds=time_limit_seconds,
            settings_override=settings_override,
            progress_callback=lambda progress, message: _update_progress(job_id, progress, message),
            cancel_callback=lambda: _is_cancel_requested(job_id),
            variant_result_callback=lambda result: _update_incumbent(job_id, result, None),
        )
        result = CalculatorResponse.model_validate(response["calculator"]["result"])
        with _lock:
            job = _jobs.get(job_id)
            if job is None:
                return
            if job.cancel_requested:
                _finish_with_result_locked(
                    job,
                    result,
                    _result_summary(result, "One-down je bil prekinjen; uporabljena je najboljša najdena rešitev"),
                )
                return
            selected_label = response.get("selected_variant_label", "izbrana varianta")
            message = (
                f"One-down končan: {result.max_sector_hours}/{result.requested_sector_hours} SH, "
                f"manjka {result.missing_sector_hours}. Vzvod: {selected_label}."
            )
            _finish_with_result_locked(job, result, message)
    except CalculationCancelled as exc:
        with _lock:
            job = _jobs.get(job_id)
            if job is not None:
                _finish_with_best_result_locked(
                    job,
                    "One-down je bil prekinjen; uporabljena je najboljša najdena rešitev",
                )
    except Exception as exc:  # noqa: BLE001 - the job API must expose calculation failures.
        with _lock:
            job = _jobs.get(job_id)
            if job is not None:
                _mark_failed_locked(job, "One-down se je ustavil zaradi napake.", str(exc))


def create_calculation_job(request: CalculatorRequest) -> dict[str, str]:
    job_id = uuid4().hex
    job = CalculationJob(
        job_id=job_id,
        kind="calculation",
        status="queued",
        progress=0,
        message="Čaka v vrsti za izračun.",
        created_at=_now(),
    )

    with _lock:
        _cleanup_old_jobs_locked()
        request = _consume_warm_start_snapshot_locked(job, request)
        _jobs[job_id] = job

    future = _executor.submit(_run_calculation_job, job_id, request)
    with _lock:
        current_job = _jobs.get(job_id)
        if current_job is not None:
            current_job.future = future

    return {"job_id": job_id, "status": "queued"}


def create_future_calculation_job(request: FutureCalculatorRequest) -> dict[str, str]:
    job_id = uuid4().hex
    job = CalculationJob(
        job_id=job_id,
        kind="future_calculation",
        status="queued",
        progress=0,
        message="Čaka v vrsti za 15-minutni izračun.",
        created_at=_now(),
    )

    with _lock:
        _cleanup_old_jobs_locked()
        _jobs[job_id] = job

    future = _executor.submit(_run_future_calculation_job, job_id, request)
    with _lock:
        current_job = _jobs.get(job_id)
        if current_job is not None:
            current_job.future = future

    return {"job_id": job_id, "status": "queued"}


def create_complete_configuration_job(request: CompleteConfigurationRequest) -> dict[str, str]:
    job_id = uuid4().hex
    job = CalculationJob(
        job_id=job_id,
        kind="complete",
        status="queued",
        progress=0,
        message="Čaka v vrsti za dopolnitev konfiguracije.",
        created_at=_now(),
    )

    with _lock:
        _cleanup_old_jobs_locked()
        resolved_request = _consume_warm_start_snapshot_locked(job, request.request)
        request = request.model_copy(update={"request": resolved_request})
        _jobs[job_id] = job

    future = _executor.submit(_run_complete_configuration_job, job_id, request)
    with _lock:
        current_job = _jobs.get(job_id)
        if current_job is not None:
            current_job.future = future

    return {"job_id": job_id, "status": "queued"}


def create_one_down_job(
    configuration_id: str,
    time_limit_seconds: int = 8,
    settings_override: CalculatorSettings | None = None,
) -> dict[str, str]:
    job_id = uuid4().hex
    job = CalculationJob(
        job_id=job_id,
        kind="one_down",
        status="queued",
        progress=0,
        message="Čaka v vrsti za one-down preverjanje.",
        created_at=_now(),
    )

    with _lock:
        _cleanup_old_jobs_locked()
        _jobs[job_id] = job

    future = _executor.submit(_run_one_down_job, job_id, configuration_id, time_limit_seconds, settings_override)
    with _lock:
        current_job = _jobs.get(job_id)
        if current_job is not None:
            current_job.future = future

    return {"job_id": job_id, "status": "queued"}


def create_pareto_job(request: CalculatorRequest) -> dict[str, str]:
    job_id = uuid4().hex
    job = CalculationJob(
        job_id=job_id,
        kind="pareto",
        status="queued",
        progress=0,
        message="Čaka v vrsti za Pareto analizo.",
        created_at=_now(),
    )

    with _lock:
        _cleanup_old_jobs_locked()
        request = _consume_warm_start_snapshot_locked(job, request)
        _jobs[job_id] = job

    future = _executor.submit(_run_pareto_job, job_id, request)
    with _lock:
        current_job = _jobs.get(job_id)
        if current_job is not None:
            current_job.future = future

    return {"job_id": job_id, "status": "queued"}


def list_jobs() -> list[dict[str, object]]:
    with _lock:
        _cleanup_old_jobs_locked()
        jobs = sorted(_jobs.values(), key=lambda item: item.created_at)
        return [_status_payload(job) for job in jobs]


def get_job_status(job_id: str) -> dict[str, object]:
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        return _status_payload(job)


def get_job_result(
    job_id: str,
) -> CalculatorResponse | ParetoResponse | FutureCalculatorResponse | dict[str, object]:
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        if job.result is not None:
            return job.result
        if job.status == "failed":
            return _status_payload(job)

        payload = _status_payload(job)
        payload["message"] = "Izračun še ni končan; nadaljuj s preverjanjem statusa."
        return payload


def cancel_job(job_id: str) -> dict[str, object]:
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        if job.status in {"finished", "failed"}:
            return _status_payload(job)

        job.cancel_requested = True
        if job.status == "queued" and job.future is not None and job.future.cancel():
            _mark_failed_locked(job, "Izračun je bil preklican pred začetkom.", "Izračun je bil preklican.")
        elif job.result is not None:
            job.message = "Prekinjam optimizacijo in ohranjam najboljšo najdeno rešitev ..."
        else:
            job.message = "Preklicujem izračun ..."
        return _status_payload(job)

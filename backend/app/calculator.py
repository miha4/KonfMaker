from __future__ import annotations

import json
import math
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic
from typing import Callable, Iterable

from ortools.sat.python import cp_model

from .models import (
    CalculatorRequest,
    CalculatorResponse,
    FixedStaffRule,
    HourlyCoverage,
    OfficerStaffRule,
    ParetoPoint,
    ParetoResponse,
    SectorAssignment,
    ShiftRule,
    ShiftSummary,
    VirtualPerson,
)
from .pattern_core import PatternSearchCancelled, calculate_pattern_minimum, can_use_pattern_minimum_core

DAY_START = 7
HOURS_IN_DAY = 24
ALL_SECTOR_LICENSES = {"FL"}
LOWER_SECTOR_LICENSES = {"APS", "FL"}
ABOVE_LOWER_SECTOR_LICENSES = {"ACS", "FL"}
SECTOR_DISPLAY_ORDER = ["ALL", "LOWER", "UPPER", "MID", "HIGH", "TOP"]
SECTOR_PROFILES = {
    0: [],
    1: ["ALL"],
    2: ["LOWER", "UPPER"],
    3: ["LOWER", "UPPER", "TOP"],
    4: ["LOWER", "UPPER", "HIGH", "TOP"],
    5: ["LOWER", "UPPER", "MID", "HIGH", "TOP"],
}
SECTOR_PROFILE_OPTIONS = {
    0: [()],
    1: [("ALL",)],
    2: [("LOWER", "UPPER"), ("LOWER", "TOP")],
    3: [("LOWER", "UPPER", "TOP")],
    4: [("LOWER", "UPPER", "HIGH", "TOP")],
    5: [("LOWER", "UPPER", "MID", "HIGH", "TOP")],
}
SECTOR_PROFILE_PREFERRED_BY_SLOT = {
    2: {
        0: ("LOWER", "UPPER"),
        1: ("LOWER", "UPPER"),
        2: ("LOWER", "UPPER"),
        11: ("LOWER", "UPPER"),
        12: ("LOWER", "UPPER"),
        13: ("LOWER", "UPPER"),
        14: ("LOWER", "UPPER"),
        15: ("LOWER", "UPPER"),
        16: ("LOWER", "TOP"),
        17: ("LOWER", "TOP"),
        23: ("LOWER", "TOP"),
    },
}
CP_SAT_WORKERS = 8
MANUAL_SEED_ATTEMPT_SECONDS = 45
MANUAL_SEED_NO_IMPROVEMENT_SECONDS = 15
COVERED_SECTOR_WEIGHT = 100_000_000
SELECTED_PERSON_PENALTY = 10_000
SELECTED_CAPACITY_PENALTY = 20
LICENSE_MIX_DEVIATION_PENALTY = 20
SECTOR_PROFILE_CHOICE_PENALTY = 3_000
FL_SELECTED_PENALTY = 750
OFFICER_SOURCE = "officer"
OFFICE_POOL_SOURCE = "office-pool"
REGULAR_SOURCE = "regular"
FIXED_SOURCE = "fixed"
WHAT_IF_SOURCE = "what-if"
OFFICER_SELECTED_PENALTY = 120_000
OFFICER_WORK_PENALTY = 5_000
OFFICER_MIDDLE_SLOT_PENALTY = 900
ROLE_EDGE_EXCEPTION_PENALTY = 250_000
FMP_LEADER_OVERLAP_PENALTY = 1_200_000
SECTOR_SWITCH_PENALTY = 10
SEAT_REPEAT_PENALTY = 3
CONFIG_LIBRARY_ENV = "KONFMAKER_CONFIG_LIBRARY_CSV"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_LIBRARY_PATHS = [
    PROJECT_ROOT / "data" / "konfiguracije_okzp_obogateno_vlimiti.csv",
    PROJECT_ROOT / "data" / "konfiguracije_okzp_obogateno.csv",
    Path("/Users/mihamedvesek/Documents/Codex/konfiguracije_okzp_obogateno_vlimiti.csv"),
    Path("/Users/mihamedvesek/Documents/Codex/konfiguracije_okzp_obogateno.csv"),
]
CALC_PHASE_PROGRESS_PREFIX = "__KONFMAKER_PHASE__"


DEFAULT_SHIFTS = [
    ShiftRule(code="A6", start_hour=6, duration_hours=8),
    ShiftRule(code="A7", start_hour=7, duration_hours=7),
    ShiftRule(code="A8", start_hour=8, duration_hours=8),
    ShiftRule(code="A9", start_hour=9, duration_hours=8),
    ShiftRule(code="A10", start_hour=10, duration_hours=8),
    ShiftRule(code="A11", start_hour=11, duration_hours=8),
    ShiftRule(code="A12", start_hour=12, duration_hours=8),
    ShiftRule(code="A13", start_hour=13, duration_hours=8),
    ShiftRule(code="A14", start_hour=14, duration_hours=7),
    ShiftRule(code="A15", start_hour=15, duration_hours=8),
    ShiftRule(code="A16", start_hour=16, duration_hours=8),
    ShiftRule(code="A17", start_hour=17, duration_hours=8),
    ShiftRule(code="A21", start_hour=21, duration_hours=10),
]

DEFAULT_OFFICER_SHIFTS = [
    ShiftRule(code="A6o", start_hour=6, duration_hours=8),
    ShiftRule(code="A7o", start_hour=7, duration_hours=8),
    ShiftRule(code="A8o", start_hour=8, duration_hours=8),
    ShiftRule(code="A9o", start_hour=9, duration_hours=8),
    ShiftRule(code="A10o", start_hour=10, duration_hours=8),
    ShiftRule(code="A11o", start_hour=11, duration_hours=8),
    ShiftRule(code="A14o", start_hour=14, duration_hours=7),
]

MANDATORY_V3_SHIFT = ShiftRule(code="V3", start_hour=21, duration_hours=10)


@dataclass(frozen=True)
class SolverSnapshot:
    status: str
    solution_count: int = 0
    objective_value: float | None = None
    best_objective_bound: float | None = None
    optimality_gap_percent: float | None = None
    stop_reason: str | None = None
    best_bound_sector_hours: int | None = None
    sector_gap_to_best_bound: int | None = None


ProgressCallback = Callable[[int, str], None]
CancelCallback = Callable[[], bool]
ScheduleSolutionCallback = Callable[["ScheduledResult", SolverSnapshot], None]
IncumbentCallback = Callable[[CalculatorResponse, SolverSnapshot | None], None]


class CalculationCancelled(RuntimeError):
    pass


def report_progress(progress_callback: ProgressCallback | None, progress: int, message: str) -> None:
    if progress_callback is not None:
        progress_callback(progress, message)


def phase_progress_message(
    phase: str,
    label: str,
    detail: str,
    next_step: str | None = None,
) -> str:
    return CALC_PHASE_PROGRESS_PREFIX + json.dumps(
        {
            "phase": phase,
            "label": label,
            "detail": detail,
            "next_step": next_step,
            "message": detail,
        },
        ensure_ascii=False,
    )


def report_phase_progress(
    progress_callback: ProgressCallback | None,
    progress: int,
    phase: str,
    label: str,
    detail: str,
    next_step: str | None = None,
) -> None:
    report_progress(progress_callback, progress, phase_progress_message(phase, label, detail, next_step))


def check_cancel(cancel_callback: CancelCallback | None) -> None:
    if cancel_callback is not None and cancel_callback():
        raise CalculationCancelled("Izračun je bil preklican.")


def solver_gap_percent(objective_value: float | None, best_objective_bound: float | None) -> float | None:
    if objective_value is None or best_objective_bound is None:
        return None
    return round(abs(best_objective_bound - objective_value) / max(1.0, abs(objective_value)) * 100, 2)


def coverage_percent(covered_hours: int, requested_hours: int) -> int:
    if requested_hours <= 0:
        return 100
    return round((covered_hours / requested_hours) * 100)


def baseline_min_people_for_profile(target_sector_counts: list[int], norm_hours_per_person: int = 5) -> tuple[int, str]:
    sector_hours = sum(target_sector_counts)
    controller_hours = sector_hours * 2
    n_norm_people = math.ceil(controller_hours / max(1, norm_hours_per_person))
    peak_people = max((count * 2 for count in target_sector_counts), default=0)
    baseline = max(n_norm_people, peak_people)
    formula = (
        f"{sector_hours} SH × 2 = {controller_hours} kontrolorskih ur; "
        f"ceil({controller_hours}/{norm_hours_per_person}) = {n_norm_people}; "
        f"urna konica zahteva {peak_people}; osnovni n{norm_hours_per_person} minimum = {baseline} ljudi."
    )
    return baseline, formula


def coverage_shortfall_warning(
    current_sector_hours: int,
    requested_sector_hours: int,
    solver_snapshot: SolverSnapshot,
    proven_message: str,
) -> str | None:
    if current_sector_hours >= requested_sector_hours:
        return None

    best_bound = solver_snapshot.best_bound_sector_hours
    if solver_snapshot.status == "OPTIMAL" or (best_bound is not None and best_bound <= current_sector_hours):
        return proven_message

    if best_bound is not None:
        if best_bound >= requested_sector_hours:
            return (
                f"CP-SAT je našel {current_sector_hours}/{requested_sector_hours} sektorskih ur, "
                "vendar še ni dokazal, da vseh ni mogoče pokriti; trenutna zgornja meja še dopušča "
                f"{requested_sector_hours}."
            )
        return (
            f"CP-SAT je našel {current_sector_hours}/{requested_sector_hours} sektorskih ur; "
            f"trenutna zgornja meja je {best_bound}, zato končna meja še ni dokazana."
        )

    return (
        f"CP-SAT je našel {current_sector_hours}/{requested_sector_hours} sektorskih ur, "
        "vendar optimalnost še ni dokazana."
    )


@dataclass(frozen=True)
class PersonState:
    id: str
    license: str
    shift: str
    role: str | None = None
    sector_hours: int = 0
    used_as_sector_controller: bool = False
    source: str = REGULAR_SOURCE
    preferred_id: str | None = None


@dataclass(frozen=True)
class SeedConfiguration:
    name: str
    fixed_staff: list[FixedStaffRule]
    officer_staff: list[OfficerStaffRule]
    license_counts: dict[str, int]
    total_without_waiting: int
    waiting_count: int = 0
    unsupported_rows: list[str] | None = None

    @property
    def parsed_total(self) -> int:
        return sum(self.license_counts.values())


@dataclass(frozen=True)
class ScheduledSector:
    sector_name: str
    lower_worker: str
    upper_worker: str


@dataclass(frozen=True)
class ScheduledResult:
    people: list[PersonState]
    hourly_sectors: list[list[ScheduledSector]]
    total_hours: int

    @property
    def hourly_workers(self) -> list[list[str]]:
        return [
            [worker for sector in sectors for worker in (sector.lower_worker, sector.upper_worker)]
            for sectors in self.hourly_sectors
        ]


def hour_index(hour: int) -> int:
    return (hour - DAY_START) % HOURS_IN_DAY


def hour_label(index: int) -> str:
    start = (DAY_START + index) % HOURS_IN_DAY
    end = (start + 1) % HOURS_IN_DAY
    return f"{start:02d}:00–{end:02d}:00"


def shift_slots(shift: ShiftRule) -> set[int]:
    return {hour_index(shift.start_hour + offset) for offset in range(shift.duration_hours)}


def ordered_shift_slots(shift: ShiftRule) -> list[int]:
    return [hour_index(shift.start_hour + offset) for offset in range(shift.duration_hours)]


def max_sector_hours_for_shift(shift: ShiftRule, max_consecutive: int, rest_after_max: int) -> int:
    worked: list[int] = []
    for slot in ordered_shift_slots(shift):
        if can_work_slot(worked, slot, max_consecutive, rest_after_max):
            worked.append(slot)
    return len(worked)


def role_edge_exception_penalty(person: PersonState, slot: int, shift_map: dict[str, ShiftRule]) -> int:
    role = (person.role or "").upper()
    if role not in {"V1", "V2", "V3"}:
        return 0
    shift = shift_map.get(person.shift)
    if shift is None:
        return 0
    slots = ordered_shift_slots(shift)
    if slot not in slots or not slots:
        return 0
    first_slot = slots[0]
    last_slot = slots[-1]
    if role in {"V1", "V2"} and slot in {first_slot, last_slot}:
        return ROLE_EDGE_EXCEPTION_PENALTY
    if role == "V3" and slot == first_slot:
        return ROLE_EDGE_EXCEPTION_PENALTY
    return 0


def role_allows_sector_slot(person: PersonState, slot: int, shift_map: dict[str, ShiftRule]) -> bool:
    # Vi edge slots are allowed as a costly exception, not forbidden outright.
    # The penalty keeps regular legal assignments and office fallback preferred.
    if role_edge_exception_penalty(person, slot, shift_map):
        return True
    return True


def role_sector_limit(request: CalculatorRequest, role: str | None) -> int | None:
    if role == "V1":
        return request.settings.v1_sector_limit
    if role == "V2":
        return request.settings.v2_sector_limit
    if role == "V3":
        return request.settings.v3_sector_limit
    if role == "FMP":
        return request.settings.fmp_sector_limit
    return None


def fmp_leader_overlap_penalty(first: PersonState, second: PersonState) -> int:
    roles = {(first.role or "").upper(), (second.role or "").upper()}
    if "FMP" in roles and roles.intersection({"V1", "V2"}):
        return FMP_LEADER_OVERLAP_PENALTY
    return 0


def max_sector_hours_for_person(
    person: PersonState,
    request: CalculatorRequest,
    shift_map: dict[str, ShiftRule],
) -> int:
    shift_capacity = 0
    worked: list[int] = []
    for slot in ordered_shift_slots(shift_map[person.shift]):
        if not role_allows_sector_slot(person, slot, shift_map):
            continue
        if can_work_slot(
            worked,
            slot,
            request.settings.max_consecutive_work_hours,
            request.settings.rest_after_max_consecutive_hours,
        ):
            worked.append(slot)
            shift_capacity += 1
    limit = role_sector_limit(request, person.role)
    if limit is None:
        return shift_capacity
    return min(shift_capacity, limit)


def label_for_person(number: int) -> str:
    # A..Z, AA..AZ, BA.. for larger generated configurations.
    label = ""
    n = number
    while True:
        label = chr(ord("A") + (n % 26)) + label
        n = n // 26 - 1
        if n < 0:
            return label


def role_display_id(role: str | None) -> str | None:
    if role == "V1":
        return "Vi1"
    if role == "V2":
        return "Vi2"
    if role == "V3":
        return "Vi3"
    return None


def role_penalty(person: PersonState) -> int:
    if person.role == "FMP":
        return 4
    if person.role in {"V1", "V2", "V3"}:
        return 2
    return 0


def is_officer(person: PersonState) -> bool:
    return person.source in {OFFICER_SOURCE, OFFICE_POOL_SOURCE}


def officer_slot_edge_penalty(person: PersonState, slot: int, shift_map: dict[str, ShiftRule]) -> int:
    if not is_officer(person):
        return 0
    shift = shift_map.get(person.shift)
    if shift is None:
        return 0
    slots = ordered_shift_slots(shift)
    if slot not in slots or len(slots) <= 2:
        return 0
    slot_position = slots.index(slot)
    edge_distance = min(slot_position, len(slots) - 1 - slot_position)
    return edge_distance * OFFICER_MIDDLE_SLOT_PENALTY


def display_shift_for_person(person: PersonState) -> str:
    if person.role == "V3" and person.shift == "V3":
        return "A21"
    return person.shift


def enabled_shift_rules(shifts: list[ShiftRule]) -> list[ShiftRule]:
    return [shift for shift in shifts if shift.enabled]


def enabled_regular_shift_codes(request: CalculatorRequest) -> set[str]:
    return {shift.code for shift in enabled_shift_rules(request.settings.shifts)}


def enabled_officer_shift_codes(request: CalculatorRequest) -> set[str]:
    return {shift.code for shift in enabled_shift_rules(request.settings.officer_shifts)}


def shift_map_for_request(request: CalculatorRequest) -> dict[str, ShiftRule]:
    configured_shift_codes = {shift.code for shift in request.settings.shifts}
    mandatory_shifts = [] if "V3" in configured_shift_codes else [MANDATORY_V3_SHIFT]
    return {
        shift.code: shift
        for shift in [*request.settings.shifts, *mandatory_shifts, *request.settings.officer_shifts]
    }


def required_a21_fl_count(request: CalculatorRequest) -> int:
    if not request.settings.include_night_fl_requirement:
        return 0
    included_v3 = 1 if request.settings.include_required_shift_leaders else 0
    return max(0, request.settings.required_night_fl_count - included_v3)


def night_shift_license_caps(request: CalculatorRequest) -> dict[tuple[str, str], int]:
    required_count = required_a21_fl_count(request)
    if required_count <= 0:
        return {}
    return {("A21", "FL"): required_count}


def can_work_slot(worked: list[int], slot: int, max_consecutive: int, rest_after_max: int) -> bool:
    candidate_worked = set(worked) | {slot}
    consecutive_before = 0
    cursor = slot - 1
    while cursor in candidate_worked:
        consecutive_before += 1
        cursor -= 1
    if consecutive_before >= max_consecutive:
        return False

    window_size = max_consecutive + rest_after_max
    for window_start in range(max(0, slot - window_size + 1), slot + 1):
        window = range(window_start, window_start + window_size)
        if slot in window and sum(1 for item in window if item in candidate_worked) > max_consecutive:
            return False
    return True


def sector_names_for_count(sector_count: int) -> list[str]:
    if sector_count in SECTOR_PROFILES:
        return list(SECTOR_PROFILES[sector_count])

    extras_needed = max(0, sector_count - 5)
    return list(SECTOR_PROFILES[5]) + [f"EXTRA {index}" for index in range(6, 6 + extras_needed)]


def ordered_sector_profile(sectors: Iterable[str]) -> tuple[str, ...]:
    sector_set = {sector for sector in sectors if sector}
    known = [sector for sector in SECTOR_DISPLAY_ORDER if sector in sector_set]
    extras = sorted(sector for sector in sector_set if sector not in SECTOR_DISPLAY_ORDER)
    return tuple([*known, *extras])


def sector_profile_options_for_count(sector_count: int) -> list[tuple[str, ...]]:
    if sector_count in SECTOR_PROFILE_OPTIONS:
        return [tuple(profile) for profile in SECTOR_PROFILE_OPTIONS[sector_count]]
    return [tuple(sector_names_for_count(sector_count))]


def sector_names_for_profile_options(profile_options: list[tuple[str, ...]]) -> list[str]:
    return list(ordered_sector_profile(sector for profile in profile_options for sector in profile))


def preferred_sector_profile_for_slot(
    slot: int,
    sector_count: int,
    requested_sector_hours: int | None = None,
) -> tuple[str, ...]:
    if sector_count == 2 and requested_sector_hours is not None:
        if slot == 16 and requested_sector_hours <= 64:
            return ("LOWER", "UPPER")
        if slot == 23 and requested_sector_hours <= 59:
            return ("LOWER", "UPPER")
    preferred = SECTOR_PROFILE_PREFERRED_BY_SLOT.get(sector_count, {}).get(slot)
    if preferred is not None:
        return tuple(preferred)
    return tuple(sector_names_for_count(sector_count))


def sector_profile_distance(left: Iterable[str], right: Iterable[str]) -> int:
    return len(set(left) ^ set(right))


def sector_profile_choice_penalty(
    slot: int,
    sector_count: int,
    profile: tuple[str, ...],
    requested_sector_hours: int | None = None,
) -> int:
    preferred = preferred_sector_profile_for_slot(slot, sector_count, requested_sector_hours)
    return sector_profile_distance(profile, preferred) * SECTOR_PROFILE_CHOICE_PENALTY


def sector_display_names_for_max(max_sector_count: int) -> list[str]:
    extras_needed = max(0, max_sector_count - 5)
    return list(SECTOR_DISPLAY_ORDER) + [f"EXTRA {index}" for index in range(6, 6 + extras_needed)]


def sector_allowed_licenses(sector_name: str) -> set[str]:
    if sector_name == "ALL":
        return ALL_SECTOR_LICENSES
    if sector_name == "LOWER":
        return LOWER_SECTOR_LICENSES
    return ABOVE_LOWER_SECTOR_LICENSES


def can_fill_sector(person: PersonState, sector_name: str) -> bool:
    return person.license in sector_allowed_licenses(sector_name)


def sector_license_preference(person: PersonState, sector_name: str) -> int:
    if sector_name == "ALL":
        return 0
    if sector_name == "LOWER":
        return 0 if person.license == "APS" else 1
    return 0 if person.license == "ACS" else 1


def build_schedule(
    people: list[PersonState],
    shift_map: dict[str, ShiftRule],
    target_sector_counts: list[int],
    max_consecutive: int,
    rest_after_max: int,
) -> ScheduledResult:
    available_slots = {person.id: shift_slots(shift_map[person.shift]) for person in people}
    ordered_slots = {person.id: ordered_shift_slots(shift_map[person.shift]) for person in people}
    capacity_by_person = {}
    for person in people:
        worked_for_capacity: list[int] = []
        for candidate_slot in ordered_slots[person.id]:
            if not role_allows_sector_slot(person, candidate_slot, shift_map):
                continue
            if can_work_slot(worked_for_capacity, candidate_slot, max_consecutive, rest_after_max):
                worked_for_capacity.append(candidate_slot)
        capacity_by_person[person.id] = len(worked_for_capacity)
    worked_slots: dict[str, list[int]] = defaultdict(list)
    person_map = {person.id: person for person in people}
    hourly_sectors: list[list[ScheduledSector]] = []

    for slot in range(HOURS_IN_DAY):
        scheduled_sectors: list[ScheduledSector] = []

        def available_for_current_slot(person: PersonState) -> bool:
            if slot not in available_slots[person.id]:
                return False
            if not role_allows_sector_slot(person, slot, shift_map):
                return False
            previous = worked_slots[person.id]
            if slot in previous:
                return False
            return can_work_slot(previous, slot, max_consecutive, rest_after_max)

        def remaining_slots(person: PersonState) -> int:
            return len(
                [
                    future
                    for future in available_slots[person.id]
                    if future >= slot and role_allows_sector_slot(person, future, shift_map)
                ]
            )

        def remaining_capacity(person: PersonState) -> int:
            current = person_map[person.id]
            return max(0, capacity_by_person[person.id] - current.sector_hours)

        def workable_future_slots(person: PersonState) -> int:
            previous = worked_slots[person.id]
            return len(
                [
                    future
                    for future in ordered_slots[person.id]
                    if future >= slot
                    and role_allows_sector_slot(person, future, shift_map)
                    and future not in previous
                    and can_work_slot(previous, future, max_consecutive, rest_after_max)
                ]
            )

        def capacity_slack(person: PersonState) -> int:
            return workable_future_slots(person) - remaining_capacity(person)

        def slot_fmp_leader_overlap_penalty(lower: PersonState, upper: PersonState) -> int:
            existing_workers = [
                person_map[worker_id]
                for sector in scheduled_sectors
                for worker_id in (sector.lower_worker, sector.upper_worker)
                if worker_id in person_map
            ]
            return (
                fmp_leader_overlap_penalty(lower, upper)
                + sum(fmp_leader_overlap_penalty(lower, existing) for existing in existing_workers)
                + sum(fmp_leader_overlap_penalty(upper, existing) for existing in existing_workers)
            )

        def pair_key(pair: tuple[PersonState, PersonState], sector_name: str) -> tuple[int, int, int, int, int, int, int, str, str]:
            lower, upper = pair
            return (
                capacity_slack(lower) + capacity_slack(upper),
                sector_license_preference(lower, sector_name) + sector_license_preference(upper, sector_name),
                role_penalty(lower) + role_penalty(upper),
                role_edge_exception_penalty(lower, slot, shift_map)
                + role_edge_exception_penalty(upper, slot, shift_map),
                slot_fmp_leader_overlap_penalty(lower, upper),
                lower.sector_hours + upper.sector_hours,
                remaining_slots(lower) + remaining_slots(upper),
                lower.id,
                upper.id,
            )

        for sector_name in sector_names_for_count(target_sector_counts[slot]):
            candidates = [person for person in people if available_for_current_slot(person)]
            sector_candidates = [person for person in candidates if can_fill_sector(person, sector_name)]

            best_pair: tuple[PersonState, PersonState] | None = None
            best_pair_key: tuple[int, int, int, int, int, int, int, str, str] | None = None
            for lower in sector_candidates:
                for upper in sector_candidates:
                    if upper.id == lower.id:
                        continue
                    current_pair = (lower, upper)
                    current_key = pair_key(current_pair, sector_name)
                    if best_pair_key is None or current_key < best_pair_key:
                        best_pair = current_pair
                        best_pair_key = current_key

            if best_pair is None:
                break

            lower, upper = best_pair
            scheduled_sectors.append(
                ScheduledSector(
                    sector_name=sector_name,
                    lower_worker=lower.id,
                    upper_worker=upper.id,
                )
            )

            for person in (lower, upper):
                worked_slots[person.id].append(slot)
                person_map[person.id] = replace(
                    person_map[person.id],
                    sector_hours=person_map[person.id].sector_hours + 1,
                    used_as_sector_controller=True,
                )

            people = [person_map[person.id] for person in people]

        hourly_sectors.append(scheduled_sectors)

    scheduled_people = [person_map[person.id] for person in people]
    return ScheduledResult(
        people=scheduled_people,
        hourly_sectors=hourly_sectors,
        total_hours=sum(len(sectors) for sectors in hourly_sectors),
    )


def summarize_shifts(people: list[PersonState]) -> list[ShiftSummary]:
    shift_order = [shift.code for shift in [*DEFAULT_SHIFTS, MANDATORY_V3_SHIFT, *DEFAULT_OFFICER_SHIFTS]]
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for person in people:
        display_shift = display_shift_for_person(person)
        if is_officer(person):
            key = f"officer/{display_shift}"
        else:
            key = f"{person.role}/{display_shift}" if person.role else display_shift
        counts[key][person.license] += 1

    def sort_key(item: tuple[str, Counter[str]]) -> tuple[int, str]:
        shift = item[0].split("/")[-1]
        try:
            return (shift_order.index(shift), item[0])
        except ValueError:
            return (999, item[0])

    summaries: list[ShiftSummary] = []
    for shift, counter in sorted(counts.items(), key=sort_key):
        fl = counter["FL"]
        aps = counter["APS"]
        acs = counter["ACS"]
        summaries.append(ShiftSummary(shift=shift, fl=fl, aps=aps, acs=acs, total=fl + aps + acs))
    return summaries


def smooth_sector_rotations(
    hourly_sectors: list[list[ScheduledSector]],
    people: list[PersonState],
) -> list[list[ScheduledSector]]:
    people_by_id = {person.id: person for person in people}
    previous_positions: dict[str, tuple[str, int]] = {}
    smoothed_hours: list[list[ScheduledSector]] = []

    def assignment_cost(worker_id: str, sector_name: str, seat: int) -> int:
        previous = previous_positions.get(worker_id)
        if previous is None:
            return 0
        previous_sector, previous_seat = previous
        if previous_sector != sector_name:
            return SECTOR_SWITCH_PENALTY
        if previous_seat == seat:
            return SEAT_REPEAT_PENALTY
        return 0

    def can_assign(worker_id: str, sector_name: str) -> bool:
        person = people_by_id.get(worker_id)
        return person is not None and can_fill_sector(person, sector_name)

    for sectors in hourly_sectors:
        if not sectors:
            previous_positions = {}
            smoothed_hours.append([])
            continue

        chosen_sectors = list(sectors)

        def hour_cost(candidate: list[ScheduledSector]) -> int:
            return sum(
                assignment_cost(worker_id, sector.sector_name, seat)
                for sector in candidate
                for seat, worker_id in enumerate((sector.lower_worker, sector.upper_worker))
            )

        def worker_at(candidate: list[ScheduledSector], sector_index: int, seat: int) -> str:
            sector = candidate[sector_index]
            return sector.lower_worker if seat == 0 else sector.upper_worker

        def replace_worker(
            candidate: list[ScheduledSector],
            sector_index: int,
            seat: int,
            worker_id: str,
        ) -> None:
            sector = candidate[sector_index]
            if seat == 0:
                candidate[sector_index] = ScheduledSector(sector.sector_name, worker_id, sector.upper_worker)
            else:
                candidate[sector_index] = ScheduledSector(sector.sector_name, sector.lower_worker, worker_id)

        def positions(candidate: list[ScheduledSector]) -> list[tuple[int, int]]:
            return [
                (sector_index, seat)
                for sector_index in range(len(candidate))
                for seat in range(2)
            ]

        improved = True
        while improved:
            improved = False
            current_cost = hour_cost(chosen_sectors)
            for first_position_index, first_position in enumerate(positions(chosen_sectors)):
                for second_position in positions(chosen_sectors)[first_position_index + 1:]:
                    first_sector_index, first_seat = first_position
                    second_sector_index, second_seat = second_position
                    first_worker = worker_at(chosen_sectors, first_sector_index, first_seat)
                    second_worker = worker_at(chosen_sectors, second_sector_index, second_seat)
                    first_sector_name = chosen_sectors[first_sector_index].sector_name
                    second_sector_name = chosen_sectors[second_sector_index].sector_name
                    if (
                        not can_assign(second_worker, first_sector_name)
                        or not can_assign(first_worker, second_sector_name)
                    ):
                        continue

                    candidate = list(chosen_sectors)
                    replace_worker(candidate, first_sector_index, first_seat, second_worker)
                    replace_worker(candidate, second_sector_index, second_seat, first_worker)
                    candidate_cost = hour_cost(candidate)
                    if candidate_cost < current_cost:
                        chosen_sectors = candidate
                        improved = True
                        break
                if improved:
                    break

        previous_positions = {
            worker_id: (sector.sector_name, seat)
            for sector in chosen_sectors
            for seat, worker_id in enumerate((sector.lower_worker, sector.upper_worker))
        }
        smoothed_hours.append(chosen_sectors)

    return smoothed_hours


def create_mandatory_people(
    request: CalculatorRequest,
    existing_people: list[PersonState] | None = None,
    next_id: int = 0,
) -> tuple[list[PersonState], int, list[str], list[str]]:
    people = list(existing_people or [])
    fixed_candidates = list(existing_people or [])
    used_fixed_indexes: set[int] = set()
    notes: list[str] = []
    warnings: list[str] = []

    def add(license_name: str, shift: str, role: str | None = None) -> None:
        nonlocal next_id
        people.append(
            PersonState(
                id=label_for_person(next_id),
                license=license_name,
                shift=shift,
                role=role,
                source=REGULAR_SOURCE,
            )
        )
        next_id += 1

    def fixed_covers_requirement(license_name: str, shift: str, role: str | None = None) -> bool:
        def shift_matches(person_shift: str) -> bool:
            if person_shift == shift:
                return True
            return shift == "V3" and role == "V3" and person_shift == "A21"

        for index, person in enumerate(fixed_candidates):
            if index in used_fixed_indexes:
                continue
            if person.license != license_name or not shift_matches(person.shift):
                continue
            if role is not None and (person.role or "").strip().upper() != role:
                continue
            used_fixed_indexes.add(index)
            return True
        return False

    covered_required_count = 0

    def require(license_name: str, shift: str, role: str | None = None) -> None:
        nonlocal covered_required_count
        if fixed_covers_requirement(license_name, shift, role):
            covered_required_count += 1
            return
        add(license_name, shift, role)

    if request.settings.include_required_shift_leaders:
        require("FL", "A7", "V1")
        require("FL", "A14", "V2")
        require("FL", "V3", "V3")

    night_without_v3 = required_a21_fl_count(request)
    for _ in range(night_without_v3):
        require("FL", "A21", None)

    if request.include_fmp:
        require("FL", "A9", "FMP")

    if covered_required_count:
        notes.append(
            f"Fiksno vpisane izmene so pokrile {covered_required_count} obveznih/nočnih mest, "
            "zato jih generator ni dodal ponovno."
        )

    if request.settings.include_night_fl_requirement and request.settings.required_night_fl_count != 4:
        warnings.append("Nočna FL zahteva je spremenjena iz privzete vrednosti 4.")

    return people, next_id, notes, warnings


def add_fixed_staff_people(
    request: CalculatorRequest,
    people: list[PersonState],
    next_id: int,
) -> tuple[list[PersonState], int, list[str]]:
    fixed_people = list(people)
    notes: list[str] = []
    active_shift_codes = enabled_regular_shift_codes(request)
    skipped = 0

    for item in request.fixed_staff:
        if item.shift not in active_shift_codes:
            skipped += item.count
            continue
        for _ in range(item.count):
            fixed_people.append(
                PersonState(
                    id=label_for_person(next_id),
                    license=item.license,
                    shift=item.shift,
                    role=item.role,
                    source=FIXED_SOURCE,
                )
            )
            next_id += 1

    if request.fixed_staff:
        total_fixed = sum(item.count for item in request.fixed_staff) - skipped
        if total_fixed:
            notes.append(f"Uporabnik je dodal {total_fixed} fiksno vpisanih ljudi/izmen.")
        if skipped:
            notes.append(f"Preskočenih je {skipped} fiksno vpisanih ljudi, ker njihova izmena ni aktivna.")

    return fixed_people, next_id, notes


def add_locked_staff_people(
    request: CalculatorRequest,
    people: list[PersonState],
    next_id: int,
) -> tuple[list[PersonState], int, list[str]]:
    locked_people = list(people)
    notes: list[str] = []
    active_shift_codes = enabled_regular_shift_codes(request)
    skipped = 0

    for item in request.locked_staff:
        if item.shift not in active_shift_codes:
            skipped += item.count
            continue
        for _ in range(item.count):
            locked_people.append(
                PersonState(
                    id=label_for_person(next_id),
                    license=item.license,
                    shift=item.shift,
                    role=item.role,
                    source=WHAT_IF_SOURCE,
                    preferred_id=item.label,
                )
            )
            next_id += 1

    if request.locked_staff:
        total_locked = sum(item.count for item in request.locked_staff) - skipped
        if total_locked:
            notes.append(f"What-if je zaklenil {total_locked} človeka/ljudi v izbrano izmeno brez spremembe skupnega števila ljudi.")
        if skipped:
            notes.append(f"Preskočenih je {skipped} what-if zaklenjenih ljudi, ker njihova izmena ni aktivna.")

    return locked_people, next_id, notes


def add_officer_staff_people(
    request: CalculatorRequest,
    people: list[PersonState],
    next_id: int,
) -> tuple[list[PersonState], int, list[str]]:
    officer_people = list(people)
    notes: list[str] = []
    active_shift_codes = enabled_officer_shift_codes(request)
    skipped = 0

    for item in request.officer_staff:
        if item.shift not in active_shift_codes:
            skipped += item.count
            continue
        for _ in range(item.count):
            officer_people.append(
                PersonState(
                    id=label_for_person(next_id),
                    license=item.license,
                    shift=item.shift,
                    role=None,
                    source=OFFICER_SOURCE,
                )
            )
            next_id += 1

    total_officers = sum(item.count for item in request.officer_staff) - skipped
    if total_officers:
        notes.append(
            f"Uporabnik je dodal {total_officers} konkretnih office izmen; obvezno so vključene v plan, "
            "sektorske ure pa dobijo samo, ko jih solver potrebuje."
        )
    if skipped:
        notes.append(f"Preskočenih je {skipped} office izmen, ker njihova izmena ni aktivna.")

    return officer_people, next_id, notes


def office_pool_counts_by_license(request: CalculatorRequest) -> Counter[str]:
    return Counter({license_name: 0 for license_name in ("FL", "APS", "ACS")}) + Counter(
        {
            license_name: sum(item.count for item in request.office_pool if item.license == license_name)
            for license_name in ("FL", "APS", "ACS")
        }
    )


def total_office_pool_count(request: CalculatorRequest) -> int:
    return sum(office_pool_counts_by_license(request).values())


def add_office_pool_candidates(
    request: CalculatorRequest,
    people: list[PersonState],
    next_id: int,
    target_sector_counts: list[int],
) -> tuple[list[PersonState], int, list[str]]:
    pool_people = list(people)
    notes: list[str] = []
    pool_counts = office_pool_counts_by_license(request)
    allowed_shift_codes = candidate_shift_codes(enabled_shift_rules(request.settings.officer_shifts), target_sector_counts)

    for license_name in ("FL", "APS", "ACS"):
        count = pool_counts[license_name]
        for shift_code in allowed_shift_codes:
            for _ in range(count):
                pool_people.append(
                    PersonState(
                        id=label_for_person(next_id),
                        license=license_name,
                        shift=shift_code,
                        role=None,
                        source=OFFICE_POOL_SOURCE,
                    )
                )
                next_id += 1

    total_pool = sum(pool_counts.values())
    if total_pool:
        notes.append(
            f"Na voljo je {total_pool} operativnih officev iz priporočilnega modula; "
            "CP-SAT sam izbere najmanj potrebne office izmene."
        )

    return pool_people, next_id, notes


def target_sector_counts_for_request(request: CalculatorRequest) -> list[int]:
    if request.requested_sector_counts is None:
        return [request.settings.max_sectors_per_hour] * HOURS_IN_DAY
    return request.requested_sector_counts


def shift_demand_score(shift: ShiftRule, target_sector_counts: list[int]) -> int:
    return sum(target_sector_counts[slot] for slot in shift_slots(shift))


def generated_shift_key(
    shift_code: str,
    shift_map: dict[str, ShiftRule],
    target_sector_counts: list[int],
    people: list[PersonState],
) -> tuple[int, int, int, str]:
    shift = shift_map[shift_code]
    assigned_to_shift = sum(1 for person in people if person.shift == shift_code)
    demand_score = shift_demand_score(shift, target_sector_counts)

    # Keep generation cheap: rank shifts by how much requested demand they cover,
    # while subtracting a small load penalty so one popular shift does not absorb
    # every generated person. This replaces repeated full schedule simulations.
    return (demand_score - assigned_to_shift * 2, demand_score, -assigned_to_shift, shift_code)


def response_from_schedule(
    scheduled: ScheduledResult,
    request: CalculatorRequest,
    minimum_required_fl: int,
    notes: list[str],
    warnings: list[str],
    requested_sector_hours: int,
    solver_snapshot: SolverSnapshot | None = None,
) -> CalculatorResponse:
    shift_map = shift_map_for_request(request)
    display_sector_names = sector_display_names_for_max(request.settings.max_sectors_per_hour)
    person_capacity = {
        person.id: max_sector_hours_for_person(person, request, shift_map)
        for person in scheduled.people
    }
    response_people = [
        VirtualPerson(
            id=person.id,
            license=person.license,
            shift=display_shift_for_person(person),
            role=person.role,
            sector_hours=person.sector_hours,
            max_sector_hours=person_capacity[person.id],
            utilization_percent=round((person.sector_hours / person_capacity[person.id]) * 100)
            if person_capacity[person.id] > 0
            else 0,
            used_as_sector_controller=person.used_as_sector_controller,
            source=person.source,
        )
        for person in scheduled.people
    ]

    hourly_coverage: list[HourlyCoverage] = []
    for slot, workers in enumerate(scheduled.hourly_workers):
        assignments_by_sector = {sector.sector_name: sector for sector in scheduled.hourly_sectors[slot]}
        hourly_coverage.append(
            HourlyCoverage(
                hour=hour_label(slot),
                open_sectors=len(scheduled.hourly_sectors[slot]),
                workers=workers,
                sector_workers=[
                    SectorAssignment(
                        sector_name=sector_name,
                        lower_worker=sector.lower_worker,
                        upper_worker=sector.upper_worker,
                    )
                    if (sector := assignments_by_sector.get(sector_name)) is not None
                    else None
                    for sector_name in display_sector_names
                ],
            )
        )

    unused_people = len([person for person in scheduled.people if person.sector_hours == 0])
    planned_people = len(scheduled.people)
    active_people = planned_people - unused_people
    scheduled_person_hours = sum(person.sector_hours for person in scheduled.people)
    total_person_capacity_hours = sum(person_capacity.values())
    utilization_percent = (
        round((scheduled_person_hours / total_person_capacity_hours) * 100)
        if total_person_capacity_hours > 0
        else 0
    )
    missing_sector_hours = max(0, requested_sector_hours - scheduled.total_hours)
    baseline_min_people, baseline_min_people_formula = baseline_min_people_for_profile(
        target_sector_counts_for_request(request)
    )

    return CalculatorResponse(
        feasible=missing_sector_hours == 0,
        max_sector_hours=scheduled.total_hours,
        requested_sector_hours=requested_sector_hours,
        solver_upper_bound_sector_hours=solver_snapshot.best_bound_sector_hours if solver_snapshot else None,
        solver_gap_to_upper_bound=solver_snapshot.sector_gap_to_best_bound if solver_snapshot else None,
        solver_status=solver_snapshot.status if solver_snapshot else None,
        solver_solution_count=solver_snapshot.solution_count if solver_snapshot else 0,
        solver_optimality_gap_percent=solver_snapshot.optimality_gap_percent if solver_snapshot else None,
        solver_stop_reason=solver_snapshot.stop_reason if solver_snapshot else None,
        missing_sector_hours=missing_sector_hours,
        baseline_min_people=baseline_min_people,
        baseline_min_people_formula=baseline_min_people_formula,
        minimum_required_fl=minimum_required_fl,
        planned_people=planned_people,
        active_people=active_people,
        unused_people=unused_people,
        scheduled_person_hours=scheduled_person_hours,
        total_person_capacity_hours=total_person_capacity_hours,
        utilization_percent=utilization_percent,
        people=response_people,
        shift_summary=summarize_shifts(scheduled.people),
        hourly_coverage=hourly_coverage,
        notes=notes,
        warnings=warnings,
    )


def choose_generated_person(
    next_id: int,
    license_name: str,
    people: list[PersonState],
    allowed_shifts: list[str],
    shift_map: dict[str, ShiftRule],
    target_sector_counts: list[int],
    max_people_by_shift_and_license: dict[tuple[str, str], int],
) -> PersonState | None:
    def shift_has_capacity(shift_code: str) -> bool:
        max_people = max_people_by_shift_and_license.get((shift_code, license_name))
        if max_people is None:
            return True
        return sum(1 for person in people if person.shift == shift_code and person.license == license_name) < max_people

    eligible_shifts = [shift_code for shift_code in allowed_shifts if shift_has_capacity(shift_code)]
    if not eligible_shifts:
        return None

    best_shift = max(
        eligible_shifts,
        key=lambda shift_code: generated_shift_key(shift_code, shift_map, target_sector_counts, people),
    )
    return PersonState(id=label_for_person(next_id), license=license_name, shift=best_shift)


def max_required_workers_per_hour(target_sector_counts: list[int]) -> int:
    return max((len(sector_names_for_count(count)) * 2 for count in target_sector_counts), default=0)


def minimum_people_lower_bound(
    candidates: list[PersonState],
    required_indexes: set[int],
    request: CalculatorRequest,
    shift_map: dict[str, ShiftRule],
    target_sector_counts: list[int],
) -> int:
    requested_controller_hours = 2 * sum(target_sector_counts)
    max_person_capacity = max(
        (max_sector_hours_for_person(person, request, shift_map) for person in candidates),
        default=1,
    )
    capacity_bound = (requested_controller_hours + max_person_capacity - 1) // max_person_capacity
    return max(
        1,
        len(required_indexes),
        max_required_workers_per_hour(target_sector_counts),
        capacity_bound,
    )


def candidate_shift_codes(shifts: list[ShiftRule], target_sector_counts: list[int]) -> list[str]:
    shifts = enabled_shift_rules(shifts)
    demanded = [
        shift.code
        for shift in shifts
        if shift_demand_score(shift, target_sector_counts) > 0
    ]
    return demanded or [shift.code for shift in shifts]


def add_optional_candidates(
    people: list[PersonState],
    next_id: int,
    license_name: str,
    per_shift_count: int,
    shifts: list[ShiftRule],
    target_sector_counts: list[int],
    max_people_by_shift: dict[str, int] | None = None,
) -> tuple[list[PersonState], int]:
    if per_shift_count <= 0:
        return people, next_id

    candidates = list(people)
    allowed_shift_codes = candidate_shift_codes(shifts, target_sector_counts)
    for shift_code in allowed_shift_codes:
        shift_cap = max_people_by_shift.get(shift_code) if max_people_by_shift is not None else None
        for _ in range(per_shift_count):
            if shift_cap is not None and sum(1 for person in candidates if person.shift == shift_code) >= shift_cap:
                break
            candidates.append(PersonState(id=label_for_person(next_id), license=license_name, shift=shift_code))
            next_id += 1
    return candidates, next_id


def compact_candidate_pool_for_people_limit(
    candidates: list[PersonState],
    required_indexes: set[int],
    request: CalculatorRequest,
    shift_map: dict[str, ShiftRule],
    target_sector_counts: list[int],
    people_limit: int,
    license_target_counts: dict[str, int] | None = None,
) -> tuple[list[PersonState], set[int]]:
    required_original_indexes = sorted(required_indexes)
    if people_limit <= 0 or len(candidates) <= max(people_limit * 3, people_limit + 20):
        return candidates, set(required_indexes)

    selected_original_indexes: list[int] = list(required_original_indexes)
    selected_original_set = set(selected_original_indexes)
    selected_license_counts = Counter(candidates[index].license for index in selected_original_indexes)
    target_pool_size = min(len(candidates), max(people_limit * 3, people_limit + 24))
    target_total = sum(max(0, count) for count in (license_target_counts or {}).values())

    def candidate_score(index: int) -> tuple[int, int, int, int, str]:
        person = candidates[index]
        source_priority = 0 if person.source == REGULAR_SOURCE else 1 if person.source == WHAT_IF_SOURCE else 2
        shift = shift_map[person.shift]
        return (
            source_priority,
            -shift_demand_score(shift, target_sector_counts),
            -max_sector_hours_for_person(person, request, shift_map),
            role_penalty(person),
            person.id,
        )

    def license_need(license_name: str) -> int:
        if target_total <= 0 or not license_target_counts:
            return 0
        return (
            max(0, license_target_counts.get(license_name, 0)) * people_limit
            - target_total * selected_license_counts[license_name]
        )

    available_by_license: dict[str, list[int]] = {}
    for license_name in ("FL", "APS", "ACS"):
        available_by_license[license_name] = sorted(
            (
                index
                for index, person in enumerate(candidates)
                if index not in selected_original_set and person.license == license_name
            ),
            key=candidate_score,
        )

    while len(selected_original_indexes) < min(people_limit, len(candidates)):
        non_empty_licenses = [
            license_name
            for license_name, indexes in available_by_license.items()
            if indexes
        ]
        if not non_empty_licenses:
            break
        if target_total > 0:
            license_name = max(
                non_empty_licenses,
                key=lambda item: (license_need(item), -len(available_by_license[item])),
            )
        else:
            license_name = min(
                non_empty_licenses,
                key=lambda item: candidate_score(available_by_license[item][0]),
            )
        selected_index = available_by_license[license_name].pop(0)
        selected_original_indexes.append(selected_index)
        selected_original_set.add(selected_index)
        selected_license_counts[license_name] += 1

    backup_candidates = sorted(
        (
            index
            for index in range(len(candidates))
            if index not in selected_original_set
        ),
        key=lambda index: (
            -max(0, license_need(candidates[index].license)),
            *candidate_score(index),
        ),
    )
    for index in backup_candidates:
        if len(selected_original_indexes) >= target_pool_size:
            break
        selected_original_indexes.append(index)
        selected_original_set.add(index)

    compact_candidates = [candidates[index] for index in selected_original_indexes]
    compact_required_indexes = set(range(len(required_original_indexes)))
    return compact_candidates, compact_required_indexes


def configuration_library_paths() -> list[Path]:
    paths: list[Path] = []
    configured = os.environ.get(CONFIG_LIBRARY_ENV)
    if configured:
        paths.append(Path(configured))
    paths.extend(DEFAULT_CONFIG_LIBRARY_PATHS)
    return paths


def configuration_model_max_hours(rows: list[list[str]]) -> dict[int, int]:
    for row in rows:
        if row and row[0].strip() == "MODEL_MAX_SH":
            model_max_by_column: dict[int, int] = {}
            for column_index, value in enumerate(row):
                if column_index == 0 or not value.strip():
                    continue
                try:
                    model_max_by_column[column_index] = int(float(value.replace(",", ".")))
                except ValueError:
                    continue
            return model_max_by_column
    return {}


@lru_cache(maxsize=512)
def configuration_excel_sector_profile(name: str) -> tuple[int, tuple[int, ...]] | None:
    try:
        from .config_library import _manual_requested_sector_counts, manual_schedule_for_configuration
    except ImportError:
        return None

    counts = _manual_requested_sector_counts(manual_schedule_for_configuration(name))
    if counts is None:
        return None
    return sum(counts), tuple(counts)


def candidate_pool_from_configuration(
    candidates: list[PersonState],
    required_indexes: set[int],
    configuration: object,
    allowed_regular_shift_codes: set[str] | None = None,
    allowed_officer_shift_codes: set[str] | None = None,
) -> tuple[list[PersonState], set[int]]:
    required_people = [candidates[index] for index in sorted(required_indexes)]

    def seed_shift_is_allowed(shift: str, source: str) -> bool:
        if source == OFFICER_SOURCE:
            return allowed_officer_shift_codes is None or shift in allowed_officer_shift_codes
        return allowed_regular_shift_codes is None or shift in allowed_regular_shift_codes

    def required_match_key(license_name: str, shift: str, role: str | None) -> tuple[str, str, str | None]:
        if role == "V3" and shift == "A21":
            return license_name, "V3", role
        return license_name, shift, role

    required_counts = Counter(required_match_key(person.license, person.shift, person.role) for person in required_people)
    seed_people = list(required_people)
    next_seed_index = 0

    def add_seed_people(count: int, license_name: str, shift: str, role: str | None, source: str) -> None:
        nonlocal next_seed_index
        if not seed_shift_is_allowed(shift, source):
            return
        for _ in range(max(0, count)):
            seed_people.append(
                PersonState(
                    id=f"seed-{next_seed_index}",
                    license=license_name,
                    shift=shift,
                    role=role,
                    source=source,
                )
            )
            next_seed_index += 1

    for item in getattr(configuration, "fixed_staff", []):
        if not seed_shift_is_allowed(item.shift, REGULAR_SOURCE):
            continue
        key = required_match_key(item.license, item.shift, item.role)
        covered_by_required = min(required_counts[key], item.count)
        required_counts[key] -= covered_by_required
        add_seed_people(item.count - covered_by_required, item.license, item.shift, item.role, REGULAR_SOURCE)

    for item in getattr(configuration, "officer_staff", []):
        if not seed_shift_is_allowed(item.shift, OFFICER_SOURCE):
            continue
        add_seed_people(item.count, item.license, item.shift, None, OFFICER_SOURCE)

    return seed_people, set(range(len(required_people)))


def preferred_user_seed_option(
    preferred_configuration_id: str,
    requested_sector_hours: int,
    target_sector_counts: list[int] | None,
) -> tuple[int, int, int, int, int, int, int, str, object] | None:
    if not preferred_configuration_id.startswith("user:"):
        return None
    try:
        from .config_library import (
            _manual_requested_sector_counts,
            _manual_schedule_max_sector_hours,
            user_configuration_detail,
        )

        detail = user_configuration_detail(preferred_configuration_id)
    except Exception:
        return None

    try:
        fixed_staff = [
            FixedStaffRule.model_validate(item)
            for item in detail.get("fixed_staff", [])
            if isinstance(item, dict)
        ]
        officer_staff = [
            OfficerStaffRule.model_validate(item)
            for item in detail.get("officer_staff", [])
            if isinstance(item, dict)
        ]
    except Exception:
        return None

    license_counts_raw = detail.get("license_counts")
    license_counts = {
        license_name: int(license_counts_raw.get(license_name, 0)) if isinstance(license_counts_raw, dict) else 0
        for license_name in ("FL", "APS", "ACS")
    }
    configuration = SeedConfiguration(
        name=str(detail.get("name") or preferred_configuration_id),
        fixed_staff=fixed_staff,
        officer_staff=officer_staff,
        license_counts=license_counts,
        total_without_waiting=int(detail.get("total_without_waiting") or sum(license_counts.values())),
        waiting_count=int(detail.get("waiting_count") or 0),
        unsupported_rows=[],
    )
    if configuration.parsed_total <= 0:
        return None

    manual_schedule = detail.get("manual_schedule")
    manual_schedule_dict = manual_schedule if isinstance(manual_schedule, dict) else None
    manual_counts = _manual_requested_sector_counts(manual_schedule_dict)
    seed_sector_hours = _manual_schedule_max_sector_hours(manual_schedule_dict)
    if seed_sector_hours is None:
        model_hours = detail.get("model_max_sector_hours")
        try:
            seed_sector_hours = int(float(str(model_hours).replace(",", ".")))
        except (TypeError, ValueError):
            seed_sector_hours = requested_sector_hours

    if manual_counts is not None and target_sector_counts is not None:
        profile_distance = sum(
            abs(target_sector_counts[slot] - manual_counts[slot])
            for slot in range(min(len(target_sector_counts), len(manual_counts)))
        )
    else:
        profile_distance = abs(seed_sector_hours - requested_sector_hours)

    return (
        0,
        0,
        max(0, requested_sector_hours - seed_sector_hours),
        profile_distance,
        abs(seed_sector_hours - requested_sector_hours),
        configuration.parsed_total,
        seed_sector_hours,
        configuration.name,
        configuration,
    )


def configuration_seed_candidate_pools(
    candidates: list[PersonState],
    required_indexes: set[int],
    request: CalculatorRequest,
    people_limit: int,
    requested_sector_hours: int,
    target_sector_counts: list[int] | None = None,
    limit: int = 3,
) -> list[tuple[str, list[PersonState], set[int]]]:
    try:
        from .config_library import configuration_columns, parse_configuration, read_configuration_csv
    except ImportError:
        return []

    supported_shifts = {shift.code for shift in request.settings.shifts}
    allowed_regular_shift_codes = {shift.code for shift in enabled_shift_rules(request.settings.shifts)}
    allowed_officer_shift_codes = {shift.code for shift in enabled_shift_rules(request.settings.officer_shifts)}
    preferred_configuration_id = (request.preferred_manual_configuration_id or "").strip()
    seed_options: list[tuple[int, int, int, int, int, int, int, str, object]] = []
    preferred_user_seed = preferred_user_seed_option(preferred_configuration_id, requested_sector_hours, target_sector_counts)
    if preferred_user_seed is not None:
        seed_options.append(preferred_user_seed)
    for path in configuration_library_paths():
        if not path.exists():
            continue
        try:
            rows = read_configuration_csv(path)
        except OSError:
            continue
        model_max_by_column = configuration_model_max_hours(rows)
        for column_index, name in configuration_columns(rows):
            model_max = model_max_by_column.get(column_index)
            excel_profile = configuration_excel_sector_profile(name)
            excel_sector_hours = excel_profile[0] if excel_profile is not None else None
            seed_sector_hours = excel_sector_hours if excel_sector_hours is not None else model_max
            is_preferred_seed = preferred_configuration_id in {str(column_index), name}
            if seed_sector_hours is None:
                continue
            if not is_preferred_seed and seed_sector_hours < requested_sector_hours - 2:
                continue
            configuration = parse_configuration(rows, column_index, name, supported_shifts)
            if configuration.unsupported_rows or configuration.parsed_total <= 0:
                continue
            if not is_preferred_seed and configuration.parsed_total > people_limit + 6:
                continue
            if excel_profile is not None and target_sector_counts is not None:
                profile_counts = excel_profile[1]
                profile_distance = sum(
                    abs(target_sector_counts[slot] - profile_counts[slot])
                    for slot in range(min(len(target_sector_counts), len(profile_counts)))
                )
            else:
                profile_distance = abs(seed_sector_hours - requested_sector_hours)
            seed_options.append(
                (
                    0 if is_preferred_seed else 1,
                    abs(configuration.parsed_total - people_limit),
                    max(0, requested_sector_hours - seed_sector_hours),
                    profile_distance,
                    abs(seed_sector_hours - requested_sector_hours),
                    configuration.parsed_total,
                    seed_sector_hours,
                    name,
                    configuration,
                )
            )
        if seed_options:
            break

    pools: list[tuple[str, list[PersonState], set[int]]] = []
    for _, _, _, _, _, _, seed_sector_hours, name, configuration in sorted(seed_options)[:limit]:
        seed_candidates, seed_required_indexes = candidate_pool_from_configuration(
            candidates,
            required_indexes,
            configuration,
            allowed_regular_shift_codes,
            allowed_officer_shift_codes,
        )
        pools.append(
            (
                f"{name} ({configuration.parsed_total} ljudi, seed SH {seed_sector_hours})",
                seed_candidates,
                seed_required_indexes,
            )
        )
    return pools


def seed_pool_with_backups(
    seed_candidates: list[PersonState],
    compact_candidates: list[PersonState],
    people_limit: int,
) -> list[PersonState]:
    target_size = max(people_limit * 3, people_limit + 24)
    combined = list(seed_candidates)
    existing_ids = {person.id for person in combined}
    backup_index = 0
    for person in compact_candidates:
        if len(combined) >= target_size:
            break
        if person.id in existing_ids:
            continue
        combined.append(replace(person, id=f"seed-backup-{backup_index}"))
        backup_index += 1
    return combined


def selected_people_by_license(people: list[PersonState]) -> Counter[str]:
    return Counter(person.license for person in people)


def fixed_shift_total_caps(request: CalculatorRequest) -> dict[str, int]:
    caps: Counter[str] = Counter()
    active_shift_codes = enabled_regular_shift_codes(request)
    for item in request.fixed_staff:
        if item.shift not in active_shift_codes:
            continue
        caps[item.shift] += item.count
    return dict(caps)


def fixed_shift_cap_warnings(people: list[PersonState], shift_caps: dict[str, int]) -> list[str]:
    warnings: list[str] = []
    people_by_shift = Counter(person.shift for person in people)
    for shift_code, cap in sorted(shift_caps.items()):
        if people_by_shift[shift_code] > cap:
            warnings.append(
                f"Fiksni vnos omejuje {shift_code} na {cap}, obvezna/fiksna pravila pa zahtevajo "
                f"{people_by_shift[shift_code]} ljudi v tej izmeni."
            )
    return warnings


def officer_counts_by_license(request: CalculatorRequest) -> Counter[str]:
    active_shift_codes = enabled_officer_shift_codes(request)
    return Counter({license_name: 0 for license_name in ("FL", "APS", "ACS")}) + Counter(
        {
            license_name: sum(
                item.count
                for item in request.officer_staff
                if item.license == license_name and item.shift in active_shift_codes
            )
            for license_name in ("FL", "APS", "ACS")
        }
    )


def office_pool_source_license_caps(request: CalculatorRequest) -> dict[tuple[str, str], int]:
    pool_counts = office_pool_counts_by_license(request)
    return {
        (OFFICE_POOL_SOURCE, license_name): count
        for license_name, count in pool_counts.items()
        if count > 0
    }


def license_caps_with_office_pool(
    base_caps: dict[str, int] | None,
    request: CalculatorRequest,
) -> dict[str, int] | None:
    if base_caps is None:
        return None
    pool_counts = office_pool_counts_by_license(request)
    return {
        license_name: base_caps.get(license_name, 0) + pool_counts[license_name]
        for license_name in ("FL", "APS", "ACS")
    }


def solution_uses_office_pool(scheduled: ScheduledResult) -> bool:
    return any(person.source == OFFICE_POOL_SOURCE and person.sector_hours > 0 for person in scheduled.people)


def solver_stopped_on_time_limit(solved: tuple[ScheduledResult, SolverSnapshot] | None) -> bool:
    if solved is None:
        return False
    stop_reason = solved[1].stop_reason or ""
    return "časovni limit CP-SAT faze" in stop_reason


def office_fallback_should_run(
    request: CalculatorRequest,
    solved: tuple[ScheduledResult, SolverSnapshot] | None,
    requested_sector_hours: int,
) -> bool:
    if total_office_pool_count(request) <= 0 or request.office_fallback_mode == "disabled":
        return False
    if request.office_fallback_mode == "force":
        return True
    if solved is not None and solved[0].total_hours >= requested_sector_hours:
        return False
    return not solver_stopped_on_time_limit(solved)


def recommended_office_shift_summary(people: list[PersonState]) -> str | None:
    active_pool = [
        person
        for person in people
        if person.source == OFFICE_POOL_SOURCE and person.sector_hours > 0
    ]
    if not active_pool:
        return None

    counts = Counter((person.shift, person.license) for person in active_pool)
    parts = [
        f"{count}× {license_name} {shift}"
        for (shift, license_name), count in sorted(counts.items())
    ]
    return "Priporočene operativne office izmene: " + ", ".join(parts) + "."


def solve_schedule_with_cp_sat(
    candidates: list[PersonState],
    required_indexes: set[int],
    request: CalculatorRequest,
    shift_map: dict[str, ShiftRule],
    target_sector_counts: list[int],
    license_caps: dict[str, int] | None = None,
    selected_total_cap: int | None = None,
    shift_license_caps: dict[tuple[str, str], int] | None = None,
    shift_total_caps: dict[str, int] | None = None,
    source_license_caps: dict[tuple[str, str], int] | None = None,
    non_pool_selected_cap: int | None = None,
    license_target_counts: dict[str, int] | None = None,
    minimum_covered_sector_hours: int | None = None,
    stop_after_coverage_target: bool = False,
    solution_callback: ScheduleSolutionCallback | None = None,
    cancel_callback: CancelCallback | None = None,
) -> tuple[ScheduledResult, SolverSnapshot] | None:
    model = cp_model.CpModel()
    max_consecutive = request.settings.max_consecutive_work_hours
    rest_after_max = request.settings.rest_after_max_consecutive_hours

    selected = {
        person_index: model.NewBoolVar(f"selected_{person_index}")
        for person_index in range(len(candidates))
    }
    for person_index in required_indexes:
        model.Add(selected[person_index] == 1)

    if selected_total_cap is not None:
        model.Add(sum(selected.values()) <= selected_total_cap)

    if non_pool_selected_cap is not None:
        model.Add(
            sum(
                selected[index]
                for index, person in enumerate(candidates)
                if person.source != OFFICE_POOL_SOURCE
            )
            <= non_pool_selected_cap
        )

    if license_caps is not None:
        for license_name, cap in license_caps.items():
            model.Add(
                sum(
                    selected[index]
                    for index, person in enumerate(candidates)
                    if person.license == license_name
                )
                <= cap
            )

    if shift_license_caps is not None:
        for (shift_code, license_name), cap in shift_license_caps.items():
            model.Add(
                sum(
                    selected[index]
                    for index, person in enumerate(candidates)
                    if person.shift == shift_code and person.license == license_name
                )
                <= cap
            )

    if shift_total_caps is not None:
        for shift_code, cap in shift_total_caps.items():
            model.Add(
                sum(
                    selected[index]
                    for index, person in enumerate(candidates)
                    if person.shift == shift_code
                )
                <= cap
            )

    if source_license_caps is not None:
        for (source, license_name), cap in source_license_caps.items():
            model.Add(
                sum(
                    selected[index]
                    for index, person in enumerate(candidates)
                    if person.source == source and person.license == license_name
                )
                <= cap
            )

    equivalent_candidate_groups: dict[tuple[str, str, str | None, str], list[int]] = defaultdict(list)
    for index, person in enumerate(candidates):
        if index not in required_indexes:
            equivalent_candidate_groups[(person.license, person.shift, person.role, person.source)].append(index)
    for group_indexes in equivalent_candidate_groups.values():
        for previous_index, next_index in zip(group_indexes, group_indexes[1:]):
            model.Add(selected[previous_index] >= selected[next_index])

    available_slots = {
        index: shift_slots(shift_map[person.shift])
        for index, person in enumerate(candidates)
    }
    capacity_by_candidate = {
        index: max_sector_hours_for_person(person, request, shift_map)
        for index, person in enumerate(candidates)
    }

    work: dict[tuple[int, int], cp_model.IntVar] = {}
    for index, slots in available_slots.items():
        for slot in slots:
            current_work = model.NewBoolVar(f"work_{index}_{slot}")
            work[(index, slot)] = current_work
            model.Add(current_work <= selected[index])

    assignments_by_person_slot: dict[tuple[int, int], list[cp_model.IntVar]] = defaultdict(list)
    seat_assignments: dict[tuple[int, int, int], list[tuple[int, cp_model.IntVar]]] = defaultdict(list)
    covered_sectors: dict[tuple[int, int, str], cp_model.IntVar] = {}
    sector_names_by_slot: dict[int, list[str]] = {}
    profile_selection_by_slot: dict[int, list[tuple[tuple[str, ...], cp_model.IntVar]]] = {}
    assignment_penalty_terms: list[cp_model.LinearExpr] = []
    profile_choice_penalty_terms: list[cp_model.LinearExpr] = []
    max_single_assignment_penalty = 0
    profile_choice_penalty_ceiling = 0
    requested_sector_hours = sum(target_sector_counts)

    for slot, target_count in enumerate(target_sector_counts):
        profile_options = sector_profile_options_for_count(target_count)
        sector_names = sector_names_for_profile_options(profile_options)
        sector_names_by_slot[slot] = sector_names

        if len(profile_options) > 1:
            selected_profiles: list[tuple[tuple[str, ...], cp_model.IntVar]] = []
            for profile_index, profile in enumerate(profile_options):
                selected_profile = model.NewBoolVar(
                    f"profile_{slot}_{profile_index}_{'_'.join(profile).replace(' ', '_')}"
                )
                selected_profiles.append((profile, selected_profile))
                penalty = sector_profile_choice_penalty(slot, target_count, profile, requested_sector_hours)
                if penalty:
                    profile_choice_penalty_terms.append(penalty * selected_profile)
            model.AddExactlyOne([selected_profile for _profile, selected_profile in selected_profiles])
            profile_selection_by_slot[slot] = selected_profiles
        profile_choice_penalty_ceiling += max(
            (
                sector_profile_choice_penalty(slot, target_count, profile, requested_sector_hours)
                for profile in profile_options
            ),
            default=0,
        )

        for sector_position, sector_name in enumerate(sector_names):
            cover = model.NewBoolVar(f"cover_{slot}_{sector_position}_{sector_name.replace(' ', '_')}")
            covered_sectors[(slot, sector_position, sector_name)] = cover
            selected_profiles_with_sector = [
                selected_profile
                for profile, selected_profile in profile_selection_by_slot.get(slot, [])
                if sector_name in profile
            ]
            if selected_profiles_with_sector:
                model.Add(cover <= sum(selected_profiles_with_sector))

            for seat in range(2):
                possible_seat_assignments: list[cp_model.IntVar] = []
                for index, person in enumerate(candidates):
                    if (
                        slot not in available_slots[index]
                        or not role_allows_sector_slot(person, slot, shift_map)
                        or not can_fill_sector(person, sector_name)
                    ):
                        continue
                    assignment = model.NewBoolVar(
                        f"x_{index}_{slot}_{sector_position}_{seat}_{sector_name.replace(' ', '_')}"
                    )
                    possible_seat_assignments.append(assignment)
                    assignments_by_person_slot[(index, slot)].append(assignment)
                    seat_assignments[(slot, sector_position, seat)].append((index, assignment))
                    model.Add(assignment <= selected[index])
                    assignment_penalty = (
                        role_penalty(person)
                        + role_edge_exception_penalty(person, slot, shift_map)
                        + sector_license_preference(person, sector_name)
                        + (OFFICER_WORK_PENALTY if is_officer(person) else 0)
                        + officer_slot_edge_penalty(person, slot, shift_map)
                    )
                    max_single_assignment_penalty = max(max_single_assignment_penalty, assignment_penalty)
                    if assignment_penalty:
                        assignment_penalty_terms.append(assignment_penalty * assignment)

                model.Add(sum(possible_seat_assignments) == cover)

    for (index, slot), current_work in work.items():
        model.Add(sum(assignments_by_person_slot.get((index, slot), [])) == current_work)

    window_size = max_consecutive + rest_after_max
    for index in range(len(candidates)):
        for window_start in range(0, HOURS_IN_DAY - window_size + 1):
            model.Add(
                sum(work.get((index, slot), 0) for slot in range(window_start, window_start + window_size))
                <= max_consecutive
            )
        model.Add(
            sum(work.get((index, slot), 0) for slot in available_slots[index])
            <= capacity_by_candidate[index]
        )

    fmp_leader_overlap_penalty_terms: list[cp_model.LinearExpr] = []
    fmp_leader_overlap_penalty_ceiling = 0
    for first_index, first_person in enumerate(candidates):
        for second_index in range(first_index + 1, len(candidates)):
            second_person = candidates[second_index]
            penalty = fmp_leader_overlap_penalty(first_person, second_person)
            if penalty <= 0:
                continue
            shared_slots = sorted(set(available_slots[first_index]).intersection(available_slots[second_index]))
            for slot in shared_slots:
                first_work = work.get((first_index, slot))
                second_work = work.get((second_index, slot))
                if first_work is None or second_work is None:
                    continue
                overlap = model.NewBoolVar(f"fmp_leader_overlap_{first_index}_{second_index}_{slot}")
                model.Add(overlap >= first_work + second_work - 1)
                fmp_leader_overlap_penalty_terms.append(penalty * overlap)
                fmp_leader_overlap_penalty_ceiling += penalty

    covered_sector_count = sum(covered_sectors.values())
    if minimum_covered_sector_hours is not None:
        model.Add(covered_sector_count >= min(requested_sector_hours, minimum_covered_sector_hours))
    selected_count = sum(selected.values())
    selected_capacity = sum(capacity_by_candidate[index] * selected[index] for index in range(len(candidates)))
    officer_candidate_count = sum(1 for person in candidates if is_officer(person))
    officer_selected_count = sum(
        selected[index]
        for index, person in enumerate(candidates)
        if is_officer(person)
    )
    fl_selected_count = sum(
        selected[index]
        for index, person in enumerate(candidates)
        if person.license == "FL"
    )
    license_mix_penalty_terms: list[cp_model.LinearExpr] = []
    if license_target_counts:
        target_total = sum(max(0, target_count) for target_count in license_target_counts.values())
        for license_name, target_count in license_target_counts.items():
            if target_total <= 0:
                continue
            license_selected_count = sum(
                selected[index]
                for index, person in enumerate(candidates)
                if person.license == license_name
            )
            deviation = model.NewIntVar(
                0,
                max(1, len(candidates) * target_total * 2),
                f"license_deviation_{license_name}",
            )
            model.AddAbsEquality(
                deviation,
                target_total * license_selected_count - max(0, target_count) * selected_count,
            )
            license_mix_penalty_terms.append(deviation * LICENSE_MIX_DEVIATION_PENALTY)

    assignment_penalty = sum(assignment_penalty_terms) if assignment_penalty_terms else 0
    profile_choice_penalty = sum(profile_choice_penalty_terms) if profile_choice_penalty_terms else 0
    license_mix_penalty = sum(license_mix_penalty_terms) if license_mix_penalty_terms else 0
    fmp_leader_overlap_penalty_value = (
        sum(fmp_leader_overlap_penalty_terms)
        if fmp_leader_overlap_penalty_terms
        else 0
    )
    fl_preference_penalty = fl_selected_count * FL_SELECTED_PENALTY if request.prefer_minimal_fl else 0
    lower_order_penalty_ceiling = (
        officer_candidate_count * OFFICER_SELECTED_PENALTY
        + len(candidates) * SELECTED_PERSON_PENALTY
        + sum(capacity_by_candidate.values()) * SELECTED_CAPACITY_PENALTY
        + requested_sector_hours * 2 * max_single_assignment_penalty
        + len(candidates)
        * max(1, sum(max(0, count) for count in (license_target_counts or {}).values()))
        * 2
        * len(("FL", "APS", "ACS"))
        * LICENSE_MIX_DEVIATION_PENALTY
        + len(candidates) * FL_SELECTED_PENALTY
        + profile_choice_penalty_ceiling
        + fmp_leader_overlap_penalty_ceiling
    )
    model.Maximize(
        covered_sector_count * COVERED_SECTOR_WEIGHT
        - officer_selected_count * OFFICER_SELECTED_PENALTY
        - selected_count * SELECTED_PERSON_PENALTY
        - selected_capacity * SELECTED_CAPACITY_PENALTY
        - license_mix_penalty
        - fl_preference_penalty
        - profile_choice_penalty
        - fmp_leader_overlap_penalty_value
        - assignment_penalty
    )

    def best_bound_sector_hours(best_objective_bound: float | None) -> int | None:
        if best_objective_bound is None:
            return None
        upper_bound = int((best_objective_bound + lower_order_penalty_ceiling) // COVERED_SECTOR_WEIGHT)
        return min(requested_sector_hours, max(0, upper_bound))

    def sector_gap_to_bound(sector_hours: int, best_objective_bound: float | None) -> tuple[int | None, int | None]:
        sector_bound = best_bound_sector_hours(best_objective_bound)
        if sector_bound is None:
            return None, None
        return sector_bound, max(0, sector_bound - sector_hours)

    def auto_stop_allowed(sector_hours: int) -> bool:
        return (
            requested_sector_hours <= 0
            or coverage_percent(sector_hours, requested_sector_hours)
            >= request.settings.cp_sat_min_auto_stop_coverage_percent
        )

    runtime_lock = Lock()
    best_sector_hours = 0
    best_sector_bound: int | None = None
    best_sector_gap: int | None = None
    last_sector_improvement_at = monotonic()
    policy_stop_reason: str | None = None

    def update_best_sector_hours(sector_hours: int, sector_bound: int | None, sector_gap: int | None) -> None:
        nonlocal best_sector_bound, best_sector_gap, best_sector_hours, last_sector_improvement_at
        with runtime_lock:
            best_sector_bound = sector_bound
            best_sector_gap = sector_gap
            if sector_hours > best_sector_hours:
                best_sector_hours = sector_hours
                last_sector_improvement_at = monotonic()

    def request_policy_stop(reason: str) -> bool:
        nonlocal policy_stop_reason
        with runtime_lock:
            if policy_stop_reason is None:
                policy_stop_reason = reason
                return True
            return False

    def no_improvement_stop_reason() -> str | None:
        no_improvement_seconds = request.settings.cp_sat_no_improvement_seconds
        if no_improvement_seconds <= 0:
            return None
        with runtime_lock:
            sector_hours = best_sector_hours
            sector_bound = best_sector_bound
            sector_gap = best_sector_gap
            idle_seconds = monotonic() - last_sector_improvement_at
        if sector_hours <= 0 or not auto_stop_allowed(sector_hours) or idle_seconds < no_improvement_seconds:
            return None
        if sector_gap is None or sector_gap > request.settings.cp_sat_acceptable_sector_gap:
            return None
        bound_text = f", meja največ {sector_bound}, razlika {sector_gap}" if sector_gap is not None else ""
        return (
            f"brez izboljšanja pokritosti {round(idle_seconds)} s; "
            f"najboljša rešitev pokriva {sector_hours}/{requested_sector_hours} sektorskih ur"
            f"{bound_text}"
        )

    def scheduled_from_solution(bool_value: Callable[[cp_model.IntVar], bool]) -> ScheduledResult:
        selected_indexes = [index for index in range(len(candidates)) if bool_value(selected[index])]
        id_remap: dict[str, str] = {}
        used_display_ids: set[str] = set()
        next_display_index = 0
        for index in selected_indexes:
            person = candidates[index]
            requested_display_id = role_display_id(person.role) or person.preferred_id
            if requested_display_id and requested_display_id not in used_display_ids:
                display_id = requested_display_id
            else:
                while label_for_person(next_display_index) in used_display_ids:
                    next_display_index += 1
                display_id = label_for_person(next_display_index)
                next_display_index += 1
            id_remap[person.id] = display_id
            used_display_ids.add(display_id)
        sector_hours_by_index: Counter[int] = Counter()
        hourly_sectors: list[list[ScheduledSector]] = []

        for slot, _target_count in enumerate(target_sector_counts):
            scheduled_sectors: list[ScheduledSector] = []
            for sector_position, sector_name in enumerate(sector_names_by_slot.get(slot, [])):
                cover = covered_sectors[(slot, sector_position, sector_name)]
                if not bool_value(cover):
                    continue

                worker_indexes: list[int] = []
                for seat in range(2):
                    matching_workers = [
                        index
                        for index, assignment in seat_assignments[(slot, sector_position, seat)]
                        if bool_value(assignment)
                    ]
                    if not matching_workers:
                        break
                    worker_indexes.append(matching_workers[0])

                if len(worker_indexes) != 2:
                    continue

                for worker_index in worker_indexes:
                    sector_hours_by_index[worker_index] += 1

                scheduled_sectors.append(
                    ScheduledSector(
                        sector_name=sector_name,
                        lower_worker=id_remap[candidates[worker_indexes[0]].id],
                        upper_worker=id_remap[candidates[worker_indexes[1]].id],
                    )
                )
            hourly_sectors.append(scheduled_sectors)

        selected_people = [
            replace(
                candidates[index],
                id=id_remap[candidates[index].id],
                sector_hours=sector_hours_by_index[index],
                used_as_sector_controller=sector_hours_by_index[index] > 0,
            )
            for index in selected_indexes
        ]
        hourly_sectors = smooth_sector_rotations(hourly_sectors, selected_people)

        return ScheduledResult(
            people=selected_people,
            hourly_sectors=hourly_sectors,
            total_hours=sum(len(sectors) for sectors in hourly_sectors),
        )

    class BestSolutionCallback(cp_model.CpSolverSolutionCallback):
        def __init__(self) -> None:
            super().__init__()
            self.solution_count = 0

        def on_solution_callback(self) -> None:
            self.solution_count += 1
            scheduled_result = scheduled_from_solution(self.BooleanValue)
            sector_bound, sector_gap = sector_gap_to_bound(scheduled_result.total_hours, self.BestObjectiveBound())
            update_best_sector_hours(scheduled_result.total_hours, sector_bound, sector_gap)
            solution_stop_reason: str | None = None
            if (
                sector_gap is not None
                and sector_gap <= request.settings.cp_sat_acceptable_sector_gap
                and auto_stop_allowed(scheduled_result.total_hours)
                and scheduled_result.total_hours < requested_sector_hours
            ):
                solution_stop_reason = (
                    "dokazana meja je dovolj blizu: "
                    f"trenutno {scheduled_result.total_hours}/{requested_sector_hours}, "
                    f"meja največ {sector_bound}, razlika {sector_gap} sektorskih ur"
                )
                request_policy_stop(solution_stop_reason)
            solver_snapshot = SolverSnapshot(
                status="FEASIBLE",
                solution_count=self.solution_count,
                objective_value=self.ObjectiveValue(),
                best_objective_bound=self.BestObjectiveBound(),
                optimality_gap_percent=solver_gap_percent(self.ObjectiveValue(), self.BestObjectiveBound()),
                stop_reason=solution_stop_reason,
                best_bound_sector_hours=sector_bound,
                sector_gap_to_best_bound=sector_gap,
            )
            if solution_callback is not None:
                solution_callback(scheduled_result, solver_snapshot)
            if (
                stop_after_coverage_target
                and minimum_covered_sector_hours is not None
                and scheduled_result.total_hours >= min(requested_sector_hours, minimum_covered_sector_hours)
            ):
                request_policy_stop(
                    f"najdena izvedljiva rešitev za {scheduled_result.total_hours}/{requested_sector_hours} "
                    "zahtevanih sektorskih ur"
                )
                self.StopSearch()
            if solution_stop_reason is not None:
                self.StopSearch()
            if cancel_callback is not None and cancel_callback():
                self.StopSearch()

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = request.settings.cp_sat_time_limit_seconds
    solver.parameters.num_search_workers = CP_SAT_WORKERS
    solver.parameters.random_seed = 1
    callback = BestSolutionCallback()
    cancel_monitor_stop = Event()
    cancel_monitor: Thread | None = None

    if cancel_callback is not None or request.settings.cp_sat_no_improvement_seconds > 0:
        def watch_for_stop() -> None:
            while not cancel_monitor_stop.wait(0.25):
                if cancel_callback is not None and cancel_callback():
                    solver.StopSearch()
                    return
                stop_reason = no_improvement_stop_reason()
                if stop_reason is not None:
                    request_policy_stop(stop_reason)
                    solver.StopSearch()
                    return

        cancel_monitor = Thread(target=watch_for_stop, daemon=True)
        cancel_monitor.start()

    try:
        status = solver.Solve(model, callback)
    finally:
        cancel_monitor_stop.set()
        if cancel_monitor is not None:
            cancel_monitor.join(timeout=1)

    if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
        return None

    selected_people_result = scheduled_from_solution(solver.BooleanValue)
    final_sector_bound, final_sector_gap = sector_gap_to_bound(
        selected_people_result.total_hours,
        solver.BestObjectiveBound(),
    )
    with runtime_lock:
        final_stop_reason = policy_stop_reason
    if final_stop_reason is None and status == cp_model.FEASIBLE:
        final_stop_reason = (
            f"časovni limit CP-SAT faze ({request.settings.cp_sat_time_limit_seconds} s) se je iztekel; "
            "optimalnost še ni dokazana"
        )
    solver_snapshot = SolverSnapshot(
        status=solver.StatusName(status),
        solution_count=callback.solution_count,
        objective_value=solver.ObjectiveValue(),
        best_objective_bound=solver.BestObjectiveBound(),
        optimality_gap_percent=solver_gap_percent(solver.ObjectiveValue(), solver.BestObjectiveBound()),
        stop_reason=final_stop_reason,
        best_bound_sector_hours=final_sector_bound,
        sector_gap_to_best_bound=final_sector_gap,
    )

    return (
        selected_people_result,
        solver_snapshot,
    )


def calculate_pareto_points(
    candidates: list[PersonState],
    required_indexes: set[int],
    request: CalculatorRequest,
    shift_map: dict[str, ShiftRule],
    target_sector_counts: list[int],
    minimum_required_fl: int,
    requested_sector_hours: int,
    shift_total_caps: dict[str, int] | None = None,
    progress_callback: ProgressCallback | None = None,
    cancel_callback: CancelCallback | None = None,
) -> list[ParetoPoint]:
    if request.calculation_mode != "staff_to_coverage" or request.total_people <= 0:
        return []

    office_pool_license_counts = office_pool_counts_by_license(request)
    max_people_limit = request.total_people + sum(office_pool_license_counts.values())
    minimum_people = max(1, len(required_indexes))
    if minimum_people > max_people_limit:
        return []

    people_limits = list(range(minimum_people, max_people_limit + 1))
    point_time_limit = max(1, request.settings.cp_sat_time_limit_seconds // max(1, len(people_limits)))
    point_settings = request.settings.model_copy(
        update={
            "cp_sat_time_limit_seconds": min(request.settings.cp_sat_time_limit_seconds, point_time_limit),
            "cp_sat_no_improvement_seconds": min(
                request.settings.cp_sat_no_improvement_seconds,
                max(1, point_time_limit // 2),
            )
            if request.settings.cp_sat_no_improvement_seconds > 0
            else 0,
        }
    )
    point_request = request.model_copy(update={"settings": point_settings, "include_pareto": False})
    points: list[ParetoPoint] = []

    for position, people_limit in enumerate(people_limits, start=1):
        check_cancel(cancel_callback)
        report_progress(
            progress_callback,
            90 + round((position / max(1, len(people_limits))) * 8),
            f"Pareto analiza ljudi: {position}/{len(people_limits)} ({people_limit} ljudi).",
        )
        solved = solve_schedule_with_cp_sat(
            candidates,
            required_indexes,
            point_request,
            shift_map,
            target_sector_counts,
            license_caps={
                "FL": request.fl_count + office_pool_license_counts["FL"],
                "APS": request.aps_count + office_pool_license_counts["APS"],
                "ACS": request.acs_count + office_pool_license_counts["ACS"],
            },
            selected_total_cap=people_limit,
            non_pool_selected_cap=request.total_people,
            shift_license_caps=night_shift_license_caps(request),
            shift_total_caps=shift_total_caps,
            source_license_caps=office_pool_source_license_caps(request),
            cancel_callback=cancel_callback,
        )
        if solved is None:
            points.append(
                ParetoPoint(
                    people_limit=people_limit,
                    requested_sector_hours=requested_sector_hours,
                    coverage_percent=0 if requested_sector_hours > 0 else 100,
                    missing_sector_hours=requested_sector_hours,
                )
            )
            continue

        scheduled, solver_snapshot = solved
        response = response_from_schedule(
            scheduled,
            point_request,
            minimum_required_fl,
            [],
            [],
            requested_sector_hours,
            solver_snapshot,
        )
        points.append(
            ParetoPoint(
                people_limit=people_limit,
                planned_people=response.planned_people,
                active_people=response.active_people,
                max_sector_hours=response.max_sector_hours,
                requested_sector_hours=response.requested_sector_hours,
                coverage_percent=coverage_percent(response.max_sector_hours, response.requested_sector_hours),
                missing_sector_hours=response.missing_sector_hours,
                scheduled_person_hours=response.scheduled_person_hours,
                total_person_capacity_hours=response.total_person_capacity_hours,
                utilization_percent=response.utilization_percent,
                used_officers=sum(1 for person in scheduled.people if is_officer(person)),
                feasible=response.feasible,
                solver_status=solver_snapshot.status,
                solver_solution_count=solver_snapshot.solution_count,
                solver_optimality_gap_percent=solver_snapshot.optimality_gap_percent,
                solver_stop_reason=solver_snapshot.stop_reason,
            )
        )

    return points


def calculate_pareto(
    request: CalculatorRequest,
    progress_callback: ProgressCallback | None = None,
    cancel_callback: CancelCallback | None = None,
) -> ParetoResponse:
    report_progress(progress_callback, 5, "Pripravljam Pareto analizo.")
    check_cancel(cancel_callback)
    shift_map = shift_map_for_request(request)
    target_sector_counts = target_sector_counts_for_request(request)
    requested_sector_hours = sum(target_sector_counts)

    if request.calculation_mode != "staff_to_coverage":
        return ParetoResponse(
            requested_sector_hours=requested_sector_hours,
            warnings=["Pareto analiza ljudi je na voljo v načinu Iz ljudi."],
        )

    notes: list[str] = []
    warnings: list[str] = []
    fixed_people, next_id, fixed_notes = add_fixed_staff_people(request, [], 0)
    locked_people, next_id, locked_notes = add_locked_staff_people(request, fixed_people, next_id)
    seed_people, next_id, mandatory_notes, mandatory_warnings = create_mandatory_people(request, locked_people, next_id)
    notes.extend(fixed_notes)
    notes.extend(locked_notes)
    notes.extend(mandatory_notes)
    warnings.extend(mandatory_warnings)
    shift_caps = fixed_shift_total_caps(request)
    warnings.extend(fixed_shift_cap_warnings(seed_people, shift_caps))

    reserved_counts = Counter(person.license for person in seed_people)
    officer_license_counts = officer_counts_by_license(request)
    office_pool_license_counts = office_pool_counts_by_license(request)
    minimum_required_fl = reserved_counts["FL"]
    insufficient_reserved_licenses = [
        license_name
        for license_name, requested_count in (
            ("FL", request.fl_count),
            ("APS", request.aps_count),
            ("ACS", request.acs_count),
        )
        if requested_count < reserved_counts[license_name] + officer_license_counts[license_name]
    ]
    if insufficient_reserved_licenses:
        shortage_text = ", ".join(
            f"{license_name}: vpisano {requested_count}, potrebno "
            f"{reserved_counts[license_name] + officer_license_counts[license_name]}"
            for license_name, requested_count in (
                ("FL", request.fl_count),
                ("APS", request.aps_count),
                ("ACS", request.acs_count),
            )
            if license_name in insufficient_reserved_licenses
        )
        return ParetoResponse(
            requested_sector_hours=requested_sector_hours,
            notes=notes,
            warnings=[
                *warnings,
                "Vpisani FL/APS/ACS ne pokrijejo obveznih, fiksnih in officer vnosov.",
                shortage_text,
            ],
        )

    people = list(seed_people)
    required_indexes = set(range(len(people)))
    remaining_fl = request.fl_count - reserved_counts["FL"] - officer_license_counts["FL"]
    remaining_aps = request.aps_count - reserved_counts["APS"] - officer_license_counts["APS"]
    remaining_acs = request.acs_count - reserved_counts["ACS"] - officer_license_counts["ACS"]

    report_progress(progress_callback, 25, "Gradim kandidate za Pareto analizo.")
    max_workers = max_required_workers_per_hour(target_sector_counts)
    for license_name, remaining_count in (
        ("FL", remaining_fl),
        ("APS", remaining_aps),
        ("ACS", remaining_acs),
    ):
        people, next_id = add_optional_candidates(
            people,
            next_id,
            license_name,
            min(remaining_count, max(2, max_workers)),
            enabled_shift_rules(request.settings.shifts),
            target_sector_counts,
            shift_caps,
        )
    officer_start_index = len(people)
    people, next_id, officer_notes = add_officer_staff_people(request, people, next_id)
    required_indexes.update(range(officer_start_index, len(people)))
    notes.extend(officer_notes)
    people, next_id, office_pool_notes = add_office_pool_candidates(request, people, next_id, target_sector_counts)
    notes.extend(office_pool_notes)

    points = calculate_pareto_points(
        people,
        required_indexes,
        request,
        shift_map,
        target_sector_counts,
        minimum_required_fl,
        requested_sector_hours,
        shift_total_caps=shift_caps,
        progress_callback=progress_callback,
        cancel_callback=cancel_callback,
    )
    notes.append("Pareto analiza je ločen predizračun: za vsak limit ljudi pokaže najboljšo najdeno odprtost.")
    if shift_caps:
        notes.append(
            "Fiksno vpisane izmene omejujejo generator: "
            + ", ".join(f"{shift} največ {cap}" for shift, cap in sorted(shift_caps.items()))
            + "."
        )

    report_progress(progress_callback, 100, "Pareto analiza je končana.")
    return ParetoResponse(
        requested_sector_hours=requested_sector_hours,
        points=points,
        notes=notes,
        warnings=warnings,
    )


def solve_minimum_staff_by_people_limit(
    candidates: list[PersonState],
    required_indexes: set[int],
    request: CalculatorRequest,
    shift_map: dict[str, ShiftRule],
    target_sector_counts: list[int],
    license_target_counts: dict[str, int] | None,
    shift_total_caps: dict[str, int] | None,
    progress_callback: ProgressCallback | None = None,
    cancel_callback: CancelCallback | None = None,
    solution_callback: ScheduleSolutionCallback | None = None,
) -> tuple[ScheduledResult, SolverSnapshot] | None:
    requested_sector_hours = sum(target_sector_counts)
    if requested_sector_hours <= 0:
        return solve_schedule_with_cp_sat(
            candidates,
            required_indexes,
            request,
            shift_map,
            target_sector_counts,
            selected_total_cap=len(required_indexes),
            shift_license_caps=night_shift_license_caps(request),
            shift_total_caps=shift_total_caps,
            license_target_counts=license_target_counts,
            solution_callback=solution_callback,
            cancel_callback=cancel_callback,
        )

    lower_bound = minimum_people_lower_bound(candidates, required_indexes, request, shift_map, target_sector_counts)
    upper_bound = min(80, len(candidates))
    if lower_bound > upper_bound:
        return None

    people_limits = list(range(lower_bound, upper_bound + 1))
    point_time_limit = max(6, min(35, request.settings.cp_sat_time_limit_seconds // 4))
    point_settings = request.settings.model_copy(
        update={
            "cp_sat_time_limit_seconds": point_time_limit,
            "cp_sat_no_improvement_seconds": 0,
            "cp_sat_acceptable_sector_gap": 0,
        }
    )
    point_request = request.model_copy(update={"settings": point_settings})

    for position, people_limit in enumerate(people_limits, start=1):
        check_cancel(cancel_callback)
        report_progress(
            progress_callback,
            45 + round((position / max(1, len(people_limits))) * 35),
            f"Preverjam minimum ljudi: {people_limit} ljudi.",
        )
        compact_candidates, compact_required_indexes = compact_candidate_pool_for_people_limit(
            candidates,
            required_indexes,
            point_request,
            shift_map,
            target_sector_counts,
            people_limit,
            license_target_counts,
        )
        for seed_name, seed_candidates, seed_required_indexes in configuration_seed_candidate_pools(
            candidates,
            required_indexes,
            point_request,
            people_limit,
            requested_sector_hours,
            target_sector_counts,
        ):
            check_cancel(cancel_callback)
            report_progress(
                progress_callback,
                45 + round((position / max(1, len(people_limits))) * 35),
                f"Preverjam {people_limit} ljudi iz knjižnice: {seed_name} + popravki.",
            )
            tweak_candidates = seed_pool_with_backups(
                seed_candidates,
                compact_candidates,
                people_limit,
            )
            solved = solve_schedule_with_cp_sat(
                tweak_candidates,
                seed_required_indexes,
                point_request,
                shift_map,
                target_sector_counts,
                selected_total_cap=people_limit,
                shift_license_caps=night_shift_license_caps(point_request),
                shift_total_caps=shift_total_caps,
                license_target_counts=license_target_counts,
                minimum_covered_sector_hours=requested_sector_hours,
                stop_after_coverage_target=True,
                solution_callback=solution_callback,
                cancel_callback=cancel_callback,
            )
            if solved is not None:
                scheduled, solver_snapshot = solved
                if scheduled.total_hours >= requested_sector_hours:
                    return scheduled, solver_snapshot

        solved = solve_schedule_with_cp_sat(
            compact_candidates,
            compact_required_indexes,
            point_request,
            shift_map,
            target_sector_counts,
            selected_total_cap=people_limit,
            shift_license_caps=night_shift_license_caps(point_request),
            shift_total_caps=shift_total_caps,
            license_target_counts=license_target_counts,
            minimum_covered_sector_hours=requested_sector_hours,
            stop_after_coverage_target=True,
            solution_callback=solution_callback,
            cancel_callback=cancel_callback,
        )
        if solved is None:
            continue
        scheduled, solver_snapshot = solved
        if scheduled.total_hours >= requested_sector_hours:
            return scheduled, solver_snapshot

    return None


def calculate_demand_to_staff(
    request: CalculatorRequest,
    shift_map: dict[str, ShiftRule],
    target_sector_counts: list[int],
    progress_callback: ProgressCallback | None = None,
    cancel_callback: CancelCallback | None = None,
    incumbent_callback: IncumbentCallback | None = None,
) -> CalculatorResponse:
    if can_use_pattern_minimum_core(request):
        try:
            return calculate_pattern_minimum(request, progress_callback, cancel_callback)
        except PatternSearchCancelled as exc:
            raise CalculationCancelled(str(exc)) from exc

    notes: list[str] = []
    report_phase_progress(
        progress_callback,
        20,
        "preparation",
        "Priprava modela",
        "Pripravljam obvezne vloge, nočno zasedbo in fiksno vpisane ljudi.",
        "Office pool v tej fazi še ni kandidat za sektorje.",
    )
    check_cancel(cancel_callback)
    fixed_people, next_id, fixed_notes = add_fixed_staff_people(request, [], 0)
    locked_people, next_id, locked_notes = add_locked_staff_people(request, fixed_people, next_id)
    people, next_id, mandatory_notes, warnings = create_mandatory_people(request, locked_people, next_id)
    notes.extend(fixed_notes)
    notes.extend(locked_notes)
    notes.extend(mandatory_notes)
    required_indexes = set(range(len(people)))
    requested_sector_hours = sum(target_sector_counts)
    shift_caps = fixed_shift_total_caps(request)
    warnings.extend(fixed_shift_cap_warnings(people, shift_caps))
    reserved_counts = Counter(person.license for person in people)
    officer_license_counts = officer_counts_by_license(request)
    office_pool_license_counts = office_pool_counts_by_license(request)
    available_license_counts = Counter(
        {
            "FL": request.fl_count,
            "APS": request.aps_count,
            "ACS": request.acs_count,
        }
    )
    license_mix_counts = Counter(
        {
            "FL": request.license_mix_percent.fl,
            "APS": request.license_mix_percent.aps,
            "ACS": request.license_mix_percent.acs,
        }
    ) if request.license_mix_percent is not None else available_license_counts
    has_percent_license_mix = request.license_mix_percent is not None
    has_license_input = sum(license_mix_counts.values()) > 0
    auto_minimum_people = request.total_people <= 0
    uses_license_ratio = (auto_minimum_people or has_percent_license_mix) and has_license_input
    has_license_availability = request.total_people > 0 and has_license_input and not has_percent_license_mix
    selected_total_cap = (
        request.total_people + sum(officer_license_counts.values())
        if request.total_people > 0
        else 80
    )
    license_caps = None
    license_target_counts = None
    if has_percent_license_mix:
        license_target_counts = dict(license_mix_counts)
    if has_license_availability:
        license_caps = {
            license_name: available_license_counts[license_name] + officer_license_counts[license_name]
            for license_name in ("FL", "APS", "ACS")
        }
        if not request.prefer_minimal_fl:
            license_target_counts = dict(available_license_counts)
        insufficient_reserved = [
            license_name
            for license_name in ("FL", "APS", "ACS")
            if available_license_counts[license_name] < reserved_counts[license_name]
        ]
        if insufficient_reserved:
            warnings.append(
                "Razpoložljive licence so nižje od obveznih/fiksnih ljudi: "
                + ", ".join(
                    f"{license_name} vpisano {available_license_counts[license_name]}, potrebno {reserved_counts[license_name]}"
                    for license_name in insufficient_reserved
                )
                + "."
            )
    elif uses_license_ratio and not license_target_counts and not request.prefer_minimal_fl:
        license_target_counts = dict(license_mix_counts)

    report_phase_progress(
        progress_callback,
        30,
        "preparation",
        "Priprava kandidatov",
        "Gradim CP-SAT kandidate za minimalno zasedbo iz dovoljenih izmen in licenc.",
        "Izključene izmene iz nastavitev pravil se ne dodajo med kandidate.",
    )
    per_shift_candidate_count = max(2, min(10, max_required_workers_per_hour(target_sector_counts)))
    for license_name in ("FL", "APS", "ACS"):
        if has_percent_license_mix:
            if license_mix_counts[license_name] <= 0 and reserved_counts[license_name] <= 0:
                continue
            current_per_shift_count = per_shift_candidate_count
        elif has_license_availability:
            remaining_license_count = max(0, available_license_counts[license_name] - reserved_counts[license_name])
            if remaining_license_count <= 0:
                continue
            current_per_shift_count = min(per_shift_candidate_count, remaining_license_count)
        elif uses_license_ratio and available_license_counts[license_name] <= 0:
            continue
        else:
            current_per_shift_count = per_shift_candidate_count
        people, next_id = add_optional_candidates(
            people,
            next_id,
            license_name,
            current_per_shift_count,
            enabled_shift_rules(request.settings.shifts),
            target_sector_counts,
            shift_caps,
        )
    officer_start_index = len(people)
    people, next_id, officer_notes = add_officer_staff_people(request, people, next_id)
    required_indexes.update(range(officer_start_index, len(people)))
    notes.extend(officer_notes)
    check_cancel(cancel_callback)

    report_progress(progress_callback, 55, "Pripravljam CP-SAT preverjanje pokritosti.")
    def publish_incumbent(scheduled_result: ScheduledResult, solver_snapshot: SolverSnapshot) -> None:
        if incumbent_callback is None:
            return
        incumbent_notes = [
            *notes,
            "Začasna najboljša CP-SAT rešitev med optimizacijo.",
            f"Status CP-SAT rešitve: {solver_snapshot.status}.",
            "ALL sektor zahteva 2× FL; LOWER sprejme APS/FL; UPPER, MID, HIGH in TOP sprejmejo ACS/FL.",
        ]
        incumbent_callback(
            response_from_schedule(
                scheduled_result,
                request,
                sum(1 for person in scheduled_result.people if person.license == "FL"),
                incumbent_notes,
                list(warnings),
                requested_sector_hours,
                solver_snapshot,
            ),
            solver_snapshot,
        )

    solved: tuple[ScheduledResult, SolverSnapshot] | None = None
    office_pool_fallback_attempted = False
    office_pool_fallback_improved = False
    office_pool_fallback_used = False
    force_office_fallback = request.office_fallback_mode == "force"
    minimum_people_search_attempted = requested_sector_hours > 0 and auto_minimum_people and not force_office_fallback
    if minimum_people_search_attempted:
        solved = solve_minimum_staff_by_people_limit(
            people,
            required_indexes,
            request,
            shift_map,
            target_sector_counts,
            license_target_counts=license_target_counts,
            shift_total_caps=shift_caps,
            progress_callback=progress_callback,
            solution_callback=publish_incumbent,
            cancel_callback=cancel_callback,
        )
    regular_solution_hours = solved[0].total_hours if solved is not None else 0

    feasibility_attempted = requested_sector_hours > 0 and not auto_minimum_people and not force_office_fallback
    feasibility_seed_name: str | None = None
    feasibility_polish_candidates: list[PersonState] | None = None
    feasibility_polish_required_indexes: set[int] | None = None
    if feasibility_attempted:
        feasibility_time_limit = min(
            request.settings.cp_sat_time_limit_seconds,
            max(20, request.settings.cp_sat_time_limit_seconds // 3),
        )
        feasibility_settings = request.settings.model_copy(
            update={
                "cp_sat_time_limit_seconds": feasibility_time_limit,
                "cp_sat_no_improvement_seconds": 0,
                "cp_sat_acceptable_sector_gap": 0,
            }
        )
        feasibility_request = request.model_copy(update={"settings": feasibility_settings})
        manual_seed_time_limit = min(feasibility_time_limit, MANUAL_SEED_ATTEMPT_SECONDS)
        manual_seed_settings = feasibility_settings.model_copy(
            update={
                "cp_sat_time_limit_seconds": manual_seed_time_limit,
                "cp_sat_no_improvement_seconds": min(MANUAL_SEED_NO_IMPROVEMENT_SECONDS, manual_seed_time_limit),
            }
        )
        manual_seed_request = feasibility_request.model_copy(update={"settings": manual_seed_settings})
        report_phase_progress(
            progress_callback,
            55,
            "regular_feasibility",
            "Redna faza: dokaz polne pokritosti",
            f"CP-SAT najprej preverja, ali je možnih {requested_sector_hours}/{requested_sector_hours} sektorskih ur brez operativnega office poola.",
            "Če redna faza doseže cilj, office osebe ostanejo nedotaknjene.",
        )
        compact_candidates, compact_required_indexes = compact_candidate_pool_for_people_limit(
            people,
            required_indexes,
            feasibility_request,
            shift_map,
            target_sector_counts,
            selected_total_cap,
            license_target_counts,
        )
        for seed_name, seed_candidates, seed_required_indexes in configuration_seed_candidate_pools(
            people,
            required_indexes,
            manual_seed_request,
            selected_total_cap,
            requested_sector_hours,
            target_sector_counts,
            limit=1,
        ):
            check_cancel(cancel_callback)
            report_phase_progress(
                progress_callback,
                55,
                "manual_seed",
                "Redna faza: izbrana ročna baza",
                f"CP-SAT kratek warm-start poskus z bazo {seed_name}; limit {manual_seed_time_limit} s.",
                "Če ta baza hitro ne pokrije cilja, nadaljujem s polnim CP-SAT računom.",
            )
            seed_candidates_with_backups = seed_pool_with_backups(seed_candidates, compact_candidates, selected_total_cap)
            solved = solve_schedule_with_cp_sat(
                seed_candidates_with_backups,
                seed_required_indexes,
                manual_seed_request,
                shift_map,
                target_sector_counts,
                license_caps=license_caps,
                selected_total_cap=selected_total_cap,
                shift_license_caps=night_shift_license_caps(manual_seed_request),
                shift_total_caps=shift_caps,
                license_target_counts=license_target_counts,
                minimum_covered_sector_hours=requested_sector_hours,
                stop_after_coverage_target=True,
                solution_callback=publish_incumbent,
                cancel_callback=cancel_callback,
            )
            if solved is not None and solved[0].total_hours >= requested_sector_hours:
                feasibility_seed_name = seed_name
                feasibility_polish_candidates = seed_candidates_with_backups
                feasibility_polish_required_indexes = seed_required_indexes
                break

        if solved is None or solved[0].total_hours < requested_sector_hours:
            solved = solve_schedule_with_cp_sat(
                compact_candidates,
                compact_required_indexes,
                feasibility_request,
                shift_map,
                target_sector_counts,
                license_caps=license_caps,
                selected_total_cap=selected_total_cap,
                shift_license_caps=night_shift_license_caps(feasibility_request),
                shift_total_caps=shift_caps,
                license_target_counts=license_target_counts,
                minimum_covered_sector_hours=requested_sector_hours,
                stop_after_coverage_target=True,
                solution_callback=publish_incumbent,
                cancel_callback=cancel_callback,
            )
            if solved is not None and solved[0].total_hours >= requested_sector_hours:
                feasibility_polish_candidates = compact_candidates
                feasibility_polish_required_indexes = compact_required_indexes

        if (
            solved is not None
            and solved[0].total_hours >= requested_sector_hours
            and feasibility_polish_candidates is not None
            and feasibility_polish_required_indexes is not None
            and (license_target_counts is not None or request.prefer_minimal_fl)
        ):
            report_phase_progress(
                progress_callback,
                58,
                "regular_polish",
                "Redna faza: poliranje razmerja",
                "Poliram polno pokrit people-limit razpored po razmerju licenc.",
                "Pokritost je že najdena; zdaj se ureja kakovost sestave.",
            )
            polished = solve_schedule_with_cp_sat(
                feasibility_polish_candidates,
                feasibility_polish_required_indexes,
                feasibility_request,
                shift_map,
                target_sector_counts,
                license_caps=license_caps,
                selected_total_cap=selected_total_cap,
                shift_license_caps=night_shift_license_caps(feasibility_request),
                shift_total_caps=shift_caps,
                license_target_counts=license_target_counts,
                minimum_covered_sector_hours=requested_sector_hours,
                stop_after_coverage_target=False,
                solution_callback=publish_incumbent,
                cancel_callback=cancel_callback,
            )
            if polished is not None and polished[0].total_hours >= requested_sector_hours:
                solved = polished

    regular_solution_hours = solved[0].total_hours if solved is not None else regular_solution_hours

    if solved is None and not force_office_fallback:
        report_phase_progress(
            progress_callback,
            62,
            "regular_optimization",
            "Redna faza: največja pokritost",
            "CP-SAT maksimizira pokritost, število ljudi in izkoriščenost brez operativnega office poola.",
            "Če se čas izteče in ni dokazano, da je to konec, bo uporabnik izbral naslednji korak.",
        )
        solved = solve_schedule_with_cp_sat(
            people,
            required_indexes,
            request,
            shift_map,
            target_sector_counts,
            license_caps=license_caps,
            selected_total_cap=selected_total_cap,
            shift_license_caps=night_shift_license_caps(request),
            shift_total_caps=shift_caps,
            license_target_counts=license_target_counts,
            solution_callback=publish_incumbent,
            cancel_callback=cancel_callback,
        )
        regular_solution_hours = solved[0].total_hours if solved is not None else regular_solution_hours
    elif force_office_fallback:
        report_phase_progress(
            progress_callback,
            62,
            "office_fallback_requested",
            "Office fallback na zahtevo",
            "Redna faza je preskočena, ker je uporabnik izbral takojšnji preizkus operativnega office poola.",
            "To je namenjeno nadaljevanju po timeoutu ali ročni odločitvi.",
        )

    if office_fallback_should_run(request, solved, requested_sector_hours):
        office_pool_fallback_attempted = True
        report_phase_progress(
            progress_callback,
            82,
            "office_fallback",
            "Zadnja možnost: office fallback",
            "Preverjam operativni office pool, ker redne možnosti še niso zaprle vseh sektorskih ur.",
            "Office oseba se uporabi samo, če dejansko izboljša rešitev.",
        )
        fallback_people, _next_id, office_pool_notes = add_office_pool_candidates(
            request,
            people,
            next_id,
            target_sector_counts,
        )
        fallback_solved = solve_schedule_with_cp_sat(
            fallback_people,
            set(required_indexes),
            request,
            shift_map,
            target_sector_counts,
            license_caps=license_caps_with_office_pool(license_caps, request),
            selected_total_cap=selected_total_cap + sum(office_pool_license_counts.values()),
            non_pool_selected_cap=selected_total_cap,
            shift_license_caps=night_shift_license_caps(request),
            shift_total_caps=shift_caps,
            source_license_caps=office_pool_source_license_caps(request),
            license_target_counts=license_target_counts,
            solution_callback=publish_incumbent,
            cancel_callback=cancel_callback,
        )
        if fallback_solved is not None and (solved is None or fallback_solved[0].total_hours > solved[0].total_hours):
            solved = fallback_solved
            notes.extend(office_pool_notes)
            office_pool_fallback_improved = True
            office_pool_fallback_used = solution_uses_office_pool(fallback_solved[0])
    elif (
        total_office_pool_count(request) > 0
        and request.office_fallback_mode == "auto"
        and solved is not None
        and solved[0].total_hours < requested_sector_hours
        and solver_stopped_on_time_limit(solved)
    ):
        notes.append(
            "Redna CP-SAT faza se je ustavila zaradi časovne omejitve, zato operativni office pool ni bil samodejno uporabljen. "
            "Uporabnik lahko nadaljuje redno fazo ali posebej zažene office fallback."
        )

    if solved is None:
        scheduled = ScheduledResult(people=[], hourly_sectors=[[] for _ in range(HOURS_IN_DAY)], total_hours=0)
        solver_snapshot = SolverSnapshot(status="NO_SOLUTION")
        warnings.append("CP-SAT ni našel izvedljivega razporeda znotraj trenutnih omejitev.")
    else:
        scheduled, solver_snapshot = solved
    check_cancel(cancel_callback)

    report_phase_progress(
        progress_callback,
        90,
        "finalizing",
        "Priprava rezultata",
        "Pripravljam rezultat, opozorila in razlago uporabljene rešitve.",
        None,
    )
    minimum_required_fl = sum(1 for person in scheduled.people if person.license == "FL")
    baseline_min_people, baseline_formula = baseline_min_people_for_profile(target_sector_counts)
    notes.append(f"Osnovno izhodišče pred CP-SAT: {baseline_formula}")
    if auto_minimum_people:
        notes.append(
            "Način 3 uporablja OR-Tools CP-SAT po naraščajočih limitih ljudi: "
            "prvi limit, ki pokrije vse zahtevane sektorske ure, je izbran kot minimalna zasedba."
        )
        notes.append(
            "Pri vsakem limitu najprej uporabi najbližje ročno izdelane konfiguracije iz knjižnice "
            "kot kandidatni bazen: npr. 25n5 sme služiti tudi za preizkus 24 ljudi, CP-SAT pa lahko "
            "enega človeka izpusti ali doda boljše backup izmene."
        )
        if scheduled.total_hours >= requested_sector_hours:
            notes.append(f"Minimum ljudi je najden pri {len(scheduled.people)} ljudeh za {requested_sector_hours}/{requested_sector_hours} sektorskih ur.")
        else:
            notes.append("Prehod po limitih ni našel polne pokritosti, zato je model prešel na najboljšo najdeno pokritost.")
    else:
        notes.append("Način 2 uporablja OR-Tools CP-SAT: najprej maksimizira pokrite sektorje, nato minimizira število ljudi znotraj limita.")
    if sum(office_pool_license_counts.values()) > 0:
        if office_pool_fallback_used:
            notes.append(
                "Operativni office pool je bil uporabljen šele kot fallback po rednih možnostih; "
                f"redna rešitev je imela {regular_solution_hours}/{requested_sector_hours} SH."
            )
        elif office_pool_fallback_improved:
            notes.append("Fallback faza je izboljšala pokritost, vendar končna rešitev ni potrebovala office osebe.")
        elif office_pool_fallback_attempted:
            notes.append("Operativni office pool je bil preizkušen kot fallback, vendar ni izboljšal pokritosti.")
        else:
            notes.append("Operativni office pool ni bil uporabljen, ker redne možnosti že pokrijejo zahtevano odprtost.")
    if feasibility_attempted and scheduled.total_hours >= requested_sector_hours:
        notes.append("Feasibility faza je našla razpored, ki pokrije vse zahtevane sektorske ure.")
        if feasibility_seed_name:
            notes.append(f"Prvi polno pokrit people-limit razpored je bil najden s seed bazo {feasibility_seed_name}.")
    elif feasibility_attempted:
        notes.append("Feasibility faza ni našla polne pokritosti v dodeljenem času, zato je model prešel na maksimizacijo pokritosti.")

    if has_percent_license_mix:
        notes.append(
            "FL/APS/ACS se v tem načinu berejo kot mehko ciljno razmerje "
            f"(FL {license_mix_counts['FL']} %, APS {license_mix_counts['APS']} %, ACS {license_mix_counts['ACS']} %), "
            "ne kot trde zgornje meje."
        )
        if request.prefer_minimal_fl:
            notes.append("Vključena je dodatna optimizacija, ki pri enaki pokritosti zmanjšuje uporabo FL.")
        if request.total_people > 0:
            officer_count = sum(officer_license_counts.values())
            if officer_count:
                notes.append(
                    f"Navadne izmene so omejene na največ {request.total_people}; "
                    f"skupaj z obveznimi office izmenami je limit {request.total_people + officer_count} ljudi."
                )
            else:
                notes.append(f"Skupno število ljudi je omejeno na največ {request.total_people}.")
    elif has_license_availability:
        notes.append(
            "Način 2 upošteva vpisano razpoložljivost licenc "
            f"(FL {available_license_counts['FL']}, APS {available_license_counts['APS']}, "
            f"ACS {available_license_counts['ACS']}) kot zgornjo mejo."
        )
        if request.prefer_minimal_fl:
            notes.append("Vključena je dodatna optimizacija, ki pri enaki pokritosti zmanjšuje uporabo FL.")
        else:
            notes.append("Pri enaki pokritosti model mehko sledi vpisanemu razmerju FL/APS/ACS.")
    elif uses_license_ratio:
        notes.append(
            "Način 3 bere FL/APS/ACS kot razmerje licenc, ne kot število ljudi "
            f"(FL {license_mix_counts['FL']}, APS {license_mix_counts['APS']}, "
            f"ACS {license_mix_counts['ACS']})."
        )
        if request.prefer_minimal_fl:
            notes.append("Checkbox za čim manj FL je vklopljen, zato razmerje ne kaznuje dodatnih/odvečnih FL; FL se zmanjšuje samo pri enaki pokritosti in istem limitu ljudi.")
        else:
            notes.append("Checkbox za čim manj FL ni vklopljen; pri enaki pokritosti in istem številu ljudi model mehko sledi vpisanemu razmerju licenc.")
    else:
        notes.append(
            "Razpoložljivost ljudi ni vpisana; CP-SAT sam išče najmanjše število ljudi, "
            "ki pokrije zahtevano odprtost."
        )
    notes.append("Pri enaki pokritosti in številu ljudi CP-SAT izbere zasedbo z manj neizkoriščene kapacitete izmen.")
    notes.append(f"Status CP-SAT rešitve: {solver_snapshot.status}.")
    if solver_snapshot.stop_reason:
        notes.append(f"Politika izračuna je samodejno ustavila CP-SAT: {solver_snapshot.stop_reason}.")
    if solver_snapshot.sector_gap_to_best_bound is not None and solver_snapshot.best_bound_sector_hours is not None:
        notes.append(
            "Dokazana zgornja meja po trenutnem CP-SAT boundu je "
            f"{solver_snapshot.best_bound_sector_hours} sektorskih ur "
            f"(razlika {solver_snapshot.sector_gap_to_best_bound})."
        )
    notes.append("ALL sektor zahteva 2× FL; LOWER sprejme APS/FL; UPPER, MID, HIGH in TOP sprejmejo ACS/FL.")
    notes.append("Razpored se po CP-SAT rešitvi lokalno zgladi: če ne zmanjša pokritosti, ohrani isti sektor in zamenja levo/desno pozicijo po eni uri.")
    active_officers = [person for person in scheduled.people if is_officer(person) and person.sector_hours > 0]
    if active_officers:
        notes.append(f"Uporabljenih je {len(active_officers)} officerjev iz pisarne.")
        notes.append("Office sektorske ure imajo mehko prednost na začetku ali koncu office izmene; sredina se uporabi, kadar izboljša pokritost.")
    shortfall_warning = coverage_shortfall_warning(
        scheduled.total_hours,
        requested_sector_hours,
        solver_snapshot,
        "Znotraj trenutnih pravil ni bilo mogoče pokriti vseh zahtevanih sektorskih ur.",
    )
    if shortfall_warning:
        warnings.append(shortfall_warning)

    response = response_from_schedule(
        scheduled,
        request,
        minimum_required_fl,
        notes,
        warnings,
        requested_sector_hours,
        solver_snapshot,
    )
    if response.missing_sector_hours > 0:
        report_progress(progress_callback, 100, f"Končano, manjka še {response.missing_sector_hours} sektorskih ur.")
    else:
        report_progress(progress_callback, 100, "Končano.")
    return response


def calculate(
    request: CalculatorRequest,
    progress_callback: ProgressCallback | None = None,
    cancel_callback: CancelCallback | None = None,
    incumbent_callback: IncumbentCallback | None = None,
) -> CalculatorResponse:
    report_phase_progress(
        progress_callback,
        5,
        "preparation",
        "Priprava modela",
        "Pripravljam vhodne podatke za kalkulator.",
        "Najprej se sestavi redni model brez operativnega office poola.",
    )
    check_cancel(cancel_callback)
    shift_map = shift_map_for_request(request)
    target_sector_counts = target_sector_counts_for_request(request)
    report_phase_progress(
        progress_callback,
        15,
        "preparation",
        "Validacija pravil",
        "Validiram pravila, dovoljene izmene in ciljno odprtost.",
        "Nastavitve pravil veljajo za vsak zagon kalkulatorja.",
    )
    if request.calculation_mode == "demand_to_staff":
        return calculate_demand_to_staff(
            request,
            shift_map,
            target_sector_counts,
            progress_callback,
            cancel_callback,
            incumbent_callback,
        )

    notes: list[str] = []
    check_cancel(cancel_callback)
    fixed_people, next_id, fixed_notes = add_fixed_staff_people(request, [], 0)
    locked_people, next_id, locked_notes = add_locked_staff_people(request, fixed_people, next_id)
    seed_people, next_id, mandatory_notes, warnings = create_mandatory_people(request, locked_people, next_id)
    notes.extend(fixed_notes)
    notes.extend(locked_notes)
    notes.extend(mandatory_notes)
    reserved_counts = Counter(person.license for person in seed_people)
    officer_license_counts = officer_counts_by_license(request)
    office_pool_license_counts = office_pool_counts_by_license(request)
    minimum_required_fl = reserved_counts["FL"]
    shift_caps = fixed_shift_total_caps(request)
    warnings.extend(fixed_shift_cap_warnings(seed_people, shift_caps))

    insufficient_reserved_licenses = [
        license_name
        for license_name, requested_count in (
            ("FL", request.fl_count),
            ("APS", request.aps_count),
            ("ACS", request.acs_count),
        )
        if requested_count < reserved_counts[license_name] + officer_license_counts[license_name]
    ]

    if insufficient_reserved_licenses:
        report_progress(progress_callback, 100, "Konfiguracija ni izvedljiva: premalo ljudi za obvezne in fiksne izmene.")
        shortage_text = ", ".join(
            f"{license_name}: vpisano {requested_count}, potrebno "
            f"{reserved_counts[license_name] + officer_license_counts[license_name]}"
            for license_name, requested_count in (
                ("FL", request.fl_count),
                ("APS", request.aps_count),
                ("ACS", request.acs_count),
            )
            if license_name in insufficient_reserved_licenses
        )
        return CalculatorResponse(
            feasible=False,
            max_sector_hours=0,
            requested_sector_hours=sum(target_sector_counts),
            missing_sector_hours=sum(target_sector_counts),
            minimum_required_fl=minimum_required_fl,
            unused_people=request.total_people,
            people=[],
            shift_summary=[],
            hourly_coverage=[],
            notes=[
                "Vpisani FL/APS/ACS ne pokrijejo obveznih in fiksno vpisanih izmen.",
                shortage_text,
            ],
            warnings=warnings,
        )

    people = list(seed_people)
    required_indexes = set(range(len(people)))
    remaining_fl = request.fl_count - reserved_counts["FL"] - officer_license_counts["FL"]
    remaining_aps = request.aps_count - reserved_counts["APS"] - officer_license_counts["APS"]
    remaining_acs = request.acs_count - reserved_counts["ACS"] - officer_license_counts["ACS"]

    report_phase_progress(
        progress_callback,
        30,
        "preparation",
        "Priprava kandidatov",
        "Gradim CP-SAT kandidate iz razpoložljivih ljudi in dovoljenih izmen.",
        "Izključene izmene iz nastavitev pravil se ne dodajo med kandidate.",
    )
    max_workers = max_required_workers_per_hour(target_sector_counts)
    for license_name, remaining_count in (
        ("FL", remaining_fl),
        ("APS", remaining_aps),
        ("ACS", remaining_acs),
    ):
        people, next_id = add_optional_candidates(
            people,
            next_id,
            license_name,
            min(remaining_count, max(2, max_workers)),
            enabled_shift_rules(request.settings.shifts),
            target_sector_counts,
            shift_caps,
        )
    officer_start_index = len(people)
    people, next_id, officer_notes = add_officer_staff_people(request, people, next_id)
    required_indexes.update(range(officer_start_index, len(people)))
    notes.extend(officer_notes)
    regular_people = list(people)
    regular_required_indexes = set(required_indexes)

    report_phase_progress(
        progress_callback,
        50,
        "preparation",
        "Preverjanje omejitev",
        "Preverjam omejitve razporeda, vloge in limite po izmenah.",
        None,
    )
    check_cancel(cancel_callback)
    force_office_fallback = request.office_fallback_mode == "force"
    if force_office_fallback:
        report_phase_progress(
            progress_callback,
            75,
            "office_fallback_requested",
            "Office fallback na zahtevo",
            "Redna faza je preskočena, ker je uporabnik izbral takojšnji preizkus operativnega office poola.",
            "To je namenjeno nadaljevanju po timeoutu ali ročni odločitvi.",
        )
    else:
        report_phase_progress(
            progress_callback,
            75,
            "regular_optimization",
            "Redna faza: CP-SAT optimizacija",
            "CP-SAT optimizira redne možnosti brez operativnega office poola.",
            "Če se čas izteče in ni dokazano, da je to konec, bo uporabnik izbral naslednji korak.",
        )

    requested_sector_hours = sum(target_sector_counts)
    office_pool_fallback_attempted = False
    office_pool_fallback_improved = False
    office_pool_fallback_used = False
    regular_solution_hours = 0

    def publish_incumbent(scheduled_result: ScheduledResult, solver_snapshot: SolverSnapshot) -> None:
        if incumbent_callback is None:
            return
        incumbent_notes = [
            *notes,
            "Začasna najboljša CP-SAT rešitev med optimizacijo.",
            f"Status CP-SAT rešitve: {solver_snapshot.status}.",
            "Generator spoštuje delovne ure izmen: npr. A6 je na voljo samo 06–14, A21/V3 samo 21–07.",
            "ALL sektor zahteva 2× FL; LOWER sprejme APS/FL; UPPER, MID, HIGH in TOP sprejmejo ACS/FL.",
        ]
        incumbent_callback(
            response_from_schedule(
                scheduled_result,
                request,
                minimum_required_fl,
                incumbent_notes,
                list(warnings),
                requested_sector_hours,
                solver_snapshot,
            ),
            solver_snapshot,
        )

    solved = None
    if not force_office_fallback:
        solved = solve_schedule_with_cp_sat(
            regular_people,
            regular_required_indexes,
            request,
            shift_map,
            target_sector_counts,
            license_caps={
                "FL": request.fl_count,
                "APS": request.aps_count,
                "ACS": request.acs_count,
            },
            selected_total_cap=request.total_people,
            shift_license_caps=night_shift_license_caps(request),
            shift_total_caps=shift_caps,
            solution_callback=publish_incumbent,
            cancel_callback=cancel_callback,
        )
    regular_solution_hours = solved[0].total_hours if solved is not None else 0

    if office_fallback_should_run(request, solved, requested_sector_hours):
        office_pool_fallback_attempted = True
        report_phase_progress(
            progress_callback,
            82,
            "office_fallback",
            "Zadnja možnost: office fallback",
            "Preverjam operativni office pool, ker redne možnosti še niso zaprle vseh sektorskih ur.",
            "Office oseba se uporabi samo, če dejansko izboljša rešitev.",
        )
        people, next_id, office_pool_notes = add_office_pool_candidates(request, regular_people, next_id, target_sector_counts)
        fallback_solved = solve_schedule_with_cp_sat(
            people,
            regular_required_indexes,
            request,
            shift_map,
            target_sector_counts,
            license_caps={
                "FL": request.fl_count + office_pool_license_counts["FL"],
                "APS": request.aps_count + office_pool_license_counts["APS"],
                "ACS": request.acs_count + office_pool_license_counts["ACS"],
            },
            selected_total_cap=request.total_people + sum(office_pool_license_counts.values()),
            non_pool_selected_cap=request.total_people,
            shift_license_caps=night_shift_license_caps(request),
            shift_total_caps=shift_caps,
            source_license_caps=office_pool_source_license_caps(request),
            solution_callback=publish_incumbent,
            cancel_callback=cancel_callback,
        )
        if fallback_solved is not None and (solved is None or fallback_solved[0].total_hours > solved[0].total_hours):
            solved = fallback_solved
            notes.extend(office_pool_notes)
            office_pool_fallback_improved = True
            office_pool_fallback_used = solution_uses_office_pool(fallback_solved[0])
    elif (
        total_office_pool_count(request) > 0
        and request.office_fallback_mode == "auto"
        and solved is not None
        and solved[0].total_hours < requested_sector_hours
        and solver_stopped_on_time_limit(solved)
    ):
        notes.append(
            "Redna CP-SAT faza se je ustavila zaradi časovne omejitve, zato operativni office pool ni bil samodejno uporabljen. "
            "Uporabnik lahko nadaljuje redno fazo ali posebej zažene office fallback."
        )

    if solved is None:
        required_people = [regular_people[index] for index in sorted(regular_required_indexes)]
        scheduled = ScheduledResult(people=required_people, hourly_sectors=[[] for _ in range(HOURS_IN_DAY)], total_hours=0)
        solver_snapshot = SolverSnapshot(status="NO_SOLUTION")
        warnings.append("CP-SAT ni našel izvedljivega razporeda znotraj trenutnih omejitev.")
    else:
        scheduled, solver_snapshot = solved
    check_cancel(cancel_callback)

    report_phase_progress(
        progress_callback,
        90,
        "finalizing",
        "Priprava rezultata",
        "Pripravljam rezultat in diagnostična sporočila.",
        None,
    )
    notes.append("OR-Tools CP-SAT najprej maksimizira pokrite sektorje, nato minimizira število uporabljenih ljudi.")
    if sum(office_pool_license_counts.values()) > 0:
        if office_pool_fallback_used:
            notes.append(
                "Operativni office pool je bil uporabljen šele kot fallback po rednih možnostih; "
                f"redna rešitev je imela {regular_solution_hours}/{requested_sector_hours} SH."
            )
        elif office_pool_fallback_improved:
            notes.append("Fallback faza je izboljšala pokritost, vendar končna rešitev ni potrebovala office osebe.")
        elif office_pool_fallback_attempted:
            notes.append("Operativni office pool je bil preizkušen kot fallback, vendar ni izboljšal pokritosti.")
        else:
            notes.append("Operativni office pool ni bil uporabljen, ker redne možnosti že pokrijejo zahtevano odprtost.")
    notes.append("Pri enaki pokritosti in številu ljudi CP-SAT izbere zasedbo z manj neizkoriščene kapacitete izmen.")
    notes.append(f"Status CP-SAT rešitve: {solver_snapshot.status}.")
    if solver_snapshot.stop_reason:
        notes.append(f"Politika izračuna je samodejno ustavila CP-SAT: {solver_snapshot.stop_reason}.")
    if solver_snapshot.sector_gap_to_best_bound is not None and solver_snapshot.best_bound_sector_hours is not None:
        notes.append(
            "Dokazana zgornja meja po trenutnem CP-SAT boundu je "
            f"{solver_snapshot.best_bound_sector_hours} sektorskih ur "
            f"(razlika {solver_snapshot.sector_gap_to_best_bound})."
        )
    notes.append("Generator spoštuje delovne ure izmen: npr. A6 je na voljo samo 06–14, A21/V3 samo 21–07.")
    required_a21_count = required_a21_fl_count(request)
    if not request.settings.include_night_fl_requirement:
        notes.append("Nočna A21 FL zahteva je izklopljena; V3 ostane obvezni FL vodja nočne izmene.")
    elif request.settings.include_required_shift_leaders:
        notes.append(
            "Nočna FL zasedba je omejena na "
            f"V3 + {required_a21_count}× A21 "
            f"(skupaj {required_a21_count + 1} FL), APS/ACS pa lahko pokrivajo A21 po potrebi."
        )
    else:
        notes.append(
            "Nočna FL zasedba je omejena na "
            f"{required_a21_count} FL v A21; APS/ACS lahko pokrivajo A21 po potrebi."
        )
    notes.append("Kalkulator odpira največ toliko sektorjev, kot jih uporabnik označi v urnem vnosu želene odprtosti.")
    notes.append("ALL sektor zahteva 2× FL; LOWER sprejme APS/FL; UPPER, MID, HIGH in TOP sprejmejo ACS/FL.")
    notes.append("Razpored se po CP-SAT rešitvi lokalno zgladi: če ne zmanjša pokritosti, ohrani isti sektor in zamenja levo/desno pozicijo po eni uri.")
    notes.append("FMP je dovoljen kot sektorski kontrolor, vendar ima pri izbiri delavcev slabšo prioriteto.")
    active_officers = [person for person in scheduled.people if is_officer(person) and person.sector_hours > 0]
    if active_officers:
        notes.append(f"Uporabljenih je {len(active_officers)} officerjev iz pisarne.")
        notes.append("Office sektorske ure imajo mehko prednost na začetku ali koncu office izmene; sredina se uporabi, kadar izboljša pokritost.")
    office_recommendation = recommended_office_shift_summary(scheduled.people)
    if office_recommendation:
        notes.append(office_recommendation)
    if shift_caps:
        notes.append(
            "Fiksno vpisane izmene omejujejo generator: "
            + ", ".join(f"{shift} največ {cap}" for shift, cap in sorted(shift_caps.items()))
            + "."
        )

    shortfall_warning = coverage_shortfall_warning(
        scheduled.total_hours,
        requested_sector_hours,
        solver_snapshot,
        "Z vnesenimi ljudmi ni mogoče pokriti vseh zahtevanih sektorskih ur.",
    )
    if shortfall_warning:
        warnings.append(shortfall_warning)

    response = response_from_schedule(
        scheduled,
        request,
        minimum_required_fl,
        notes,
        warnings,
        requested_sector_hours,
        solver_snapshot,
    )
    if response.missing_sector_hours > 0:
        report_progress(progress_callback, 100, f"Končano, manjka še {response.missing_sector_hours} sektorskih ur.")
    else:
        report_progress(progress_callback, 100, "Končano.")
    return response

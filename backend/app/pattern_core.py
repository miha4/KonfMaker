from __future__ import annotations

import math
import json
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Thread
from time import monotonic
from typing import Callable

from ortools.sat.python import cp_model

from .models import (
    CalculatorRequest,
    CalculatorResponse,
    HourlyCoverage,
    SectorAssignment,
    ShiftRule,
    ShiftSummary,
    VirtualPerson,
)

DAY_START = 7
HOURS_IN_DAY = 24
DEFAULT_FMP_SHIFT = "A9"
FMP_AUTO_SHIFT_CODES = ("A7", "A8", "A9", "A10", "A11")
FMP_LEADER_OVERLAP_PENALTY = 1_200_000
LICENSES = ("FL", "APS", "ACS")
SECTOR_DISPLAY_ORDER = ["ALL", "LOWER", "UPPER", "MID", "HIGH", "TOP"]
SECTOR_PROFILES = {
    0: [],
    1: ["ALL"],
    2: ["LOWER", "UPPER"],
    3: ["LOWER", "UPPER", "TOP"],
    4: ["LOWER", "UPPER", "HIGH", "TOP"],
    5: ["LOWER", "UPPER", "MID", "HIGH", "TOP"],
}
MANDATORY_V3_SHIFT = ShiftRule(code="V3", start_hour=21, duration_hours=10)
CP_SAT_WORKERS = 8
PATTERN_CACHE_ENV = "KONFMAKER_PATTERN_CACHE_PATH"
DEFAULT_PATTERN_CACHE_PATH = Path(__file__).resolve().parent.parent / ".pattern-cache" / "patterns.json"
PATTERN_PROGRESS_PREFIX = "__KONFMAKER_PATTERN_PROGRESS__"

ProgressCallback = Callable[[int, str], None]
CancelCallback = Callable[[], bool]


class PatternSearchCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkPattern:
    code: str
    shift: str
    license: str
    role: str | None
    slots: tuple[int, ...]
    work_hours: int
    required_group: str | None = None

    @property
    def group_key(self) -> tuple[str, str, str | None]:
        return self.license, self.shift, self.role


@dataclass(frozen=True)
class PatternLibrary:
    patterns: list[WorkPattern]
    required_group_counts: dict[str, int]
    generated_at_seconds: float
    rule_signature: str
    exact_group_counts: dict[str, int] = field(default_factory=dict)
    cache_status: str = "generated"
    cache_path: str | None = None

    @property
    def pattern_count(self) -> int:
        return len(self.patterns)


@dataclass(frozen=True)
class PatternSearchStep:
    people_limit: int
    status: str
    elapsed_seconds: float
    message: str


@dataclass(frozen=True)
class PatternSolveResult:
    status: str
    selected_counts: dict[int, int]
    slot_assignment_counts: dict[int, dict[str, int]]
    objective_value: float | None = None
    best_objective_bound: float | None = None


@dataclass(frozen=True)
class PatternRequirement:
    license: str
    shift: str
    role: str | None
    required_count: int
    max_work_hours: int | None
    required_group: str | None = None
    exact_count: int | None = None


def can_use_pattern_minimum_core(request: CalculatorRequest) -> bool:
    """Keep the first version isolated from advanced manual overrides."""
    return (
        request.calculation_mode == "demand_to_staff"
        and request.total_people <= 0
        and not request.fixed_staff
        and not request.locked_staff
        and not request.officer_staff
        and not request.office_pool
        and request.continuation_min_sector_hours is None
        and request.leader_exception_mode == "forbid"
        and request.requested_sector_counts is not None
    )


def _report(progress_callback: ProgressCallback | None, progress: int, message: str) -> None:
    if progress_callback is not None:
        progress_callback(progress, message)


def _pattern_progress_message(phase: str, message: str, **data: object) -> str:
    return PATTERN_PROGRESS_PREFIX + json.dumps(
        {"phase": phase, "message": message, **data},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _report_pattern(
    progress_callback: ProgressCallback | None,
    progress: int,
    phase: str,
    message: str,
    **data: object,
) -> None:
    _report(progress_callback, progress, _pattern_progress_message(phase, message, **data))


def _check_cancel(cancel_callback: CancelCallback | None) -> None:
    if cancel_callback is not None and cancel_callback():
        raise PatternSearchCancelled("Izračun je bil preklican.")


def hour_index(hour: int) -> int:
    return (hour - DAY_START) % HOURS_IN_DAY


def hour_label(index: int) -> str:
    start = (DAY_START + index) % HOURS_IN_DAY
    end = (start + 1) % HOURS_IN_DAY
    return f"{start:02d}:00–{end:02d}:00"


def shift_slots(shift: ShiftRule) -> tuple[int, ...]:
    return tuple(hour_index(shift.start_hour + offset) for offset in range(shift.duration_hours))


def blocked_role_offsets(role: str | None, shift: ShiftRule) -> set[int]:
    role_name = (role or "").upper()
    if role_name in {"V1", "V2"} and shift.duration_hours > 0:
        return {0, shift.duration_hours - 1}
    if role_name == "V3" and shift.duration_hours > 0:
        return {0}
    return set()


def enabled_shift_rules(shifts: list[ShiftRule]) -> list[ShiftRule]:
    return [shift for shift in shifts if shift.enabled]


def display_shift(shift: str, role: str | None) -> str:
    if role == "V3" and shift == "V3":
        return "A21"
    return shift


def sector_names_for_count(sector_count: int) -> list[str]:
    if sector_count in SECTOR_PROFILES:
        return list(SECTOR_PROFILES[sector_count])
    extras_needed = max(0, sector_count - 5)
    return list(SECTOR_PROFILES[5]) + [f"EXTRA {index}" for index in range(6, 6 + extras_needed)]


def sector_display_names_for_max(max_sector_count: int) -> list[str]:
    extras_needed = max(0, max_sector_count - 5)
    return list(SECTOR_DISPLAY_ORDER) + [f"EXTRA {index}" for index in range(6, 6 + extras_needed)]


def sector_seat_requirements(sector_count: int) -> dict[str, int]:
    sectors = sector_names_for_count(sector_count)
    return {
        "all": 2 if "ALL" in sectors else 0,
        "lower": 2 if "LOWER" in sectors else 0,
        "above": 2 * len([sector for sector in sectors if sector not in {"ALL", "LOWER"}]),
    }


def can_work_bits(bits: tuple[int, ...], max_consecutive: int, rest_after_max: int) -> bool:
    if not bits:
        return True
    consecutive = 0
    for bit in bits:
        consecutive = consecutive + 1 if bit else 0
        if consecutive > max_consecutive:
            return False

    window_size = max_consecutive + rest_after_max
    if window_size <= 0:
        return True
    for start in range(0, len(bits) - window_size + 1):
        if sum(bits[start : start + window_size]) > max_consecutive:
            return False
    return True


def generate_shift_patterns(
    shift: ShiftRule,
    max_consecutive: int,
    rest_after_max: int,
    *,
    max_work_hours: int | None = None,
    include_zero: bool = False,
    blocked_offsets: set[int] | None = None,
) -> list[tuple[int, ...]]:
    patterns: list[tuple[int, ...]] = []
    blocked = blocked_offsets or set()

    def visit(position: int, bits: tuple[int, ...]) -> None:
        if position == shift.duration_hours:
            work_hours = sum(bits)
            if work_hours == 0 and not include_zero:
                return
            if max_work_hours is not None and work_hours > max_work_hours:
                return
            if can_work_bits(bits, max_consecutive, rest_after_max):
                patterns.append(bits)
            return

        possible_bits = (0,) if position in blocked else (0, 1)
        for bit in possible_bits:
            candidate = (*bits, bit)
            if can_work_bits(candidate, max_consecutive, rest_after_max):
                if max_work_hours is None or sum(candidate) <= max_work_hours:
                    visit(position + 1, candidate)

    visit(0, ())
    return patterns


def slots_from_bits(shift: ShiftRule, bits: tuple[int, ...]) -> tuple[int, ...]:
    return tuple(
        hour_index(shift.start_hour + offset)
        for offset, bit in enumerate(bits)
        if bit
    )


def fmp_shift_candidates(request: CalculatorRequest) -> list[str]:
    if not request.include_fmp:
        return []
    active_shift_codes = {shift.code for shift in enabled_shift_rules(request.settings.shifts)}
    if request.fmp_shift_mode == "fixed":
        fmp_shift = (request.fmp_shift or DEFAULT_FMP_SHIFT).strip() or DEFAULT_FMP_SHIFT
        return [fmp_shift] if fmp_shift in active_shift_codes else []
    return [shift_code for shift_code in FMP_AUTO_SHIFT_CODES if shift_code in active_shift_codes]


def _role_requirements(request: CalculatorRequest) -> list[PatternRequirement]:
    requirements: list[PatternRequirement] = []
    if request.settings.include_required_shift_leaders:
        requirements.append(PatternRequirement("FL", "A7", "V1", 1, request.settings.v1_sector_limit))
        requirements.append(PatternRequirement("FL", "A14", "V2", 1, request.settings.v2_sector_limit))
        requirements.append(PatternRequirement("FL", "V3", "V3", 1, request.settings.v3_sector_limit))

    night_without_v3 = (
        max(
            0,
            request.settings.required_night_fl_count
            - (1 if request.settings.include_required_shift_leaders else 0),
        )
        if request.settings.include_night_fl_requirement
        else 0
    )
    if night_without_v3 > 0:
        requirements.append(PatternRequirement("FL", "A21", None, night_without_v3, None))

    if request.include_fmp:
        candidate_shifts = fmp_shift_candidates(request)
        if request.fmp_shift_mode == "auto":
            required_group = "FL:FMP:auto"
            for index, fmp_shift in enumerate(candidate_shifts):
                requirements.append(
                    PatternRequirement(
                        "FL",
                        fmp_shift,
                        "FMP",
                        1 if index == 0 else 0,
                        request.settings.fmp_sector_limit,
                        required_group=required_group,
                        exact_count=1 if index == 0 else None,
                    )
                )
        elif candidate_shifts:
            fmp_shift = candidate_shifts[0]
            requirements.append(
                PatternRequirement(
                    "FL",
                    fmp_shift,
                    "FMP",
                    1,
                    request.settings.fmp_sector_limit,
                    exact_count=1,
                )
            )
    return requirements


def pattern_rule_signature(request: CalculatorRequest) -> str:
    fmp_candidates = ",".join(fmp_shift_candidates(request)) or "-"
    shift_signature = ",".join(
        f"{shift.code}:{shift.start_hour}:{shift.duration_hours}:{int(shift.enabled)}"
        for shift in request.settings.shifts
    )
    return (
        f"max{request.settings.max_consecutive_work_hours}"
        f"_rest{request.settings.rest_after_max_consecutive_hours}"
        f"_night{request.settings.required_night_fl_count}"
        f"_nightEnabled{int(request.settings.include_night_fl_requirement)}"
        f"_leaders{int(request.settings.include_required_shift_leaders)}"
        f"_fmp{int(request.include_fmp)}"
        f"_fmpMode{request.fmp_shift_mode}"
        f"_fmpShift{(request.fmp_shift or DEFAULT_FMP_SHIFT).strip() or DEFAULT_FMP_SHIFT}"
        f"_fmpCandidates{fmp_candidates}"
        f"_v1{request.settings.v1_sector_limit}"
        f"_v2{request.settings.v2_sector_limit}"
        f"_v3{request.settings.v3_sector_limit}"
        f"_fmplimit{request.settings.fmp_sector_limit}"
        "_leaderEdgeRules1"
        "_exactCover1"
        f"_{shift_signature}"
    )


def pattern_cache_path() -> Path:
    configured = os.environ.get(PATTERN_CACHE_ENV)
    if configured:
        return Path(configured)
    return DEFAULT_PATTERN_CACHE_PATH


def _pattern_to_dict(pattern: WorkPattern) -> dict[str, object]:
    return {
        "code": pattern.code,
        "shift": pattern.shift,
        "license": pattern.license,
        "role": pattern.role,
        "slots": list(pattern.slots),
        "work_hours": pattern.work_hours,
        "required_group": pattern.required_group,
    }


def _pattern_from_dict(data: dict[str, object]) -> WorkPattern:
    return WorkPattern(
        code=str(data["code"]),
        shift=str(data["shift"]),
        license=str(data["license"]),
        role=data["role"] if isinstance(data.get("role"), str) else None,
        slots=tuple(int(slot) for slot in data.get("slots", [])),
        work_hours=int(data["work_hours"]),
        required_group=data["required_group"] if isinstance(data.get("required_group"), str) else None,
    )


def _library_to_dict(library: PatternLibrary) -> dict[str, object]:
    return {
        "version": 2,
        "rule_signature": library.rule_signature,
        "generated_at_seconds": library.generated_at_seconds,
        "required_group_counts": library.required_group_counts,
        "exact_group_counts": library.exact_group_counts,
        "patterns": [_pattern_to_dict(pattern) for pattern in library.patterns],
    }


def _library_from_dict(data: dict[str, object], cache_path: Path) -> PatternLibrary:
    required_group_counts = {
        str(key): int(value)
        for key, value in dict(data.get("required_group_counts", {})).items()
    }
    exact_group_counts = {
        str(key): int(value)
        for key, value in dict(data.get("exact_group_counts", {})).items()
    }
    return PatternLibrary(
        patterns=[
            _pattern_from_dict(item)
            for item in data.get("patterns", [])
            if isinstance(item, dict)
        ],
        required_group_counts=required_group_counts,
        exact_group_counts=exact_group_counts,
        generated_at_seconds=float(data.get("generated_at_seconds", 0.0)),
        rule_signature=str(data["rule_signature"]),
        cache_status="hit",
        cache_path=str(cache_path),
    )


def _read_cached_pattern_library(signature: str, cache_path: Path) -> PatternLibrary | None:
    try:
        with cache_path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("rule_signature") != signature:
        return None
    return _library_from_dict(data, cache_path)


def _write_cached_pattern_library(library: PatternLibrary, cache_path: Path) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with cache_path.open("w", encoding="utf-8") as handle:
            json.dump(_library_to_dict(library), handle, ensure_ascii=False, separators=(",", ":"))
    except OSError:
        return


def build_pattern_library(request: CalculatorRequest) -> PatternLibrary:
    started_at = monotonic()
    max_consecutive = request.settings.max_consecutive_work_hours
    rest_after_max = request.settings.rest_after_max_consecutive_hours
    shifts = {shift.code: shift for shift in [*request.settings.shifts, MANDATORY_V3_SHIFT]}
    demanded_shifts = enabled_shift_rules(request.settings.shifts)

    patterns: list[WorkPattern] = []
    required_group_counts: dict[str, int] = {}
    exact_group_counts: dict[str, int] = {}

    def add_pattern_set(
        license_name: str,
        shift: ShiftRule,
        role: str | None,
        *,
        required_group: str | None = None,
        max_work_hours: int | None = None,
        include_zero: bool = False,
    ) -> None:
        bit_patterns = generate_shift_patterns(
            shift,
            max_consecutive,
            rest_after_max,
            max_work_hours=max_work_hours,
            include_zero=include_zero,
            blocked_offsets=blocked_role_offsets(role, shift),
        )
        for pattern_index, bits in enumerate(bit_patterns):
            slots = slots_from_bits(shift, bits)
            patterns.append(
                WorkPattern(
                    code=f"{license_name}:{shift.code}:{role or '-'}:{pattern_index}",
                    shift=shift.code,
                    license=license_name,
                    role=role,
                    slots=slots,
                    work_hours=len(slots),
                    required_group=required_group,
                )
            )

    for shift in demanded_shifts:
        for license_name in LICENSES:
            add_pattern_set(license_name, shift, None)

    for requirement in _role_requirements(request):
        shift = shifts.get(requirement.shift)
        if shift is None:
            continue
        required_group = requirement.required_group or f"{requirement.license}:{requirement.shift}:{requirement.role or '-'}"
        if requirement.required_count > 0:
            required_group_counts[required_group] = required_group_counts.get(required_group, 0) + requirement.required_count
        if requirement.exact_count is not None:
            exact_group_counts[required_group] = requirement.exact_count
        add_pattern_set(
            requirement.license,
            shift,
            requirement.role,
            required_group=required_group,
            max_work_hours=requirement.max_work_hours,
            include_zero=True,
        )

    return PatternLibrary(
        patterns=patterns,
        required_group_counts=required_group_counts,
        exact_group_counts=exact_group_counts,
        generated_at_seconds=round(monotonic() - started_at, 3),
        rule_signature=pattern_rule_signature(request),
        cache_status="generated",
        cache_path=None,
    )


def load_or_build_pattern_library(request: CalculatorRequest) -> PatternLibrary:
    signature = pattern_rule_signature(request)
    cache_path = pattern_cache_path()
    cached = _read_cached_pattern_library(signature, cache_path)
    if cached is not None:
        return cached
    library = build_pattern_library(request)
    _write_cached_pattern_library(library, cache_path)
    return PatternLibrary(
        patterns=library.patterns,
        required_group_counts=library.required_group_counts,
        exact_group_counts=library.exact_group_counts,
        generated_at_seconds=library.generated_at_seconds,
        rule_signature=library.rule_signature,
        cache_status="generated",
        cache_path=str(cache_path),
    )


def pattern_library_profile(request: CalculatorRequest, *, regenerate: bool = False) -> dict[str, object]:
    if regenerate:
        cache_path = pattern_cache_path()
        library = build_pattern_library(request)
        _write_cached_pattern_library(library, cache_path)
        library = PatternLibrary(
            patterns=library.patterns,
            required_group_counts=library.required_group_counts,
            exact_group_counts=library.exact_group_counts,
            generated_at_seconds=library.generated_at_seconds,
            rule_signature=library.rule_signature,
            cache_status="regenerated",
            cache_path=str(cache_path),
        )
    else:
        library = load_or_build_pattern_library(request)

    by_shift: Counter[str] = Counter(pattern.shift for pattern in library.patterns)
    by_license: Counter[str] = Counter(pattern.license for pattern in library.patterns)
    by_role: Counter[str] = Counter(pattern.role or "regular" for pattern in library.patterns)
    return {
        "rule_signature": library.rule_signature,
        "pattern_count": library.pattern_count,
        "cache_status": library.cache_status,
        "cache_path": library.cache_path,
        "generated_at_seconds": library.generated_at_seconds,
        "required_group_counts": library.required_group_counts,
        "exact_group_counts": library.exact_group_counts,
        "patterns_by_shift": dict(sorted(by_shift.items())),
        "patterns_by_license": {license_name: by_license[license_name] for license_name in LICENSES},
        "patterns_by_role": dict(sorted(by_role.items())),
    }


def _shift_demand_score(shift: ShiftRule, target_sector_counts: list[int]) -> int:
    if len(target_sector_counts) != HOURS_IN_DAY:
        return 0
    return sum(target_sector_counts[slot] for slot in shift_slots(shift))


def estimate_pattern_search_seconds(pattern_count: int, lower_bound: int, upper_bound: int) -> tuple[int, int]:
    limits = max(1, upper_bound - lower_bound + 1)
    base = max(2.0, pattern_count / 850)
    low = math.ceil(base * min(limits, 6) * 0.6)
    high = math.ceil(base * min(limits, 10) * 2.4)
    return max(1, low), max(low + 1, high)


def _license_ratio_targets(request: CalculatorRequest) -> dict[str, int]:
    if request.license_mix_percent is not None:
        return {
            "FL": max(0, request.license_mix_percent.fl),
            "APS": max(0, request.license_mix_percent.aps),
            "ACS": max(0, request.license_mix_percent.acs),
        }
    ratio = {
        "FL": max(0, request.fl_count),
        "APS": max(0, request.aps_count),
        "ACS": max(0, request.acs_count),
    }
    if sum(ratio.values()) <= 0:
        return {"FL": 1, "APS": 0, "ACS": 1}
    return ratio


def _is_vi_role(role: str | None) -> bool:
    return (role or "").upper() in {"V1", "V2", "V3"}


def _minimum_people_lower_bound(
    library: PatternLibrary,
    request: CalculatorRequest,
    target_sector_counts: list[int],
) -> int:
    requested_seat_hours = 2 * sum(target_sector_counts)
    max_pattern_capacity = max((pattern.work_hours for pattern in library.patterns), default=1)
    capacity_bound = math.ceil(requested_seat_hours / max(1, max_pattern_capacity))
    peak_bound = max((2 * sector_count for sector_count in target_sector_counts), default=0)
    required_bound = sum(library.required_group_counts.values())
    return max(1, capacity_bound, peak_bound, required_bound)


def _solve_pattern_model(
    library: PatternLibrary,
    request: CalculatorRequest,
    target_sector_counts: list[int],
    people_limit: int,
    *,
    optimize_quality: bool,
    cancel_callback: CancelCallback | None = None,
) -> PatternSolveResult | None:
    _check_cancel(cancel_callback)
    model = cp_model.CpModel()
    upper = max(0, people_limit)
    counts = [
        model.NewIntVar(0, upper, f"count_{index}_{pattern.code}")
        for index, pattern in enumerate(library.patterns)
    ]
    selected_total = sum(counts)
    model.Add(selected_total <= people_limit)

    for required_group, required_count in library.required_group_counts.items():
        model.Add(
            sum(
                counts[index]
                for index, pattern in enumerate(library.patterns)
                if pattern.required_group == required_group
            )
            >= required_count
        )
    for exact_group, exact_count in library.exact_group_counts.items():
        model.Add(
            sum(
                counts[index]
                for index, pattern in enumerate(library.patterns)
                if pattern.required_group == exact_group
            )
            == exact_count
        )

    slot_assignment_vars: dict[int, dict[str, cp_model.IntVar | int]] = {}
    flexible_fl_terms: list[cp_model.LinearExpr] = []
    fmp_vi_overlap_terms: list[cp_model.LinearExpr] = []
    fmp_vi_overlap_counts: list[cp_model.IntVar] = []
    for slot, sector_count in enumerate(target_sector_counts):
        requirements = sector_seat_requirements(sector_count)
        available = {
            license_name: sum(
                counts[index]
                for index, pattern in enumerate(library.patterns)
                if pattern.license == license_name and slot in pattern.slots
            )
            for license_name in LICENSES
        }

        all_fl = requirements["all"]
        if requirements["lower"] > 0:
            aps_lower = model.NewIntVar(0, requirements["lower"], f"aps_lower_{slot}")
            fl_lower = model.NewIntVar(0, requirements["lower"], f"fl_lower_{slot}")
            model.Add(aps_lower + fl_lower == requirements["lower"])
        else:
            aps_lower = 0
            fl_lower = 0

        if requirements["above"] > 0:
            acs_above = model.NewIntVar(0, requirements["above"], f"acs_above_{slot}")
            fl_above = model.NewIntVar(0, requirements["above"], f"fl_above_{slot}")
            model.Add(acs_above + fl_above == requirements["above"])
        else:
            acs_above = 0
            fl_above = 0

        model.Add(all_fl + fl_lower + fl_above == available["FL"])
        model.Add(aps_lower == available["APS"])
        model.Add(acs_above == available["ACS"])
        slot_assignment_vars[slot] = {
            "all_fl": all_fl,
            "aps_lower": aps_lower,
            "fl_lower": fl_lower,
            "acs_above": acs_above,
            "fl_above": fl_above,
        }
        flexible_fl_terms.extend([fl_lower, fl_above])

        fmp_slot_pattern_indexes = [
            index
            for index, pattern in enumerate(library.patterns)
            if pattern.role == "FMP" and slot in pattern.slots
        ]
        vi_slot_pattern_indexes = [
            index
            for index, pattern in enumerate(library.patterns)
            if _is_vi_role(pattern.role) and slot in pattern.slots
        ]
        if fmp_slot_pattern_indexes and vi_slot_pattern_indexes:
            fmp_on_slot = sum(counts[index] for index in fmp_slot_pattern_indexes)
            vi_on_slot = sum(counts[index] for index in vi_slot_pattern_indexes)
            overlap_count = model.NewIntVar(0, people_limit, f"fmp_vi_overlap_{slot}")
            model.Add(overlap_count <= vi_on_slot)
            model.Add(overlap_count <= people_limit * fmp_on_slot)
            model.Add(overlap_count >= vi_on_slot - people_limit * (1 - fmp_on_slot))
            fmp_vi_overlap_counts.append(overlap_count)
            fmp_vi_overlap_terms.append(FMP_LEADER_OVERLAP_PENALTY * overlap_count)

    if fmp_vi_overlap_counts:
        if request.leader_exception_mode == "allow":
            model.Add(sum(fmp_vi_overlap_counts) <= request.max_leader_exception_hours)
        else:
            model.Add(sum(fmp_vi_overlap_counts) == 0)

    if optimize_quality:
        ratio_targets = _license_ratio_targets(request)
        target_total = sum(ratio_targets.values())
        selected_by_license = {
            license_name: sum(
                counts[index]
                for index, pattern in enumerate(library.patterns)
                if pattern.license == license_name
            )
            for license_name in LICENSES
        }
        deviation_terms: list[cp_model.LinearExpr] = []
        for license_name, target_count in ratio_targets.items():
            deviation = model.NewIntVar(0, max(1, people_limit * target_total * 2), f"dev_{license_name}")
            model.AddAbsEquality(deviation, target_total * selected_by_license[license_name] - target_count * selected_total)
            deviation_terms.append(deviation)

        selected_capacity = sum(pattern.work_hours * counts[index] for index, pattern in enumerate(library.patterns))
        selected_fl = selected_by_license["FL"]
        role_terms = [
            counts[index]
            for index, pattern in enumerate(library.patterns)
            if pattern.role is not None
        ]
        model.Minimize(
            sum(deviation_terms) * 10_000
            + sum(flexible_fl_terms) * (750 if request.prefer_minimal_fl else 80)
            + sum(fmp_vi_overlap_terms)
            + selected_fl * (250 if request.prefer_minimal_fl else 5)
            + selected_capacity * 2
            + sum(role_terms) * 20
        )

    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = CP_SAT_WORKERS
    solver.parameters.random_seed = request.solver_random_seed
    stop_event = Event()
    monitor: Thread | None = None
    if cancel_callback is not None:
        def watch_cancel() -> None:
            while not stop_event.wait(0.25):
                if cancel_callback():
                    solver.StopSearch()
                    return

        monitor = Thread(target=watch_cancel, daemon=True)
        monitor.start()

    try:
        status = solver.Solve(model)
    finally:
        stop_event.set()
        if monitor is not None:
            monitor.join(timeout=1)

    if cancel_callback is not None and cancel_callback():
        raise PatternSearchCancelled("Izračun je bil preklican.")

    if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
        return None

    selected_counts = {
        index: int(solver.Value(count_var))
        for index, count_var in enumerate(counts)
        if solver.Value(count_var) > 0
    }
    assignment_counts: dict[int, dict[str, int]] = {}
    for slot, vars_by_name in slot_assignment_vars.items():
        assignment_counts[slot] = {
            name: int(value if isinstance(value, int) else solver.Value(value))
            for name, value in vars_by_name.items()
        }

    return PatternSolveResult(
        status=solver.StatusName(status),
        selected_counts=selected_counts,
        slot_assignment_counts=assignment_counts,
        objective_value=solver.ObjectiveValue() if optimize_quality else None,
        best_objective_bound=solver.BestObjectiveBound() if optimize_quality else None,
    )


@dataclass
class _PatternPerson:
    id: str
    license: str
    shift: str
    role: str | None
    slots: set[int]
    sector_hours: int = 0


def _label_for_person(number: int) -> str:
    label = ""
    n = number
    while True:
        label = chr(ord("A") + (n % 26)) + label
        n = n // 26 - 1
        if n < 0:
            return label


def _expand_people(library: PatternLibrary, selected_counts: dict[int, int]) -> list[_PatternPerson]:
    people: list[_PatternPerson] = []
    for pattern_index, count in sorted(selected_counts.items()):
        pattern = library.patterns[pattern_index]
        for _ in range(count):
            people.append(
                _PatternPerson(
                    id=_label_for_person(len(people)),
                    license=pattern.license,
                    shift=pattern.shift,
                    role=pattern.role,
                    slots=set(pattern.slots),
                )
            )
    return people


def _take_workers(
    people: list[_PatternPerson],
    slot: int,
    license_name: str,
    count: int,
    used_this_slot: set[str],
) -> list[_PatternPerson]:
    candidates = [
        person
        for person in people
        if person.license == license_name and slot in person.slots and person.id not in used_this_slot
    ]
    candidates.sort(key=lambda person: (person.sector_hours, person.role is not None, person.shift, person.id))
    chosen = candidates[:count]
    if len(chosen) < count:
        raise RuntimeError("Exact-cover rešitev ni uspela sestaviti urnega prikaza.")
    for person in chosen:
        used_this_slot.add(person.id)
        person.sector_hours += 1
    return chosen


def _build_hourly_coverage(
    people: list[_PatternPerson],
    assignment_counts: dict[int, dict[str, int]],
    target_sector_counts: list[int],
    max_sectors_per_hour: int,
) -> list[HourlyCoverage]:
    display_sector_names = sector_display_names_for_max(max_sectors_per_hour)
    hourly_coverage: list[HourlyCoverage] = []
    for slot, sector_count in enumerate(target_sector_counts):
        used_this_slot: set[str] = set()
        assignments_by_sector: dict[str, SectorAssignment] = {}
        counts = assignment_counts.get(slot, {})
        all_fl = counts.get("all_fl", 0)
        aps_lower = counts.get("aps_lower", 0)
        fl_lower = counts.get("fl_lower", 0)
        acs_above = counts.get("acs_above", 0)
        fl_above = counts.get("fl_above", 0)

        def worker_pair(license_name: str) -> tuple[str, str]:
            workers = _take_workers(people, slot, license_name, 2, used_this_slot)
            return workers[0].id, workers[1].id

        def mixed_pair(primary_license: str, primary_count: int, fallback_license: str, fallback_count: int) -> tuple[str, str]:
            workers = [
                *_take_workers(people, slot, primary_license, primary_count, used_this_slot),
                *_take_workers(people, slot, fallback_license, fallback_count, used_this_slot),
            ]
            if len(workers) != 2:
                raise RuntimeError("Exact-cover rešitev ni uspela sestaviti para za sektor.")
            return workers[0].id, workers[1].id

        for sector_name in sector_names_for_count(sector_count):
            if sector_name == "ALL":
                if all_fl < 2:
                    raise RuntimeError("ALL sektor nima dveh FL v exact-cover rešitvi.")
                lower_worker, upper_worker = worker_pair("FL")
                all_fl -= 2
            elif sector_name == "LOWER":
                lower_worker, upper_worker = mixed_pair("APS", min(2, aps_lower), "FL", min(2 - min(2, aps_lower), fl_lower))
                used_aps = min(2, aps_lower)
                aps_lower -= used_aps
                fl_lower -= 2 - used_aps
            else:
                used_acs = min(2, acs_above)
                lower_worker, upper_worker = mixed_pair("ACS", used_acs, "FL", min(2 - used_acs, fl_above))
                acs_above -= used_acs
                fl_above -= 2 - used_acs
            assignments_by_sector[sector_name] = SectorAssignment(
                sector_name=sector_name,
                lower_worker=lower_worker,
                upper_worker=upper_worker,
            )

        workers = [
            worker_id
            for assignment in assignments_by_sector.values()
            for worker_id in (assignment.lower_worker, assignment.upper_worker)
        ]
        hourly_coverage.append(
            HourlyCoverage(
                hour=hour_label(slot),
                open_sectors=len(assignments_by_sector),
                workers=workers,
                sector_workers=[assignments_by_sector.get(sector_name) for sector_name in display_sector_names],
            )
        )
    return hourly_coverage


def _shift_summary(people: list[_PatternPerson]) -> list[ShiftSummary]:
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for person in people:
        key = f"{person.role}/{display_shift(person.shift, person.role)}" if person.role else display_shift(person.shift, person.role)
        counts[key][person.license] += 1
    summaries: list[ShiftSummary] = []
    for shift, counter in sorted(counts.items()):
        summaries.append(
            ShiftSummary(
                shift=shift,
                fl=counter["FL"],
                aps=counter["APS"],
                acs=counter["ACS"],
                total=sum(counter.values()),
            )
        )
    return summaries


def _response_from_pattern_solution(
    library: PatternLibrary,
    solve_result: PatternSolveResult,
    request: CalculatorRequest,
    target_sector_counts: list[int],
    notes: list[str],
    warnings: list[str],
    *,
    proven_minimum: bool,
    search_steps: list[PatternSearchStep],
) -> CalculatorResponse:
    people = _expand_people(library, solve_result.selected_counts)
    hourly_coverage = _build_hourly_coverage(
        people,
        solve_result.slot_assignment_counts,
        target_sector_counts,
        request.settings.max_sectors_per_hour,
    )
    requested_sector_hours = sum(target_sector_counts)
    selected_capacity = sum(len(person.slots) for person in people)
    scheduled_person_hours = sum(person.sector_hours for person in people)
    unused_people = len([person for person in people if person.sector_hours == 0])
    utilization_percent = round((scheduled_person_hours / selected_capacity) * 100) if selected_capacity > 0 else 0
    people_by_id = {person.id: person for person in people}
    role_by_id = {person.id: (person.role or "").upper() for person in people}
    fmp_vi_overlap_hours = 0
    for coverage in hourly_coverage:
        fmp_count = sum(1 for worker_id in coverage.workers if role_by_id.get(worker_id) == "FMP")
        vi_count = sum(1 for worker_id in coverage.workers if role_by_id.get(worker_id) in {"V1", "V2", "V3"})
        fmp_vi_overlap_hours += fmp_count * vi_count
    response_people = [
        VirtualPerson(
            id=person.id,
            license=person.license,
            shift=display_shift(person.shift, person.role),
            role=person.role,
            sector_hours=person.sector_hours,
            max_sector_hours=len(person.slots),
            utilization_percent=round((person.sector_hours / len(person.slots)) * 100) if person.slots else 0,
            used_as_sector_controller=person.sector_hours > 0,
            source="pattern-core",
        )
        for person in sorted(people_by_id.values(), key=lambda item: item.id)
    ]
    failed_steps = [step for step in search_steps if step.status == "INFEASIBLE"]
    notes.extend(
        [
            "Uporabljeno je eksperimentalno exact-cover jedro: izbrani legalni work/rest vzorci pomenijo dejanske sektorske ure, ne samo razpoložljivosti.",
            (
                f"Generator je uporabil {library.pattern_count} legalnih vzorcev "
                f"({ 'cache' if library.cache_status == 'hit' else 'na novo generirano' })."
            ),
            "FL/APS/ACS vnosi se v minimum načinu berejo kot razmerje licenc, ne kot trde zgornje meje.",
            f"Pred najdeno rešitvijo je bilo dokazano neizvedljivih {len(failed_steps)} manjših limitov ljudi.",
            "CP-SAT pri tem jedru nima časovne omejitve; izračun se konča z dokazom, izvedljivo rešitvijo ali uporabniškim preklicem.",
        ]
    )
    if proven_minimum:
        notes.append(f"Minimum je dokazan pri {len(people)} ljudeh za {requested_sector_hours}/{requested_sector_hours} sektorskih ur.")
    else:
        warnings.append("Najdena je izvedljiva rešitev, vendar minimum ni dokazan za vse manjše limite.")

    return CalculatorResponse(
        feasible=True,
        max_sector_hours=requested_sector_hours,
        requested_sector_hours=requested_sector_hours,
        solver_upper_bound_sector_hours=requested_sector_hours,
        solver_gap_to_upper_bound=0 if proven_minimum else None,
        solver_status=solve_result.status,
        solver_optimality_gap_percent=None,
        leader_edge_exception_hours=0,
        fmp_vi_overlap_hours=fmp_vi_overlap_hours,
        crisis_exception_hours=fmp_vi_overlap_hours,
        missing_sector_hours=0,
        baseline_min_people=len(people),
        baseline_min_people_formula="Exact-cover pattern model je preverjal limite ljudi po vrsti in dokazoval neizvedljivost manjših limitov.",
        minimum_required_fl=sum(1 for person in people if person.license == "FL"),
        planned_people=len(people),
        active_people=len(people) - unused_people,
        unused_people=unused_people,
        scheduled_person_hours=scheduled_person_hours,
        total_person_capacity_hours=selected_capacity,
        utilization_percent=utilization_percent,
        people=response_people,
        shift_summary=_shift_summary(people),
        hourly_coverage=hourly_coverage,
        pareto_points=[],
        notes=notes,
        warnings=warnings,
    )


def calculate_pattern_minimum(
    request: CalculatorRequest,
    progress_callback: ProgressCallback | None = None,
    cancel_callback: CancelCallback | None = None,
) -> CalculatorResponse:
    target_sector_counts = list(request.requested_sector_counts or [request.settings.max_sectors_per_hour] * HOURS_IN_DAY)
    requested_sector_hours = sum(target_sector_counts)
    notes: list[str] = []
    warnings: list[str] = []

    _report_pattern(
        progress_callback,
        12,
        "library_start",
        "Eksperimentalni core pripravlja legalne work/rest vzorce.",
    )
    library = load_or_build_pattern_library(request)
    lower_bound = _minimum_people_lower_bound(library, request, target_sector_counts)
    upper_bound = min(80, max(lower_bound, request.total_people if request.total_people > 0 else 80))
    estimate_low, estimate_high = estimate_pattern_search_seconds(library.pattern_count, lower_bound, upper_bound)
    cache_text = "prebrano iz cache baze" if library.cache_status == "hit" else "na novo generirano in shranjeno"
    _report_pattern(
        progress_callback,
        18,
        "library_ready",
        f"Vzorcev: {library.pattern_count} ({cache_text}). Spodnja meja {lower_bound}; groba ocena {estimate_low}-{estimate_high} s.",
        pattern_count=library.pattern_count,
        cache_status=library.cache_status,
        cache_path=library.cache_path,
        lower_bound=lower_bound,
        upper_bound=upper_bound,
        estimate_low_seconds=estimate_low,
        estimate_high_seconds=estimate_high,
    )
    if requested_sector_hours <= 0:
        return CalculatorResponse(
            feasible=True,
            max_sector_hours=0,
            requested_sector_hours=0,
            missing_sector_hours=0,
            baseline_min_people=0,
            baseline_min_people_formula="Ni zahtevanih sektorskih ur.",
            minimum_required_fl=0,
            planned_people=0,
            active_people=0,
            unused_people=0,
            scheduled_person_hours=0,
            total_person_capacity_hours=0,
            utilization_percent=0,
            people=[],
            shift_summary=[],
            hourly_coverage=[
                HourlyCoverage(hour=hour_label(slot), open_sectors=0, workers=[], sector_workers=[])
                for slot in range(HOURS_IN_DAY)
            ],
            notes=["Ni zahtevanih sektorskih ur."],
            warnings=[],
        )

    search_steps: list[PatternSearchStep] = []
    feasible_result: PatternSolveResult | None = None
    feasible_people_limit: int | None = None
    total_limits = max(1, upper_bound - lower_bound + 1)

    for position, people_limit in enumerate(range(lower_bound, upper_bound + 1), start=1):
        _check_cancel(cancel_callback)
        progress = 20 + round((position - 1) / total_limits * 55)
        _report_pattern(
            progress_callback,
            progress,
            "limit_start",
            f"Preverjam dokazljivost pri {people_limit} ljudeh ({position}/{total_limits}); brez časovne omejitve, lahko prekineš.",
            people_limit=people_limit,
            lower_bound=lower_bound,
            upper_bound=upper_bound,
            limit_index=position,
            limit_count=total_limits,
        )
        started_at = monotonic()
        result = _solve_pattern_model(
            library,
            request,
            target_sector_counts,
            people_limit,
            optimize_quality=False,
            cancel_callback=cancel_callback,
        )
        elapsed = round(monotonic() - started_at, 1)
        if result is None:
            search_steps.append(
                PatternSearchStep(
                    people_limit=people_limit,
                    status="INFEASIBLE",
                    elapsed_seconds=elapsed,
                    message=f"{people_limit} ljudi je dokazano neizvedljivo.",
                )
            )
            _report_pattern(
                progress_callback,
                progress + 1,
                "limit_done",
                f"{people_limit} ljudi: dokazano neizvedljivo ({elapsed:.1f} s).",
                people_limit=people_limit,
                limit_status="INFEASIBLE",
                elapsed_seconds=elapsed,
            )
            continue

        search_steps.append(
            PatternSearchStep(
                people_limit=people_limit,
                status=result.status,
                elapsed_seconds=elapsed,
                message=f"{people_limit} ljudi je izvedljivo.",
            )
        )
        feasible_result = result
        feasible_people_limit = people_limit
        _report_pattern(
            progress_callback,
            78,
            "limit_done",
            f"{people_limit} ljudi je izvedljivo; optimiziram sestavo znotraj dokazanega minimuma.",
            people_limit=people_limit,
            limit_status=result.status,
            elapsed_seconds=elapsed,
        )
        break

    if feasible_result is None or feasible_people_limit is None:
        warnings.append(f"Exact-cover pattern model ni našel izvedljive rešitve do {upper_bound} ljudi.")
        return CalculatorResponse(
            feasible=False,
            max_sector_hours=0,
            requested_sector_hours=requested_sector_hours,
            missing_sector_hours=requested_sector_hours,
            baseline_min_people=lower_bound,
            baseline_min_people_formula="Spodnja meja iz peak potrebe, kapacitete vzorcev in obveznih vlog.",
            minimum_required_fl=0,
            planned_people=0,
            active_people=0,
            unused_people=0,
            scheduled_person_hours=0,
            total_person_capacity_hours=0,
            utilization_percent=0,
            people=[],
            shift_summary=[],
            hourly_coverage=[
                HourlyCoverage(hour=hour_label(slot), open_sectors=0, workers=[], sector_workers=[])
                for slot in range(HOURS_IN_DAY)
            ],
            notes=[
                "Eksperimentalni exact-cover model je dokazal neizvedljivost vseh preverjenih limitov.",
                f"Preverjeni limiti: {lower_bound}-{upper_bound}.",
            ],
            warnings=warnings,
        )

    _check_cancel(cancel_callback)
    _report_pattern(
        progress_callback,
        82,
        "quality",
        "Pri minimalnem limitu optimiziram razmerje licenc in uporabo FL.",
        people_limit=feasible_people_limit,
    )
    quality_result = _solve_pattern_model(
        library,
        request,
        target_sector_counts,
        feasible_people_limit,
        optimize_quality=True,
        cancel_callback=cancel_callback,
    ) or feasible_result

    _report_pattern(
        progress_callback,
        92,
        "assignment",
        "Iz exact-cover rešitve sestavljam imena oseb in urni prikaz sektorjev.",
        people_limit=feasible_people_limit,
    )
    proven_minimum = all(step.status == "INFEASIBLE" for step in search_steps[:-1])
    response = _response_from_pattern_solution(
        library,
        quality_result,
        request,
        target_sector_counts,
        notes,
        warnings,
        proven_minimum=proven_minimum,
        search_steps=search_steps,
    )
    _report_pattern(
        progress_callback,
        100,
        "finished",
        f"Eksperimentalni core je končal: minimum {response.planned_people} ljudi.",
        people_limit=response.planned_people,
        proven_minimum=proven_minimum,
    )
    return response

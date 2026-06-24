from __future__ import annotations

import math
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from time import perf_counter
from typing import Iterable

from . import calculator, pattern_core
from .config_library import (
    FOCUS_CONFIGURATION_NAMES,
    LICENSES,
    _manual_requested_sector_counts,
    configuration_columns,
    manual_configuration_audit,
    manual_schedule_for_configuration,
    parse_configuration,
    read_configuration_csv,
    selected_configuration_library_path,
    settings_for_manual_schedule_evaluation,
)
from .models import CalculatorRequest


SECTOR_ORDER = tuple(calculator.SECTOR_DISPLAY_ORDER)


@dataclass(frozen=True)
class SectorProfileRecord:
    configuration: str
    slot: int
    hour: str
    open_sector_count: int
    manual_profile: tuple[str, ...]
    daily_sector_hours: int = 0


def ordered_profile(sectors: Iterable[str]) -> tuple[str, ...]:
    sector_set = {str(sector).strip().upper() for sector in sectors if str(sector).strip()}
    known = [sector for sector in SECTOR_ORDER if sector in sector_set]
    extras = sorted(sector for sector in sector_set if sector not in SECTOR_ORDER)
    return tuple([*known, *extras])


def serializable_profiles(profiles: dict[int, list[str] | tuple[str, ...]]) -> dict[str, list[str]]:
    return {str(count): list(profile) for count, profile in sorted(profiles.items())}


def _manual_profile_for_hour(hour: object) -> tuple[str, ...]:
    if not isinstance(hour, dict):
        return ()
    sector_workers = hour.get("sector_workers")
    if not isinstance(sector_workers, list):
        return ()
    sectors: list[str] = []
    for assignment in sector_workers:
        if not isinstance(assignment, dict):
            continue
        sector_name = assignment.get("sector_name")
        lower_worker = str(assignment.get("lower_worker") or "").strip()
        upper_worker = str(assignment.get("upper_worker") or "").strip()
        if sector_name and (lower_worker or upper_worker):
            sectors.append(str(sector_name))
    return ordered_profile(sectors)


def collect_manual_sector_profile_records(names: list[str] | None = None) -> list[SectorProfileRecord]:
    selected_names = names or FOCUS_CONFIGURATION_NAMES
    records: list[SectorProfileRecord] = []
    for name in selected_names:
        manual_schedule = manual_schedule_for_configuration(name)
        if manual_schedule is None:
            continue
        hourly_coverage = manual_schedule.get("hourly_coverage")
        if not isinstance(hourly_coverage, list):
            continue
        daily_sector_hours = 0
        for hour in hourly_coverage:
            if not isinstance(hour, dict):
                continue
            try:
                daily_sector_hours += int(hour.get("open_sectors") or 0)
            except (TypeError, ValueError):
                continue
        for slot, hour in enumerate(hourly_coverage):
            if not isinstance(hour, dict):
                continue
            try:
                open_sector_count = int(hour.get("open_sectors") or 0)
            except (TypeError, ValueError):
                open_sector_count = 0
            if open_sector_count <= 0:
                continue
            profile = _manual_profile_for_hour(hour)
            records.append(
                SectorProfileRecord(
                    configuration=name,
                    slot=slot,
                    hour=str(hour.get("hour") or calculator.hour_label(slot)),
                    open_sector_count=open_sector_count,
                    manual_profile=profile,
                    daily_sector_hours=daily_sector_hours,
                )
            )
    return records


def manual_profile_stats(records: list[SectorProfileRecord]) -> dict[int, Counter[tuple[str, ...]]]:
    stats: dict[int, Counter[tuple[str, ...]]] = defaultdict(Counter)
    for record in records:
        stats[record.open_sector_count][record.manual_profile] += 1
    return dict(stats)


def _profile_distance(left: tuple[str, ...], right: tuple[str, ...]) -> int:
    return len(set(left) ^ set(right))


def recommend_sector_profiles(
    records: list[SectorProfileRecord],
    current_profiles: dict[int, list[str] | tuple[str, ...]] | None = None,
) -> dict[int, tuple[str, ...]]:
    current = {
        int(count): ordered_profile(profile)
        for count, profile in (current_profiles or calculator.SECTOR_PROFILES).items()
    }
    recommended = dict(current)
    for count, counter in manual_profile_stats(records).items():
        current_profile = current.get(count, ())
        profile, _frequency = sorted(
            counter.items(),
            key=lambda item: (-item[1], _profile_distance(item[0], current_profile), item[0]),
        )[0]
        recommended[count] = profile
    return recommended


def score_sector_profile_records(
    records: list[SectorProfileRecord],
    profiles: dict[int, list[str] | tuple[str, ...]],
) -> dict[str, object]:
    normalized_profiles = {int(count): ordered_profile(profile) for count, profile in profiles.items()}
    exact_matches = 0
    sector_distance = 0
    length_mismatches = 0
    by_count: dict[int, dict[str, int]] = defaultdict(
        lambda: {"hours": 0, "exact_matches": 0, "sector_distance": 0, "length_mismatches": 0}
    )
    by_configuration: dict[str, dict[str, int]] = defaultdict(
        lambda: {"hours": 0, "exact_matches": 0, "sector_distance": 0, "length_mismatches": 0}
    )
    examples: list[dict[str, object]] = []

    for record in records:
        expected_profile = normalized_profiles.get(record.open_sector_count, ())
        distance = _profile_distance(record.manual_profile, expected_profile)
        exact_match = record.manual_profile == expected_profile
        length_mismatch = len(record.manual_profile) != record.open_sector_count
        exact_matches += 1 if exact_match else 0
        sector_distance += distance
        length_mismatches += 1 if length_mismatch else 0

        for bucket in (by_count[record.open_sector_count], by_configuration[record.configuration]):
            bucket["hours"] += 1
            bucket["exact_matches"] += 1 if exact_match else 0
            bucket["sector_distance"] += distance
            bucket["length_mismatches"] += 1 if length_mismatch else 0

        if not exact_match and len(examples) < 24:
            examples.append(
                {
                    "configuration": record.configuration,
                    "hour": record.hour,
                    "open_sector_count": record.open_sector_count,
                    "manual_profile": list(record.manual_profile),
                    "model_profile": list(expected_profile),
                    "missing_sectors": sorted(set(record.manual_profile) - set(expected_profile)),
                    "extra_sectors": sorted(set(expected_profile) - set(record.manual_profile)),
                    "sector_distance": distance,
                }
            )

    total_hours = len(records)
    return {
        "total_hours": total_hours,
        "exact_matches": exact_matches,
        "exact_match_percent": round((exact_matches / total_hours) * 100, 1) if total_hours else 100.0,
        "profile_mismatch_hours": total_hours - exact_matches,
        "sector_distance": sector_distance,
        "length_mismatches": length_mismatches,
        "by_count": {str(count): dict(values) for count, values in sorted(by_count.items())},
        "by_configuration": {name: dict(values) for name, values in sorted(by_configuration.items())},
        "examples": examples,
    }


def score_preferred_sector_profiles(records: list[SectorProfileRecord]) -> dict[str, object]:
    profiles_by_record = {
        (record.configuration, record.slot): calculator.preferred_sector_profile_for_slot(
            record.slot,
            record.open_sector_count,
            record.daily_sector_hours or None,
        )
        for record in records
    }
    exact_matches = 0
    sector_distance = 0
    examples: list[dict[str, object]] = []
    for record in records:
        expected_profile = profiles_by_record[(record.configuration, record.slot)]
        distance = _profile_distance(record.manual_profile, expected_profile)
        exact_match = record.manual_profile == expected_profile
        exact_matches += 1 if exact_match else 0
        sector_distance += distance
        if not exact_match and len(examples) < 24:
            examples.append(
                {
                    "configuration": record.configuration,
                    "hour": record.hour,
                    "open_sector_count": record.open_sector_count,
                    "manual_profile": list(record.manual_profile),
                    "preferred_profile": list(expected_profile),
                    "missing_sectors": sorted(set(record.manual_profile) - set(expected_profile)),
                    "extra_sectors": sorted(set(expected_profile) - set(record.manual_profile)),
                    "sector_distance": distance,
                }
            )
    total_hours = len(records)
    return {
        "total_hours": total_hours,
        "exact_matches": exact_matches,
        "exact_match_percent": round((exact_matches / total_hours) * 100, 1) if total_hours else 100.0,
        "profile_mismatch_hours": total_hours - exact_matches,
        "sector_distance": sector_distance,
        "examples": examples,
    }


def _profile_stats_for_output(records: list[SectorProfileRecord]) -> dict[str, list[dict[str, object]]]:
    output: dict[str, list[dict[str, object]]] = {}
    for count, counter in sorted(manual_profile_stats(records).items()):
        output[str(count)] = [
            {
                "profile": list(profile),
                "hours": hours,
            }
            for profile, hours in counter.most_common()
        ]
    return output


def _profile_stats_by_slot_for_output(records: list[SectorProfileRecord]) -> dict[str, list[dict[str, object]]]:
    counters: dict[tuple[int, int, str], Counter[tuple[str, ...]]] = defaultdict(Counter)
    for record in records:
        counters[(record.open_sector_count, record.slot, record.hour)][record.manual_profile] += 1
    output: dict[str, list[dict[str, object]]] = defaultdict(list)
    for (count, slot, hour), counter in sorted(counters.items()):
        output[str(count)].append(
            {
                "slot": slot,
                "hour": hour,
                "profiles": [
                    {
                        "profile": list(profile),
                        "hours": hours,
                    }
                    for profile, hours in counter.most_common()
                ],
            }
        )
    return dict(output)


@contextmanager
def temporary_sector_profiles(profiles: dict[int, list[str] | tuple[str, ...]]):
    old_calculator_profiles = {count: list(profile) for count, profile in calculator.SECTOR_PROFILES.items()}
    old_pattern_profiles = {count: list(profile) for count, profile in pattern_core.SECTOR_PROFILES.items()}
    normalized_profiles = {int(count): list(ordered_profile(profile)) for count, profile in profiles.items()}
    try:
        calculator.SECTOR_PROFILES.clear()
        calculator.SECTOR_PROFILES.update(normalized_profiles)
        pattern_core.SECTOR_PROFILES.clear()
        pattern_core.SECTOR_PROFILES.update(normalized_profiles)
        yield
    finally:
        calculator.SECTOR_PROFILES.clear()
        calculator.SECTOR_PROFILES.update(old_calculator_profiles)
        pattern_core.SECTOR_PROFILES.clear()
        pattern_core.SECTOR_PROFILES.update(old_pattern_profiles)


def apply_sector_profiles(profiles: dict[int, list[str] | tuple[str, ...]]) -> dict[str, list[str]]:
    normalized_profiles = {int(count): list(ordered_profile(profile)) for count, profile in profiles.items()}
    calculator.SECTOR_PROFILES.clear()
    calculator.SECTOR_PROFILES.update(normalized_profiles)
    pattern_core.SECTOR_PROFILES.clear()
    pattern_core.SECTOR_PROFILES.update(normalized_profiles)
    return serializable_profiles(normalized_profiles)


def summarize_manual_audit(audit: dict[str, object]) -> dict[str, object]:
    rows = audit.get("rows")
    if not isinstance(rows, list):
        rows = []
    status_counts: Counter[str] = Counter()
    total_manual_sector_hours = 0
    total_model_sector_hours = 0
    total_missing_sector_hours = 0
    sector_mismatch_hours = 0
    sector_distance = 0
    per_configuration: list[dict[str, object]] = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        status_counts[str(row.get("status") or "unknown")] += 1
        manual_sector_hours = int(row.get("manual_sector_hours") or 0)
        model_sector_hours = int(row.get("model_sector_hours") or 0)
        missing_sector_hours = int(row.get("model_missing_sector_hours") or 0)
        total_manual_sector_hours += manual_sector_hours
        total_model_sector_hours += model_sector_hours
        total_missing_sector_hours += missing_sector_hours

        row_mismatch_hours = 0
        row_sector_distance = 0
        hourly_comparison = row.get("hourly_comparison")
        if isinstance(hourly_comparison, list):
            for hour in hourly_comparison:
                if not isinstance(hour, dict):
                    continue
                missing = hour.get("missing_sectors")
                extra = hour.get("extra_sectors")
                missing_count = len(missing) if isinstance(missing, list) else 0
                extra_count = len(extra) if isinstance(extra, list) else 0
                if missing_count or extra_count:
                    row_mismatch_hours += 1
                    row_sector_distance += missing_count + extra_count

        sector_mismatch_hours += row_mismatch_hours
        sector_distance += row_sector_distance
        per_configuration.append(
            {
                "name": row.get("name"),
                "status": row.get("status"),
                "manual_sector_hours": manual_sector_hours,
                "model_sector_hours": model_sector_hours,
                "missing_sector_hours": missing_sector_hours,
                "sector_mismatch_hours": row_mismatch_hours,
                "sector_distance": row_sector_distance,
                "solver_status": row.get("solver_status"),
                "elapsed_seconds": row.get("elapsed_seconds"),
            }
        )

    return {
        "elapsed_seconds": audit.get("elapsed_seconds"),
        "status_counts": dict(status_counts),
        "configuration_count": len(per_configuration),
        "total_manual_sector_hours": total_manual_sector_hours,
        "total_model_sector_hours": total_model_sector_hours,
        "total_missing_sector_hours": total_missing_sector_hours,
        "sector_mismatch_hours": sector_mismatch_hours,
        "sector_distance": sector_distance,
        "per_configuration": per_configuration,
    }


def _empty_counter_output(counter: Counter[str], total: int) -> list[dict[str, object]]:
    return [
        {
            "name": name,
            "count": count,
            "share_percent": round((count / total) * 100, 1) if total else 0,
        }
        for name, count in counter.most_common()
    ]


def analyze_focus_configuration_composition(names: list[str] | None = None) -> dict[str, object]:
    selected_names = names or FOCUS_CONFIGURATION_NAMES
    path = selected_configuration_library_path()
    if path is None:
        return {
            "configuration_count": len(selected_names),
            "status": "missing_library",
            "message": "Baza ročnih konfiguracij ni najdena.",
        }

    rows = read_configuration_csv(path)
    columns_by_name = {name: column_index for column_index, name in configuration_columns(rows)}
    supported_shifts = {shift.code for shift in calculator.DEFAULT_SHIFTS}
    regular_shift_hours = {shift.code: shift.duration_hours for shift in calculator.DEFAULT_SHIFTS}
    office_shift_hours = {shift.code: shift.duration_hours for shift in calculator.DEFAULT_OFFICER_SHIFTS}

    total_people = 0
    total_control_hours = 0
    total_office_people = 0
    total_office_hours = 0
    missing: list[str] = []
    unsupported: dict[str, list[str]] = {}
    license_counts: Counter[str] = Counter({license_name: 0 for license_name in LICENSES})
    shift_counts: Counter[str] = Counter()
    role_counts: Counter[str] = Counter()
    office_shift_counts: Counter[str] = Counter()
    per_configuration: list[dict[str, object]] = []

    for name in selected_names:
        column_index = columns_by_name.get(name)
        if column_index is None:
            missing.append(name)
            continue
        configuration = parse_configuration(rows, column_index, name, supported_shifts)
        if configuration.unsupported_rows:
            unsupported[name] = configuration.unsupported_rows

        configuration_shift_counts: Counter[str] = Counter()
        configuration_role_counts: Counter[str] = Counter()
        configuration_office_counts: Counter[str] = Counter()
        configuration_control_hours = 0
        configuration_office_hours = 0

        for license_name, count in configuration.license_counts.items():
            license_counts[str(license_name)] += int(count)

        for person in configuration.fixed_staff:
            count = int(person.count)
            shift = str(person.shift)
            role = str(person.role or "REGULAR").upper()
            shift_counts[shift] += count
            role_counts[role] += count
            configuration_shift_counts[shift] += count
            configuration_role_counts[role] += count
            configuration_control_hours += count * int(regular_shift_hours.get(shift, 0))

        for person in configuration.officer_staff:
            count = int(person.count)
            shift = str(person.shift)
            office_shift_counts[shift] += count
            configuration_office_counts[shift] += count
            configuration_office_hours += count * int(office_shift_hours.get(shift, 0))

        total_people += configuration.parsed_total
        total_control_hours += configuration_control_hours
        total_office_people += sum(configuration_office_counts.values())
        total_office_hours += configuration_office_hours
        per_configuration.append(
            {
                "name": name,
                "people": configuration.parsed_total,
                "license_counts": dict(configuration.license_counts),
                "shift_counts": dict(configuration_shift_counts),
                "role_counts": dict(configuration_role_counts),
                "office_shift_counts": dict(configuration_office_counts),
                "office_people": sum(configuration_office_counts.values()),
                "control_hours": configuration_control_hours,
                "office_hours": configuration_office_hours,
            }
        )

    return {
        "configuration_count": len(per_configuration),
        "requested_configuration_count": len(selected_names),
        "missing": missing,
        "unsupported": unsupported,
        "total_people": total_people,
        "average_people": round(total_people / len(per_configuration), 1) if per_configuration else 0,
        "total_control_hours": total_control_hours,
        "average_control_hours": round(total_control_hours / len(per_configuration), 1) if per_configuration else 0,
        "total_office_people": total_office_people,
        "total_office_hours": total_office_hours,
        "license_counts": dict(license_counts),
        "license_ratio_percent": {
            license_name: round((license_counts[license_name] / total_people) * 100, 1) if total_people else 0
            for license_name in LICENSES
        },
        "shift_mix": _empty_counter_output(shift_counts, sum(shift_counts.values())),
        "role_mix": _empty_counter_output(role_counts, sum(role_counts.values())),
        "office_shift_mix": _empty_counter_output(office_shift_counts, sum(office_shift_counts.values())),
        "per_configuration": per_configuration,
    }


def run_sector_profile_calibration(
    names: list[str] | None = None,
    *,
    time_limit_seconds: int = 3,
    run_solver: bool = True,
) -> dict[str, object]:
    selected_names = names or FOCUS_CONFIGURATION_NAMES
    records = collect_manual_sector_profile_records(selected_names)
    current_profiles = {count: tuple(profile) for count, profile in calculator.SECTOR_PROFILES.items()}
    recommended_profiles = recommend_sector_profiles(records, current_profiles)
    result: dict[str, object] = {
        "focus_names": selected_names,
        "manual_record_count": len(records),
        "current_profiles": serializable_profiles(current_profiles),
        "recommended_profiles": serializable_profiles(recommended_profiles),
        "manual_profile_stats": _profile_stats_for_output(records),
        "manual_profile_stats_by_slot": _profile_stats_by_slot_for_output(records),
        "profile_score_current": score_sector_profile_records(records, current_profiles),
        "profile_score_current_slot_preferences": score_preferred_sector_profiles(records),
        "profile_score_recommended": score_sector_profile_records(records, recommended_profiles),
    }

    if run_solver:
        current_audit = manual_configuration_audit(selected_names, time_limit_seconds=time_limit_seconds)
        with temporary_sector_profiles(recommended_profiles):
            recommended_audit = manual_configuration_audit(selected_names, time_limit_seconds=time_limit_seconds)
        result["solver_audit_current"] = summarize_manual_audit(current_audit)
        result["solver_audit_recommended_profiles"] = summarize_manual_audit(recommended_audit)

    return result


def run_focus_soft_calibration(
    names: list[str] | None = None,
    *,
    time_limit_seconds: int = 3,
    apply_on_success: bool = True,
) -> dict[str, object]:
    selected_names = list(dict.fromkeys(name.strip() for name in (names or FOCUS_CONFIGURATION_NAMES) if name.strip()))
    sector_calibration = run_sector_profile_calibration(
        selected_names,
        time_limit_seconds=time_limit_seconds,
        run_solver=True,
    )
    recommended_audit = sector_calibration.get("solver_audit_recommended_profiles")
    current_audit = sector_calibration.get("solver_audit_current")
    recommended_missing = (
        int(recommended_audit.get("total_missing_sector_hours") or 0)
        if isinstance(recommended_audit, dict)
        else None
    )
    recommended_count = (
        int(recommended_audit.get("configuration_count") or 0)
        if isinstance(recommended_audit, dict)
        else 0
    )
    recommended_status_counts = (
        recommended_audit.get("status_counts")
        if isinstance(recommended_audit, dict) and isinstance(recommended_audit.get("status_counts"), dict)
        else {}
    )
    covered_count = int(recommended_status_counts.get("covered", 0) or 0) if isinstance(recommended_status_counts, dict) else 0
    accepted = (
        bool(selected_names)
        and recommended_missing == 0
        and recommended_count == len(selected_names)
        and covered_count == len(selected_names)
    )
    applied_profiles: dict[str, list[str]] | None = None

    if accepted and apply_on_success:
        raw_profiles = sector_calibration.get("recommended_profiles")
        if isinstance(raw_profiles, dict):
            applied_profiles = apply_sector_profiles(
                {
                    int(count): [str(sector) for sector in profile]
                    for count, profile in raw_profiles.items()
                    if isinstance(profile, list)
                }
            )

    current_missing = (
        int(current_audit.get("total_missing_sector_hours") or 0)
        if isinstance(current_audit, dict)
        else None
    )
    message = (
        "Kalibracija je sprejeta in mehki profili sektorjev so uporabljeni v tem zagonu."
        if accepted and apply_on_success
        else "Kalibracija je sprejeta, vendar profili niso samodejno uporabljeni."
        if accepted
        else "Kalibracija ni sprejeta, ker fokus konfiguracije po učenju niso 100 % pokrite."
    )

    return {
        "focus_names": selected_names,
        "status": "accepted" if accepted else "rejected",
        "success": accepted,
        "applied": bool(applied_profiles),
        "applied_profiles": applied_profiles,
        "hard_constraints_changed": False,
        "acceptance_rule": "Vse fokus konfiguracije morajo imeti 100 % pokritost in 0 manjkajocih SH.",
        "message": message,
        "current_missing_sector_hours": current_missing,
        "recommended_missing_sector_hours": recommended_missing,
        "composition": analyze_focus_configuration_composition(selected_names),
        "sector_profile_calibration": sector_calibration,
    }


def _manual_role_counts(manual_schedule: dict[str, object] | None) -> dict[str, int]:
    counts: Counter[str] = Counter()
    if manual_schedule is None:
        return {}
    people = manual_schedule.get("people")
    if not isinstance(people, list):
        return {}
    for person in people:
        if not isinstance(person, dict):
            continue
        role = str(person.get("role") or "").upper()
        if role:
            counts[role] += 1
    return dict(counts)


def _excel_people_counts(manual_schedule: dict[str, object] | None) -> dict[str, int]:
    if manual_schedule is None:
        return {"rows": 0, "with_shift": 0, "with_sector_hours": 0}
    people = manual_schedule.get("people")
    if not isinstance(people, list):
        return {"rows": 0, "with_shift": 0, "with_sector_hours": 0}
    with_shift = 0
    with_sector_hours = 0
    for person in people:
        if not isinstance(person, dict):
            continue
        if person.get("shift"):
            with_shift += 1
        try:
            sector_hours = int(person.get("sector_hours") or 0)
        except (TypeError, ValueError):
            sector_hours = 0
        if sector_hours > 0:
            with_sector_hours += 1
    return {"rows": len(people), "with_shift": with_shift, "with_sector_hours": with_sector_hours}


def _license_ratio_from_counts(license_counts: dict[str, int]) -> dict[str, int]:
    if sum(max(0, int(license_counts.get(license_name, 0))) for license_name in ("FL", "APS", "ACS")) <= 0:
        return {"FL": 50, "APS": 0, "ACS": 50}
    return {
        license_name: max(0, int(license_counts.get(license_name, 0)))
        for license_name in ("FL", "APS", "ACS")
    }


def _minimum_staff_request(
    requested_sector_counts: list[int],
    manual_schedule: dict[str, object] | None,
    *,
    license_ratio: dict[str, int],
    include_fmp: bool,
    people_limit: int,
    time_limit_seconds: int,
) -> CalculatorRequest:
    settings = settings_for_manual_schedule_evaluation(manual_schedule, time_limit_seconds)
    return CalculatorRequest(
        calculation_mode="demand_to_staff",
        total_people=people_limit,
        fl_count=0,
        aps_count=0,
        acs_count=0,
        include_fmp=include_fmp,
        settings=settings,
        requested_sector_counts=requested_sector_counts,
        fixed_staff=[],
        locked_staff=[],
        officer_staff=[],
        office_pool=[],
        license_mix_percent={
            "fl": license_ratio["FL"],
            "aps": license_ratio["APS"],
            "acs": license_ratio["ACS"],
        },
        include_pareto=False,
        prefer_minimal_fl=False,
    )


def _hard_people_lower_bound(
    requested_sector_counts: list[int],
    manual_schedule: dict[str, object] | None,
    *,
    include_fmp: bool,
) -> int:
    settings = settings_for_manual_schedule_evaluation(manual_schedule, 1)
    enabled_shifts = calculator.enabled_shift_rules(settings.shifts)
    max_capacity = max(
        (
            calculator.max_sector_hours_for_shift(
                shift,
                settings.max_consecutive_work_hours,
                settings.rest_after_max_consecutive_hours,
            )
            for shift in enabled_shifts
        ),
        default=1,
    )
    seat_hours = sum(requested_sector_counts) * 2
    capacity_bound = math.ceil(seat_hours / max(1, max_capacity))
    peak_bound = max((sector_count * 2 for sector_count in requested_sector_counts), default=0)
    required_roles = 3 if settings.include_required_shift_leaders else 0
    if include_fmp:
        required_roles += 1
    return max(1, capacity_bound, peak_bound, required_roles)


def _full_coverage_is_proven_impossible(result, requested_sector_hours: int) -> bool:
    if result.missing_sector_hours <= 0:
        return False
    if result.solver_status == "OPTIMAL":
        return True
    if result.solver_upper_bound_sector_hours is not None and result.solver_upper_bound_sector_hours < requested_sector_hours:
        return True
    if result.solver_gap_to_upper_bound == 0 and result.max_sector_hours < requested_sector_hours:
        return True
    return False


def _minimum_staff_scan_variant_result(
    variant_name: str,
    manual_people: int,
    requested_sector_hours: int,
    requested_sector_counts: list[int],
    manual_schedule: dict[str, object] | None,
    *,
    license_ratio: dict[str, int],
    include_fmp: bool,
    time_limit_seconds: int,
) -> dict[str, object]:
    started_at = perf_counter()
    lower_bound = _hard_people_lower_bound(requested_sector_counts, manual_schedule, include_fmp=include_fmp)
    search_limit = max(manual_people, lower_bound)
    limit_results: list[dict[str, object]] = []
    best_result: dict[str, object] | None = None
    proof_status = "no_full_coverage"
    people_limit = search_limit

    while people_limit >= lower_bound:
        request = _minimum_staff_request(
            requested_sector_counts,
            manual_schedule,
            license_ratio=license_ratio,
            include_fmp=include_fmp,
            people_limit=people_limit,
            time_limit_seconds=time_limit_seconds,
        )
        limit_started_at = perf_counter()
        try:
            result = calculator.calculate(request)
        except Exception as exc:  # pragma: no cover - surfaced in calibration output.
            limit_results.append(
                {
                    "people_limit": people_limit,
                    "status": "error",
                    "message": str(exc),
                    "elapsed_seconds": round(perf_counter() - limit_started_at, 3),
                }
            )
            people_limit -= 1
            continue

        full_coverage = result.feasible and result.missing_sector_hours == 0
        proven_impossible = _full_coverage_is_proven_impossible(result, requested_sector_hours)
        limit_row = {
            "people_limit": people_limit,
            "status": "covered" if full_coverage else ("proven_shortfall" if proven_impossible else "shortfall_or_timeout"),
            "planned_people": result.planned_people,
            "active_people": result.active_people,
            "model_sector_hours": result.max_sector_hours,
            "missing_sector_hours": result.missing_sector_hours,
            "solver_status": result.solver_status,
            "solver_upper_bound_sector_hours": result.solver_upper_bound_sector_hours,
            "solver_gap_to_upper_bound": result.solver_gap_to_upper_bound,
            "elapsed_seconds": round(perf_counter() - limit_started_at, 3),
        }
        limit_results.append(limit_row)

        if full_coverage:
            best_result = {
                "planned_people": result.planned_people,
                "active_people": result.active_people,
                "requested_sector_hours": requested_sector_hours,
                "model_sector_hours": result.max_sector_hours,
                "missing_sector_hours": result.missing_sector_hours,
                "coverage_percent": 100,
                "scheduled_person_hours": result.scheduled_person_hours,
                "total_person_capacity_hours": result.total_person_capacity_hours,
                "utilization_percent": result.utilization_percent,
                "minimum_required_fl": result.minimum_required_fl,
                "solver_status": result.solver_status,
                "solver_gap_to_upper_bound": result.solver_gap_to_upper_bound,
                "shift_summary": [item.model_dump() for item in result.shift_summary],
                "license_counts": dict(Counter(person.license for person in result.people)),
            }
            if result.planned_people <= lower_bound:
                proof_status = "hard_lower_bound"
                break
            people_limit = min(people_limit - 1, result.planned_people - 1)
            continue

        if best_result is not None and proven_impossible:
            best_people = int(best_result["planned_people"])
            proof_status = "proven_minimum" if people_limit == best_people - 1 else "candidate_with_lower_bound"
            break

        people_limit -= 1

    if best_result is None:
        status = "shortfall"
    elif int(best_result["planned_people"]) < manual_people:
        status = "better"
    elif int(best_result["planned_people"]) == manual_people:
        status = "equal"
    else:
        status = "worse"
    if best_result is not None and proof_status == "no_full_coverage":
        proof_status = "candidate"

    output = {
        "variant": variant_name,
        "status": status,
        "proof_status": proof_status,
        "manual_people": manual_people,
        "lower_bound": lower_bound,
        "license_ratio": license_ratio,
        "include_fmp": include_fmp,
        "limit_results": limit_results,
        "elapsed_seconds": round(perf_counter() - started_at, 3),
    }
    if best_result is not None:
        output.update(
            {
                **best_result,
                "people_diff": int(best_result["planned_people"]) - manual_people,
            }
        )
    return output


def run_staff_minimum_calibration(
    names: list[str] | None = None,
    *,
    time_limit_seconds: int = 600,
) -> dict[str, object]:
    selected_names = names or FOCUS_CONFIGURATION_NAMES
    path = selected_configuration_library_path()
    if path is None:
        return {
            "source_path": None,
            "focus_names": selected_names,
            "rows": [
                {
                    "name": name,
                    "status": "missing_library",
                    "message": "Baza ročnih konfiguracij ni najdena.",
                }
                for name in selected_names
            ],
        }

    rows = read_configuration_csv(path)
    columns_by_name = {name: column_index for column_index, name in configuration_columns(rows)}
    supported_shifts = {shift.code for shift in calculator.DEFAULT_SHIFTS}
    started_at = perf_counter()
    rows_out: list[dict[str, object]] = []

    for name in selected_names:
        row_started_at = perf_counter()
        column_index = columns_by_name.get(name)
        if column_index is None:
            rows_out.append(
                {
                    "name": name,
                    "status": "missing",
                    "message": "Konfiguracija ni najdena v CSV bazi.",
                    "elapsed_seconds": round(perf_counter() - row_started_at, 3),
                }
            )
            continue

        configuration = parse_configuration(rows, column_index, name, supported_shifts)
        manual_schedule = manual_schedule_for_configuration(name)
        requested_sector_counts = _manual_requested_sector_counts(manual_schedule)
        if requested_sector_counts is None:
            rows_out.append(
                {
                    "name": name,
                    "status": "missing_schedule",
                    "message": "Excel ročni urnik ni najden ali nima 24 ur.",
                    "elapsed_seconds": round(perf_counter() - row_started_at, 3),
                }
            )
            continue
        if configuration.unsupported_rows:
            rows_out.append(
                {
                    "name": name,
                    "status": "unsupported",
                    "unsupported_rows": configuration.unsupported_rows,
                    "message": "Konfiguracija vsebuje nepodprte vrstice.",
                    "elapsed_seconds": round(perf_counter() - row_started_at, 3),
                }
            )
            continue

        manual_people = configuration.parsed_total
        requested_sector_hours = sum(requested_sector_counts)
        role_counts = _manual_role_counts(manual_schedule)
        manual_ratio = _license_ratio_from_counts(configuration.license_counts)
        variants = [
            (
                "manual_ratio",
                manual_ratio,
                role_counts.get("FMP", 0) > 0,
            ),
            (
                "default_50_50",
                {"FL": 50, "APS": 0, "ACS": 50},
                False,
            ),
        ]
        variant_results = [
            _minimum_staff_scan_variant_result(
                variant_name,
                manual_people,
                requested_sector_hours,
                requested_sector_counts,
                manual_schedule,
                license_ratio=license_ratio,
                include_fmp=include_fmp,
                time_limit_seconds=time_limit_seconds,
            )
            for variant_name, license_ratio, include_fmp in variants
        ]
        best_result = min(
            (item for item in variant_results if item.get("status") not in {"error", "shortfall"}),
            key=lambda item: int(item.get("planned_people") or 999),
            default=None,
        )
        rows_out.append(
            {
                "id": str(column_index),
                "name": name,
                "status": "ok" if best_result is not None else "shortfall",
                "manual_people": manual_people,
                "manual_total_without_waiting": configuration.total_without_waiting,
                "manual_waiting_count": configuration.waiting_count,
                "excel_people": _excel_people_counts(manual_schedule),
                "manual_license_counts": configuration.license_counts,
                "manual_role_counts": role_counts,
                "requested_sector_hours": requested_sector_hours,
                "variants": variant_results,
                "best_planned_people": best_result.get("planned_people") if best_result else None,
                "best_people_diff": best_result.get("people_diff") if best_result else None,
                "elapsed_seconds": round(perf_counter() - row_started_at, 3),
            }
        )

    summary_by_variant: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows_out:
        variants = row.get("variants")
        if not isinstance(variants, list):
            continue
        for variant in variants:
            if isinstance(variant, dict):
                summary_by_variant[str(variant.get("variant"))][str(variant.get("status"))] += 1

    return {
        "source_path": str(path),
        "focus_names": selected_names,
        "time_limit_seconds": time_limit_seconds,
        "elapsed_seconds": round(perf_counter() - started_at, 3),
        "summary_by_variant": {
            variant: dict(counter)
            for variant, counter in sorted(summary_by_variant.items())
        },
        "rows": rows_out,
    }

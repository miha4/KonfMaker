from __future__ import annotations

import csv
import json
import math
import os
import re
import time
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Callable
from uuid import uuid4
from xml.etree import ElementTree as ET

from .calculator import CALC_PHASE_PROGRESS_PREFIX, CalculationCancelled, DEFAULT_OFFICER_SHIFTS, DEFAULT_SHIFTS, calculate, sector_display_names_for_max
from .models import (
    CalculatorRequest,
    CalculatorResponse,
    CalculatorSettings,
    CompareConfigurationRequest,
    CompleteConfigurationRequest,
    DEFAULT_INCLUDE_NIGHT_FL_REQUIREMENT,
    DEFAULT_REQUIRED_NIGHT_FL_COUNT,
    FixedStaffRule,
    OfficerStaffRule,
    SaveUserConfigurationRequest,
    ShiftRule,
)


IGNORED_CONFIGURATION_HEADERS = {"", "Mušter", "Muster", "TCfgOKZP"}
ROLE_ROWS = {
    "Vi1": ("A7", "V1"),
    "Vi2": ("A14", "V2"),
    "Vi3": ("A21", "V3"),
}
FMP_ROWS = {
    "A7f": "A7",
    "A78f": "A78",
    "A8f": "A8",
    "A9f": "A9",
    "A10f": "A10",
    "A11f": "A11",
    "A14f": "A14",
    "A48f": "A48",
    "A15f": "A15",
}
NORMAL_SHIFT_ROWS = [
    "A6",
    "A7",
    "A8",
    "A9",
    "A10",
    "A11",
    "A12",
    "A13",
    "A14",
    "A48",
    "A15",
    "A16",
    "A17",
    "A19",
    "A21",
    "A23",
]
OFFICER_SHIFT_ROWS = [
    "A6o",
    "A7o",
    "A8o",
    "A9o",
    "A10o",
    "A11o",
    "A14o",
]
LICENSES = ("FL", "APS", "ACS")
CONFIG_LIBRARY_ENV = "KONFMAKER_CONFIG_LIBRARY_CSV"
CONFIG_WORKBOOK_ENV = "KONFMAKER_CONFIG_WORKBOOK_XLSX"
USER_CONFIG_LIBRARY_ENV = "KONFMAKER_USER_CONFIG_LIBRARY_JSON"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_LIBRARY_PATHS = [
    PROJECT_ROOT / "data" / "konfiguracije_okzp_obogateno_vlimiti.csv",
    PROJECT_ROOT / "data" / "konfiguracije_okzp_obogateno.csv",
    Path("/Users/mihamedvesek/Documents/Codex/konfiguracije_okzp_obogateno_vlimiti.csv"),
    Path("/Users/mihamedvesek/Documents/Codex/konfiguracije_okzp_obogateno.csv"),
]
DEFAULT_CONFIG_WORKBOOK_PATHS = [
    PROJECT_ROOT / "Konfiguracije OKZP.xlsx",
]
DEFAULT_USER_CONFIG_LIBRARY_PATH = PROJECT_ROOT / "data" / "user_configurations.local.json"
LEGACY_USER_CONFIG_LIBRARY_PATH = PROJECT_ROOT / "data" / "user_configurations.json"
_USER_CONFIGURATION_LOCK = Lock()
DAY_START = 7
XLSX_NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pkgrel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
MANUAL_SCHEDULE_COLUMNS = [
    ("ALL", "AW", "AX"),
    ("LOWER", "AY", "AZ"),
    ("UPPER", "BA", "BB"),
    ("MID", "BC", "BD"),
    ("HIGH", "BE", "BF"),
    ("TOP", "BG", "BH"),
]
MANUAL_SCHEDULE_CELL_RE = re.compile(r"^([A-Z]+)([0-9]+)$")
FOCUS_CONFIGURATION_NAMES = [
    "21z4",
    "20z4",
    "22z4",
    "23z4",
    "23n4",
    "24n4",
    "25n5x",
    "26f4",
    "26f4x",
    "27n5",
    "27s5",
    "28s5",
]


@dataclass(frozen=True)
class ParsedConfiguration:
    name: str
    column_index: int
    fixed_staff: list[FixedStaffRule]
    officer_staff: list[OfficerStaffRule]
    license_counts: dict[str, int]
    total_without_waiting: int
    waiting_count: int
    unsupported_rows: list[str]

    @property
    def parsed_total(self) -> int:
        return sum(self.license_counts.values())


def _parse_int(value: str | None) -> int:
    if value is None:
        return 0
    cleaned = value.strip()
    if not cleaned:
        return 0
    try:
        return int(float(cleaned.replace(",", ".")))
    except ValueError:
        return 0


def _cell(rows_by_label: dict[str, list[str]], label: str, column_index: int) -> int:
    row = rows_by_label.get(label)
    if row is None or column_index >= len(row):
        return 0
    return _parse_int(row[column_index])


def read_configuration_csv(path: str | Path) -> list[list[str]]:
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.reader(handle, delimiter=";"))


def configuration_library_paths() -> list[Path]:
    paths: list[Path] = []
    configured = os.environ.get(CONFIG_LIBRARY_ENV)
    if configured:
        paths.append(Path(configured))
    paths.extend(DEFAULT_CONFIG_LIBRARY_PATHS)
    return paths


def configuration_workbook_paths() -> list[Path]:
    paths: list[Path] = []
    configured = os.environ.get(CONFIG_WORKBOOK_ENV)
    if configured:
        paths.append(Path(configured))
    paths.extend(DEFAULT_CONFIG_WORKBOOK_PATHS)
    return paths


def selected_configuration_library_path() -> Path | None:
    return next((path for path in configuration_library_paths() if path.exists()), None)


def selected_configuration_workbook_path() -> Path | None:
    return next((path for path in configuration_workbook_paths() if path.exists()), None)


def user_configuration_library_path() -> Path:
    configured = os.environ.get(USER_CONFIG_LIBRARY_ENV)
    return Path(configured) if configured else DEFAULT_USER_CONFIG_LIBRARY_PATH


def user_configuration_read_path() -> Path:
    path = user_configuration_library_path()
    if path.exists() or os.environ.get(USER_CONFIG_LIBRARY_ENV):
        return path
    return LEGACY_USER_CONFIG_LIBRARY_PATH if LEGACY_USER_CONFIG_LIBRARY_PATH.exists() else path


def _read_user_configuration_records() -> list[dict[str, object]]:
    path = user_configuration_read_path()
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return []
    records = data.get("configurations") if isinstance(data, dict) else data
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict)]


def _write_user_configuration_records(records: list[dict[str, object]]) -> None:
    path = user_configuration_library_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary_path.open("w", encoding="utf-8") as handle:
            json.dump(
                {
                    "version": 2,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "configurations": records,
                },
                handle,
                ensure_ascii=False,
                indent=2,
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _user_configuration_id() -> str:
    return f"user:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"


def configuration_columns(rows: list[list[str]]) -> list[tuple[int, str]]:
    if not rows:
        return []
    columns: list[tuple[int, str]] = []
    seen: dict[str, int] = {}
    for column_index, raw_name in enumerate(rows[0][1:], start=1):
        name = raw_name.strip()
        if name in IGNORED_CONFIGURATION_HEADERS:
            continue
        seen[name] = seen.get(name, 0) + 1
        display_name = name if seen[name] == 1 else f"{name}#{seen[name]}"
        columns.append((column_index, display_name))
    return columns


def configuration_metric_by_column(rows: list[list[str]], label: str) -> dict[int, int | float | str]:
    for row in rows:
        if not row or row[0].strip() != label:
            continue
        values: dict[int, int | float | str] = {}
        for column_index, value in enumerate(row):
            if column_index == 0:
                continue
            cleaned = value.strip()
            if not cleaned:
                continue
            try:
                numeric = float(cleaned.replace(",", "."))
            except ValueError:
                values[column_index] = cleaned
                continue
            values[column_index] = int(numeric) if numeric.is_integer() else numeric
        return values
    return {}


def parse_configuration(
    rows: list[list[str]],
    column_index: int,
    name: str,
    supported_shifts: set[str] | None = None,
) -> ParsedConfiguration:
    rows_by_label = {row[0].strip(): row for row in rows if row}
    supported = supported_shifts or {shift.code for shift in DEFAULT_SHIFTS}
    fixed_staff: list[FixedStaffRule] = []
    officer_staff: list[OfficerStaffRule] = []
    license_counts = {license_name: 0 for license_name in LICENSES}
    unsupported_rows: list[str] = []

    def add_fixed(count: int, license_name: str, shift: str, role: str | None = None, source_row: str | None = None) -> None:
        if count <= 0:
            return
        if shift not in supported:
            unsupported_rows.append(source_row or shift)
            return
        fixed_staff.append(FixedStaffRule(count=count, license=license_name, shift=shift, role=role))
        license_counts[license_name] += count

    def add_officer(count: int, license_name: str, shift: str, source_row: str | None = None) -> None:
        if count <= 0:
            return
        if shift not in {officer_shift.code for officer_shift in DEFAULT_OFFICER_SHIFTS}:
            unsupported_rows.append(source_row or shift)
            return
        officer_staff.append(OfficerStaffRule(count=count, license=license_name, shift=shift))
        license_counts[license_name] += count

    for row_name, (shift, role) in ROLE_ROWS.items():
        add_fixed(_cell(rows_by_label, row_name, column_index), "FL", shift, role, row_name)

    for row_name, shift in FMP_ROWS.items():
        add_fixed(_cell(rows_by_label, row_name, column_index), "FL", shift, "FMP", row_name)

    for shift in NORMAL_SHIFT_ROWS:
        total = _cell(rows_by_label, shift, column_index)
        if total <= 0:
            continue
        aps = _cell(rows_by_label, f"APS{shift}", column_index)
        acs = _cell(rows_by_label, f"ACS{shift}", column_index)
        fl = max(0, total - aps - acs)
        add_fixed(fl, "FL", shift)
        add_fixed(aps, "APS", shift)
        add_fixed(acs, "ACS", shift)

    for shift in OFFICER_SHIFT_ROWS:
        total = _cell(rows_by_label, shift, column_index)
        if total <= 0:
            continue
        aps = _cell(rows_by_label, f"APS{shift}", column_index)
        acs = _cell(rows_by_label, f"ACS{shift}", column_index)
        fl = max(0, total - aps - acs)
        add_officer(fl, "FL", shift)
        add_officer(aps, "APS", shift)
        add_officer(acs, "ACS", shift)

    return ParsedConfiguration(
        name=name,
        column_index=column_index,
        fixed_staff=fixed_staff,
        officer_staff=officer_staff,
        license_counts=license_counts,
        total_without_waiting=_cell(rows_by_label, "Skupaj-w", column_index),
        waiting_count=_cell(rows_by_label, "w", column_index),
        unsupported_rows=sorted(set(unsupported_rows)),
    )


def settings_for_configuration_evaluation(
    time_limit_seconds: int = 10,
    shifts: list[ShiftRule] | None = None,
) -> CalculatorSettings:
    return CalculatorSettings(
        max_sectors_per_hour=5,
        max_consecutive_work_hours=2,
        rest_after_max_consecutive_hours=1,
        cp_sat_time_limit_seconds=time_limit_seconds,
        cp_sat_no_improvement_seconds=0,
        cp_sat_acceptable_sector_gap=0,
        cp_sat_min_auto_stop_coverage_percent=95,
        include_required_shift_leaders=True,
        include_night_fl_requirement=DEFAULT_INCLUDE_NIGHT_FL_REQUIREMENT,
        required_night_fl_count=DEFAULT_REQUIRED_NIGHT_FL_COUNT,
        v1_sector_limit=1,
        v2_sector_limit=1,
        v3_sector_limit=4,
        fmp_sector_limit=6,
        shifts=shifts or DEFAULT_SHIFTS,
        officer_shifts=DEFAULT_OFFICER_SHIFTS,
    )


def settings_for_manual_schedule_evaluation(
    manual_schedule: dict[str, object] | None,
    time_limit_seconds: int = 10,
    base_settings: CalculatorSettings | None = None,
) -> CalculatorSettings:
    settings = (
        settings_for_configuration_evaluation(time_limit_seconds)
        if base_settings is None
        else base_settings.model_copy(
            update={
                "cp_sat_time_limit_seconds": time_limit_seconds,
                "cp_sat_no_improvement_seconds": 0,
                "cp_sat_acceptable_sector_gap": 0,
            }
        )
    )
    if manual_schedule is None:
        return settings

    role_limits: dict[str, int] = {}
    people = manual_schedule.get("people")
    if isinstance(people, list):
        for person in people:
            if not isinstance(person, dict):
                continue
            role = str(person.get("role") or "").upper()
            if role not in {"V1", "V2", "V3", "FMP"}:
                continue
            sector_hours = _excel_number(person.get("sector_hours"))
            if sector_hours is None:
                continue
            role_limits[role] = max(role_limits.get(role, 0), int(math.ceil(float(sector_hours))))

    return settings.model_copy(
        update={
            "v1_sector_limit": max(settings.v1_sector_limit, role_limits.get("V1", settings.v1_sector_limit)),
            "v2_sector_limit": max(settings.v2_sector_limit, role_limits.get("V2", settings.v2_sector_limit)),
            "v3_sector_limit": max(settings.v3_sector_limit, role_limits.get("V3", settings.v3_sector_limit)),
            "fmp_sector_limit": max(settings.fmp_sector_limit, role_limits.get("FMP", settings.fmp_sector_limit)),
        }
    )


def _enabled_officer_shift_codes(settings: CalculatorSettings) -> set[str]:
    return {shift.code for shift in settings.officer_shifts if shift.enabled}


def _officer_staff_from_detail(
    detail: dict[str, object],
    settings: CalculatorSettings,
) -> list[OfficerStaffRule]:
    enabled_codes = _enabled_officer_shift_codes(settings)
    officer_staff: list[OfficerStaffRule] = []
    for item in detail.get("officer_staff", []):
        if not isinstance(item, dict):
            continue
        try:
            rule = OfficerStaffRule.model_validate(item)
        except ValueError:
            continue
        if rule.shift in enabled_codes:
            officer_staff.append(rule)
    return officer_staff


def request_for_configuration(
    configuration: ParsedConfiguration,
    settings: CalculatorSettings,
    requested_sector_counts: list[int] | None = None,
) -> CalculatorRequest:
    return CalculatorRequest(
        calculation_mode="staff_to_coverage",
        total_people=configuration.parsed_total,
        fl_count=configuration.license_counts["FL"],
        aps_count=configuration.license_counts["APS"],
        acs_count=configuration.license_counts["ACS"],
        include_fmp=False,
        settings=settings,
        requested_sector_counts=requested_sector_counts,
        fixed_staff=configuration.fixed_staff,
        locked_staff=[],
        officer_staff=configuration.officer_staff,
        office_pool=[],
        license_mix_percent=None,
        include_pareto=False,
        prefer_minimal_fl=False,
    )


def _shift_lookup() -> dict[str, ShiftRule]:
    return {
        shift.code: shift
        for shift in [*DEFAULT_SHIFTS, *DEFAULT_OFFICER_SHIFTS]
    }


def _hour_slots_for_shift(shift: ShiftRule) -> list[int]:
    return [
        (shift.start_hour + offset - DAY_START) % 24
        for offset in range(shift.duration_hours)
    ]


def staff_rows_for_configuration(configuration: ParsedConfiguration) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, str | None], dict[str, object]] = {}
    shift_lookup = _shift_lookup()

    def add(source: str, shift_code: str, role: str | None, license_name: str, count: int) -> None:
        if count <= 0:
            return
        key = (source, shift_code, role)
        row = grouped.setdefault(
            key,
            {
                "source": source,
                "shift": shift_code,
                "role": role,
                "fl": 0,
                "aps": 0,
                "acs": 0,
                "total": 0,
                "start_hour": None,
                "duration_hours": None,
                "hour_slots": [],
            },
        )
        row_key = license_name.lower()
        row[row_key] = int(row[row_key]) + count
        row["total"] = int(row["total"]) + count

    for item in configuration.fixed_staff:
        add("regular", item.shift, item.role, item.license, item.count)
    for item in configuration.officer_staff:
        add("officer", item.shift, None, item.license, item.count)

    rows: list[dict[str, object]] = []
    for row in grouped.values():
        shift = shift_lookup.get(str(row["shift"]))
        if shift is not None:
            row["start_hour"] = shift.start_hour
            row["duration_hours"] = shift.duration_hours
            row["hour_slots"] = _hour_slots_for_shift(shift)
        rows.append(row)

    return sorted(
        rows,
        key=lambda row: (
            99 if row["start_hour"] is None else int(row["start_hour"]),
            str(row["source"]),
            str(row["role"] or ""),
            str(row["shift"]),
        ),
    )


def _is_officer_shift(shift_code: str) -> bool:
    return shift_code in {shift.code for shift in DEFAULT_OFFICER_SHIFTS}


def _result_source_for_shift(shift_code: str, source: str | None) -> str:
    if source in {"officer", "office-pool"} and _is_officer_shift(shift_code):
        return "officer"
    return "regular"


def _license_counts_from_result(result: CalculatorResponse) -> dict[str, int]:
    counts = {license_name: 0 for license_name in LICENSES}
    for person in result.people:
        if person.license in counts:
            counts[person.license] += 1
    return counts


def _fixed_staff_from_result(result: CalculatorResponse) -> list[dict[str, object]]:
    grouped: Counter[tuple[str, str, str | None]] = Counter()
    for person in result.people:
        if _result_source_for_shift(person.shift, person.source) == "officer":
            continue
        grouped[(person.license, person.shift, person.role)] += 1
    return [
        {
            "count": count,
            "license": license_name,
            "shift": shift,
            "role": role,
        }
        for (license_name, shift, role), count in sorted(grouped.items(), key=lambda item: (item[0][1], item[0][2] or "", item[0][0]))
    ]


def _officer_staff_from_result(result: CalculatorResponse) -> list[dict[str, object]]:
    grouped: Counter[tuple[str, str]] = Counter()
    for person in result.people:
        if _result_source_for_shift(person.shift, person.source) != "officer":
            continue
        grouped[(person.license, person.shift)] += 1
    return [
        {
            "count": count,
            "license": license_name,
            "shift": shift,
        }
        for (license_name, shift), count in sorted(grouped.items(), key=lambda item: (item[0][1], item[0][0]))
    ]


def _staff_rows_from_result(result: CalculatorResponse) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, str | None], dict[str, object]] = {}
    shift_lookup = _shift_lookup()

    for person in result.people:
        source = _result_source_for_shift(person.shift, person.source)
        key = (source, person.shift, person.role)
        row = grouped.setdefault(
            key,
            {
                "source": source,
                "shift": person.shift,
                "role": person.role,
                "fl": 0,
                "aps": 0,
                "acs": 0,
                "total": 0,
                "start_hour": None,
                "duration_hours": None,
                "hour_slots": [],
            },
        )
        row_key = person.license.lower()
        row[row_key] = int(row[row_key]) + 1
        row["total"] = int(row["total"]) + 1

    rows: list[dict[str, object]] = []
    for row in grouped.values():
        shift = shift_lookup.get(str(row["shift"]))
        if shift is not None:
            row["start_hour"] = shift.start_hour
            row["duration_hours"] = shift.duration_hours
            row["hour_slots"] = _hour_slots_for_shift(shift)
        rows.append(row)

    return sorted(
        rows,
        key=lambda row: (
            99 if row["start_hour"] is None else int(row["start_hour"]),
            str(row["source"]),
            str(row["role"] or ""),
            str(row["shift"]),
        ),
    )


def _manual_schedule_from_result(result: CalculatorResponse, source_path: Path) -> dict[str, object]:
    return {
        "source_path": str(source_path),
        "people": [
            {
                "label": person.id,
                "shift": person.shift,
                "sector_hours": person.sector_hours,
                "role": person.role,
                "source": _result_source_for_shift(person.shift, person.source),
            }
            for person in result.people
        ],
        "hourly_coverage": [hour.model_dump() for hour in result.hourly_coverage],
        "max_sector_hours": result.max_sector_hours,
        "scheduled_person_hours": result.scheduled_person_hours,
    }


def _user_configuration_summary(record: dict[str, object]) -> dict[str, object]:
    stored_summary = record.get("summary")
    if isinstance(stored_summary, dict):
        planned_people = int(stored_summary.get("planned_people", 0) or 0)
        max_sector_hours = int(stored_summary.get("max_sector_hours", 0) or 0)
        stored_license_counts = stored_summary.get("license_counts")
        license_counts = {
            license_name: int(stored_license_counts.get(license_name, 0) or 0)
            if isinstance(stored_license_counts, dict)
            else 0
            for license_name in LICENSES
        }
    else:
        result = CalculatorResponse.model_validate(record["result"])
        planned_people = result.planned_people
        max_sector_hours = result.max_sector_hours
        license_counts = _license_counts_from_result(result)
    return {
        "id": str(record["id"]),
        "name": str(record["name"]),
        "column_index": -1,
        "parsed_total": planned_people,
        "total_without_waiting": planned_people,
        "waiting_count": 0,
        "license_counts": license_counts,
        "unsupported_rows": [],
        "status": "UPORABNIK",
        "model_max_sector_hours": max_sector_hours,
        "model_seconds": None,
        "has_manual_schedule": True,
        "source_type": "user",
        "source_label": "Shranjena s strani uporabnika",
        "created_at": record.get("created_at"),
        "note": record.get("note"),
    }


def _user_configuration_detail(record: dict[str, object]) -> dict[str, object]:
    path = user_configuration_library_path()
    summary = _user_configuration_summary(record)
    stored_schedule = record.get("manual_schedule")
    if isinstance(stored_schedule, dict):
        manual_schedule = {**stored_schedule, "source_path": str(path)}
        fixed_staff = record.get("fixed_staff") if isinstance(record.get("fixed_staff"), list) else []
        officer_staff = record.get("officer_staff") if isinstance(record.get("officer_staff"), list) else []
        staff_rows = record.get("staff_rows") if isinstance(record.get("staff_rows"), list) else []
    else:
        result = CalculatorResponse.model_validate(record["result"])
        manual_schedule = _manual_schedule_from_result(result, path)
        fixed_staff = _fixed_staff_from_result(result)
        officer_staff = _officer_staff_from_result(result)
        staff_rows = _staff_rows_from_result(result)
    return {
        **summary,
        "source_path": str(path),
        "workbook_path": None,
        "fixed_staff": fixed_staff,
        "officer_staff": officer_staff,
        "staff_rows": staff_rows,
        "manual_schedule": manual_schedule,
    }


def user_configuration_detail(configuration_id: str) -> dict[str, object]:
    for record in _read_user_configuration_records():
        if str(record.get("id")) == configuration_id:
            return _user_configuration_detail(record)
    raise ValueError("Uporabniška konfiguracija ne obstaja.")


def save_user_configuration(payload: SaveUserConfigurationRequest) -> dict[str, object]:
    result = payload.result
    if not result.feasible or result.missing_sector_hours > 0:
        raise ValueError("Shraniš lahko samo izvedljivo konfiguracijo brez manjkajočih sektorskih ur.")
    name = (payload.name or "").strip()
    if not name:
        name = f"Shranjena {datetime.now().strftime('%d.%m. %H:%M')}"
    manual_schedule = _manual_schedule_from_result(result, user_configuration_library_path())
    manual_schedule.pop("source_path", None)
    record = {
        "schema_version": 2,
        "id": _user_configuration_id(),
        "name": name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "note": (payload.note or "").strip() or None,
        "summary": {
            "planned_people": result.planned_people,
            "max_sector_hours": result.max_sector_hours,
            "license_counts": _license_counts_from_result(result),
        },
        "fixed_staff": _fixed_staff_from_result(result),
        "officer_staff": _officer_staff_from_result(result),
        "staff_rows": _staff_rows_from_result(result),
        "manual_schedule": manual_schedule,
    }
    with _USER_CONFIGURATION_LOCK:
        records = _read_user_configuration_records()
        records.insert(0, record)
        _write_user_configuration_records(records)
    return _user_configuration_detail(record)


def delete_user_configuration(configuration_id: str) -> dict[str, object]:
    if not str(configuration_id).startswith("user:"):
        raise ValueError("Izbrišeš lahko samo konfiguracije, shranjene s strani uporabnika.")
    with _USER_CONFIGURATION_LOCK:
        records = _read_user_configuration_records()
        next_records = [
            record
            for record in records
            if str(record.get("id")) != configuration_id
        ]
        if len(next_records) == len(records):
            raise ValueError("Uporabniška konfiguracija ne obstaja.")
        _write_user_configuration_records(next_records)
    return {
        "deleted": True,
        "id": configuration_id,
        "remaining": len(next_records),
    }


def _clean_excel_label(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        text = str(int(value))
    else:
        text = str(value)
    cleaned = text.replace("\xa0", " ").strip()
    return cleaned or None


def _excel_number(value: object) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return int(value) if float(value).is_integer() else float(value)
    cleaned = str(value).strip().replace(",", ".")
    if not cleaned:
        return None
    try:
        parsed = float(cleaned)
    except ValueError:
        return None
    return int(parsed) if parsed.is_integer() else parsed


def _xlsx_shared_strings(workbook: zipfile.ZipFile) -> list[str]:
    try:
        payload = workbook.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(payload)
    values: list[str] = []
    for item in root.findall("main:si", XLSX_NS):
        parts = [node.text or "" for node in item.findall(".//main:t", XLSX_NS)]
        values.append("".join(parts))
    return values


def _xlsx_sheet_targets(workbook: zipfile.ZipFile) -> dict[str, str]:
    workbook_root = ET.fromstring(workbook.read("xl/workbook.xml"))
    rels_root = ET.fromstring(workbook.read("xl/_rels/workbook.xml.rels"))
    rel_targets = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in rels_root.findall("pkgrel:Relationship", XLSX_NS)
    }
    targets: dict[str, str] = {}
    for sheet in workbook_root.findall("main:sheets/main:sheet", XLSX_NS):
        rel_id = sheet.attrib.get(f"{{{XLSX_NS['rel']}}}id")
        target = rel_targets.get(str(rel_id))
        if not target:
            continue
        normalized_target = target.lstrip("/")
        if not normalized_target.startswith("xl/"):
            normalized_target = f"xl/{normalized_target}"
        targets[sheet.attrib["name"]] = normalized_target
    return targets


@lru_cache(maxsize=8)
def _xlsx_sheet_names_cached(path: str, modified_at_ns: int) -> frozenset[str]:
    with zipfile.ZipFile(path) as workbook:
        return frozenset(_xlsx_sheet_targets(workbook))


def _xlsx_sheet_names(path: str) -> frozenset[str]:
    return _xlsx_sheet_names_cached(path, Path(path).stat().st_mtime_ns)


def _xlsx_cell_value(cell: ET.Element, shared_strings: list[str]) -> str | int | float | bool | None:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        parts = [node.text or "" for node in cell.findall(".//main:t", XLSX_NS)]
        return "".join(parts) or None

    value_node = cell.find("main:v", XLSX_NS)
    if value_node is None or value_node.text is None:
        return None

    raw_value = value_node.text
    if cell_type == "s":
        try:
            return shared_strings[int(raw_value)]
        except (IndexError, ValueError):
            return None
    if cell_type == "b":
        return raw_value == "1"
    if cell_type in {"str", "e"}:
        return raw_value

    try:
        numeric = float(raw_value)
    except ValueError:
        return raw_value
    return int(numeric) if numeric.is_integer() else numeric


def _xlsx_sheet_cells(
    path: Path,
    sheet_name: str,
    *,
    min_row: int,
    max_row: int,
    columns: set[str],
) -> dict[str, object]:
    with zipfile.ZipFile(path) as workbook:
        targets = _xlsx_sheet_targets(workbook)
        target = targets.get(sheet_name)
        if target is None:
            return {}
        shared_strings = _xlsx_shared_strings(workbook)
        root = ET.fromstring(workbook.read(target))

    values: dict[str, object] = {}
    for cell in root.findall(".//main:c", XLSX_NS):
        reference = cell.attrib.get("r", "")
        match = MANUAL_SCHEDULE_CELL_RE.match(reference)
        if match is None:
            continue
        column, row_text = match.groups()
        row = int(row_text)
        if row < min_row or row > max_row or column not in columns:
            continue
        value = _xlsx_cell_value(cell, shared_strings)
        if value is not None:
            values[reference] = value
    return values


@lru_cache(maxsize=512)
def _manual_schedule_for_workbook(path_text: str, modified_at_ns: int, name: str) -> dict[str, object] | None:
    path = Path(path_text)
    schedule_columns = {
        "AR", "AS", "AT", "AU", "AV",
        *(column for _, *pair in MANUAL_SCHEDULE_COLUMNS for column in pair),
    }
    cells = _xlsx_sheet_cells(path, name, min_row=1, max_row=80, columns=schedule_columns)
    if not cells:
        return None

    people: list[dict[str, object]] = []
    for row in range(2, 81):
        label = _clean_excel_label(cells.get(f"AR{row}"))
        if label is None:
            if people:
                break
            continue
        shift = _clean_excel_label(cells.get(f"AS{row}"))
        sector_hours = _excel_number(cells.get(f"AT{row}"))
        role = label.upper() if label.upper() in {"V1", "V2", "V3", "FMP"} else None
        source = "officer" if shift and shift.endswith("o") else "regular"
        people.append(
            {
                "label": label,
                "shift": shift,
                "sector_hours": sector_hours,
                "role": role,
                "source": source,
            }
        )

    hourly_coverage: list[dict[str, object]] = []
    max_sector_hours = 0
    scheduled_person_hours = 0
    for row in range(2, 26):
        hour = _clean_excel_label(cells.get(f"AU{row}")) or _hour_label_for_slot(row - 2)
        open_sectors = _excel_number(cells.get(f"AV{row}"))
        sector_workers: list[dict[str, str] | None] = []
        workers: list[str] = []
        for sector_name, lower_column, upper_column in MANUAL_SCHEDULE_COLUMNS:
            lower_worker = _clean_excel_label(cells.get(f"{lower_column}{row}"))
            upper_worker = _clean_excel_label(cells.get(f"{upper_column}{row}"))
            if lower_worker is None and upper_worker is None:
                sector_workers.append(None)
                continue
            lower_worker = lower_worker or ""
            upper_worker = upper_worker or ""
            sector_workers.append(
                {
                    "sector_name": sector_name,
                    "lower_worker": lower_worker,
                    "upper_worker": upper_worker,
                }
            )
            workers.extend([worker for worker in (lower_worker, upper_worker) if worker])
        open_sector_count = int(open_sectors) if isinstance(open_sectors, int | float) else sum(1 for item in sector_workers if item)
        max_sector_hours += open_sector_count
        scheduled_person_hours += len(workers)
        hourly_coverage.append(
            {
                "hour": hour,
                "open_sectors": open_sector_count,
                "workers": workers,
                "sector_workers": sector_workers,
            }
        )

    return {
        "source_path": str(path),
        "people": people,
        "hourly_coverage": hourly_coverage,
        "max_sector_hours": max_sector_hours,
        "scheduled_person_hours": scheduled_person_hours,
    }


def manual_schedule_for_configuration(name: str) -> dict[str, object] | None:
    path = selected_configuration_workbook_path()
    if path is None:
        return None
    return _manual_schedule_for_workbook(str(path), path.stat().st_mtime_ns, name)


def _manual_schedule_max_sector_hours(manual_schedule: dict[str, object] | None) -> int | None:
    if manual_schedule is None:
        return None
    max_sector_hours = _excel_number(manual_schedule.get("max_sector_hours"))
    if max_sector_hours is not None:
        return int(max_sector_hours)

    hourly_coverage = manual_schedule.get("hourly_coverage")
    if not isinstance(hourly_coverage, list):
        return None

    total = 0
    for hour in hourly_coverage:
        if not isinstance(hour, dict):
            return None
        open_sectors = _excel_number(hour.get("open_sectors"))
        if open_sectors is None:
            return None
        total += int(open_sectors)
    return total


def _hour_label_for_slot(slot: int) -> str:
    start = (DAY_START + slot) % 24
    end = (start + 1) % 24
    return f"{start:02d}:00–{end:02d}:00"


def configuration_summary(
    configuration: ParsedConfiguration,
    *,
    model_max_sector_hours: int | float | str | None = None,
    manual_sector_hours: int | None = None,
    model_status: int | float | str | None = None,
    model_seconds: int | float | str | None = None,
    has_manual_schedule: bool = False,
) -> dict[str, object]:
    status = str(model_status or ("NEPODPRTE VRSTICE" if configuration.unsupported_rows else "OK"))
    display_sector_hours = manual_sector_hours if manual_sector_hours is not None else model_max_sector_hours
    return {
        "id": str(configuration.column_index),
        "name": configuration.name,
        "column_index": configuration.column_index,
        "parsed_total": configuration.parsed_total,
        "total_without_waiting": configuration.total_without_waiting,
        "waiting_count": configuration.waiting_count,
        "license_counts": configuration.license_counts,
        "unsupported_rows": configuration.unsupported_rows,
        "status": status,
        "model_max_sector_hours": display_sector_hours,
        "model_reported_sector_hours": model_max_sector_hours,
        "excel_sector_hours": manual_sector_hours,
        "model_seconds": model_seconds,
        "has_manual_schedule": has_manual_schedule,
        "source_type": "excel",
        "source_label": "Excel ročna konfiguracija",
    }


@lru_cache(maxsize=8)
def _base_manual_configuration_summaries(
    path_text: str,
    path_modified_at_ns: int,
    workbook_path_text: str | None,
    workbook_modified_at_ns: int | None,
) -> tuple[dict[str, object], ...]:
    path = Path(path_text)
    rows = read_configuration_csv(path)
    workbook_path = Path(workbook_path_text) if workbook_path_text else None
    manual_sheet_names = _xlsx_sheet_names(str(workbook_path)) if workbook_path is not None else frozenset()
    model_max_by_column = configuration_metric_by_column(rows, "MODEL_MAX_SH")
    status_by_column = configuration_metric_by_column(rows, "MODEL_STATUS")
    seconds_by_column = configuration_metric_by_column(rows, "MODEL_SECONDS")
    supported_shifts = {shift.code for shift in DEFAULT_SHIFTS}
    configuration_columns_list = list(configuration_columns(rows))
    manual_sector_hours_by_name = {
        name: _manual_schedule_max_sector_hours(
            _manual_schedule_for_workbook(str(workbook_path), int(workbook_modified_at_ns or 0), name)
        )
        for _, name in configuration_columns_list
        if workbook_path is not None and name in manual_sheet_names
    }
    configurations = [
        configuration_summary(
            parse_configuration(rows, column_index, name, supported_shifts),
            model_max_sector_hours=model_max_by_column.get(column_index),
            manual_sector_hours=manual_sector_hours_by_name.get(name),
            model_status=status_by_column.get(column_index),
            model_seconds=seconds_by_column.get(column_index),
            has_manual_schedule=name in manual_sheet_names,
        )
        for column_index, name in configuration_columns_list
    ]
    configurations = [
        item
        for item in configurations
        if int(item["parsed_total"]) > 0 or item["model_max_sector_hours"] is not None
    ]
    return tuple(configurations)


def manual_configuration_library() -> dict[str, object]:
    path = selected_configuration_library_path()
    workbook_path = selected_configuration_workbook_path()
    user_configurations = [_user_configuration_summary(record) for record in _read_user_configuration_records()]
    if path is None:
        return {
            "source_path": None,
            "workbook_path": None,
            "user_source_path": str(user_configuration_library_path()),
            "configurations": user_configurations,
        }

    configurations = _base_manual_configuration_summaries(
        str(path),
        path.stat().st_mtime_ns,
        str(workbook_path) if workbook_path else None,
        workbook_path.stat().st_mtime_ns if workbook_path else None,
    )
    return {
        "source_path": str(path),
        "workbook_path": str(workbook_path) if workbook_path else None,
        "user_source_path": str(user_configuration_library_path()),
        "configurations": [*user_configurations, *configurations],
    }


def manual_configuration_detail(configuration_id: str | int) -> dict[str, object]:
    configuration_id_text = str(configuration_id)
    if configuration_id_text.startswith("user:"):
        return user_configuration_detail(configuration_id_text)

    path = selected_configuration_library_path()
    if path is None:
        raise ValueError("Baza ročnih konfiguracij ni najdena.")

    rows = read_configuration_csv(path)
    names = dict(configuration_columns(rows))
    try:
        column_index = int(configuration_id_text)
    except ValueError as exc:
        raise ValueError("Ročna konfiguracija ne obstaja.") from exc
    name = names.get(column_index)
    if name is None:
        raise ValueError("Ročna konfiguracija ne obstaja.")

    model_max_by_column = configuration_metric_by_column(rows, "MODEL_MAX_SH")
    status_by_column = configuration_metric_by_column(rows, "MODEL_STATUS")
    seconds_by_column = configuration_metric_by_column(rows, "MODEL_SECONDS")
    supported_shifts = {shift.code for shift in DEFAULT_SHIFTS}
    configuration = parse_configuration(rows, column_index, name, supported_shifts)
    workbook_path = selected_configuration_workbook_path()
    manual_schedule = manual_schedule_for_configuration(name)
    summary = configuration_summary(
        configuration,
        model_max_sector_hours=model_max_by_column.get(column_index),
        manual_sector_hours=_manual_schedule_max_sector_hours(manual_schedule),
        model_status=status_by_column.get(column_index),
        model_seconds=seconds_by_column.get(column_index),
        has_manual_schedule=manual_schedule is not None,
    )
    return {
        **summary,
        "source_path": str(path),
        "workbook_path": str(workbook_path) if workbook_path else None,
        "fixed_staff": [item.model_dump() for item in configuration.fixed_staff],
        "officer_staff": [item.model_dump() for item in configuration.officer_staff],
        "staff_rows": staff_rows_for_configuration(configuration),
        "manual_schedule": manual_schedule,
    }


def _configuration_has_fmp(detail: dict[str, object]) -> bool:
    fixed_staff = detail.get("fixed_staff")
    if isinstance(fixed_staff, list):
        for item in fixed_staff:
            if isinstance(item, dict) and item.get("role") == "FMP":
                return True
    manual_schedule = detail.get("manual_schedule")
    if isinstance(manual_schedule, dict):
        people = manual_schedule.get("people")
        if isinstance(people, list):
            return any(isinstance(person, dict) and person.get("role") == "FMP" for person in people)
    return False


def _configuration_fmp_shift(detail: dict[str, object]) -> str | None:
    fixed_staff = detail.get("fixed_staff")
    if isinstance(fixed_staff, list):
        for item in fixed_staff:
            if isinstance(item, dict) and item.get("role") == "FMP":
                shift = item.get("shift")
                if isinstance(shift, str) and shift.strip():
                    return shift.strip()
    manual_schedule = detail.get("manual_schedule")
    if isinstance(manual_schedule, dict):
        people = manual_schedule.get("people")
        if isinstance(people, list):
            for person in people:
                if isinstance(person, dict) and person.get("role") == "FMP":
                    shift = person.get("shift")
                    if isinstance(shift, str) and shift.strip():
                        return shift.strip()
    return None


def _license_ratio_from_detail(detail: dict[str, object]) -> dict[str, int]:
    raw_counts = detail.get("license_counts")
    counts = raw_counts if isinstance(raw_counts, dict) else {}
    ratio = {
        license_name: max(0, int(counts.get(license_name, 0) or 0))
        for license_name in LICENSES
    }
    if sum(ratio.values()) <= 0:
        return {"FL": 50, "APS": 0, "ACS": 50}
    return ratio


def _result_license_counts_for_compare(result: CalculatorResponse) -> dict[str, int]:
    counts = Counter(person.license for person in result.people if person.license in LICENSES)
    return {license_name: counts[license_name] for license_name in LICENSES}


def _role_hours_from_result(result: CalculatorResponse) -> dict[str, int]:
    role_hours: dict[str, int] = {role: 0 for role in ("V1", "V2", "V3", "FMP")}
    for person in result.people:
        role = (person.role or "").upper()
        if role in role_hours:
            role_hours[role] += person.sector_hours
    return role_hours


def _role_hours_from_detail(detail: dict[str, object]) -> dict[str, int]:
    role_hours: dict[str, int] = {role: 0 for role in ("V1", "V2", "V3", "FMP")}
    manual_schedule = detail.get("manual_schedule")
    if isinstance(manual_schedule, dict):
        people = manual_schedule.get("people")
        if isinstance(people, list):
            for person in people:
                if not isinstance(person, dict):
                    continue
                role = str(person.get("role") or "").upper()
                if role not in role_hours:
                    continue
                sector_hours = _excel_number(person.get("sector_hours"))
                role_hours[role] += int(sector_hours or 0)
    return role_hours


def _sector_counts_from_detail(detail: dict[str, object]) -> list[int]:
    manual_schedule = detail.get("manual_schedule")
    counts = _manual_requested_sector_counts(manual_schedule if isinstance(manual_schedule, dict) else None)
    return counts or []


def _sector_counts_from_result(result: CalculatorResponse) -> list[int]:
    return [hour.open_sectors for hour in result.hourly_coverage]


def _workload_profile_from_result(result: CalculatorResponse) -> list[int]:
    return sorted((person.sector_hours for person in result.people), reverse=True)


def _workload_profile_from_detail(detail: dict[str, object]) -> list[int]:
    manual_schedule = detail.get("manual_schedule")
    if not isinstance(manual_schedule, dict):
        return []
    people = manual_schedule.get("people")
    if not isinstance(people, list):
        return []
    hours: list[int] = []
    for person in people:
        if not isinstance(person, dict):
            continue
        sector_hours = _excel_number(person.get("sector_hours"))
        if sector_hours is not None:
            hours.append(int(sector_hours))
    return sorted(hours, reverse=True)


def _sequence_diff(first: list[int], second: list[int]) -> int:
    length = max(len(first), len(second))
    diff = 0
    for index in range(length):
        diff += abs((first[index] if index < len(first) else 0) - (second[index] if index < len(second) else 0))
    return diff


def _license_diff(left: dict[str, int], right: dict[str, int]) -> int:
    return sum(abs(left.get(license_name, 0) - right.get(license_name, 0)) for license_name in LICENSES)


def _role_diff(left: dict[str, int], right: dict[str, int]) -> int:
    return sum(abs(left.get(role, 0) - right.get(role, 0)) for role in ("V1", "V2", "V3", "FMP"))


def _candidate_similarity_payload(
    result: CalculatorResponse,
    detail: dict[str, object],
) -> dict[str, object]:
    result_sector_counts = _sector_counts_from_result(result)
    detail_sector_counts = _sector_counts_from_detail(detail)
    result_license_counts = _result_license_counts_for_compare(result)
    detail_license_counts = detail.get("license_counts") if isinstance(detail.get("license_counts"), dict) else {}
    detail_license_counts = {
        license_name: int(detail_license_counts.get(license_name, 0) or 0)
        for license_name in LICENSES
    }
    result_role_hours = _role_hours_from_result(result)
    detail_role_hours = _role_hours_from_detail(detail)
    sector_profile_diff = _sequence_diff(result_sector_counts, detail_sector_counts)
    manual_sector_hours = _manual_schedule_max_sector_hours(
        detail.get("manual_schedule") if isinstance(detail.get("manual_schedule"), dict) else None
    )
    candidate_sector_hours = int(manual_sector_hours or detail.get("model_max_sector_hours") or sum(detail_sector_counts) or 0)
    signed_sh_diff = result.max_sector_hours - candidate_sector_hours
    sh_diff = abs(signed_sh_diff)
    people_diff = abs(result.planned_people - int(detail.get("parsed_total") or 0))
    license_delta = _license_diff(result_license_counts, detail_license_counts)
    role_hours_diff = _role_diff(result_role_hours, detail_role_hours)
    workload_diff = _sequence_diff(_workload_profile_from_result(result), _workload_profile_from_detail(detail))
    penalty = (
        sh_diff * 1.2
        + people_diff * 8
        + license_delta * 4
        + sector_profile_diff * 2
        + role_hours_diff * 0.8
        + workload_diff * 0.35
    )
    similarity = max(0, min(100, round(100 - penalty)))
    return {
        "id": detail.get("id"),
        "name": detail.get("name"),
        "source_type": detail.get("source_type", "excel"),
        "source_label": detail.get("source_label"),
        "similarity": similarity,
        "sh_diff": signed_sh_diff,
        "people_diff": result.planned_people - int(detail.get("parsed_total") or 0),
        "license_diff": {
            license_name: result_license_counts[license_name] - detail_license_counts[license_name]
            for license_name in LICENSES
        },
        "role_hours_diff": {
            role: result_role_hours[role] - detail_role_hours[role]
            for role in ("V1", "V2", "V3", "FMP")
        },
        "sector_profile_diff": sector_profile_diff,
        "workload_diff": workload_diff,
        "candidate_sector_hours": candidate_sector_hours,
        "candidate_people": int(detail.get("parsed_total") or 0),
    }


def compare_result_to_configurations(payload: CompareConfigurationRequest) -> dict[str, object]:
    library = manual_configuration_library()
    matches: list[dict[str, object]] = []
    for summary in library.get("configurations", []):
        if not isinstance(summary, dict):
            continue
        configuration_id = summary.get("id")
        if configuration_id is None:
            continue
        try:
            detail = manual_configuration_detail(str(configuration_id))
        except ValueError:
            continue
        matches.append(_candidate_similarity_payload(payload.result, detail))

    matches.sort(
        key=lambda item: (
            -int(item["similarity"]),
            abs(int(item["people_diff"])),
            abs(int(item["sh_diff"])),
            str(item["name"]),
        )
    )
    top_matches = matches[: payload.limit]
    duplicate_warning = None
    if top_matches and int(top_matches[0]["similarity"]) >= 92:
        duplicate_warning = (
            f"Najbolj podobna obstoječa konfiguracija je {top_matches[0]['name']} "
            f"({top_matches[0]['similarity']}%)."
        )
    return {
        "result": {
            "sector_hours": payload.result.max_sector_hours,
            "people": payload.result.planned_people,
            "license_counts": _result_license_counts_for_compare(payload.result),
            "role_hours": _role_hours_from_result(payload.result),
            "sector_counts": _sector_counts_from_result(payload.result),
        },
        "duplicate_warning": duplicate_warning,
        "matches": top_matches,
    }


def manual_configuration_one_down(
    configuration_id: str | int,
    *,
    time_limit_seconds: int = 8,
    settings_override: CalculatorSettings | None = None,
    progress_callback: ProgressCallback | None = None,
    cancel_callback: CancelCallback | None = None,
    variant_result_callback: VariantResultCallback | None = None,
) -> dict[str, object]:
    from .pattern_core import _solve_pattern_model, load_or_build_pattern_library

    detail = manual_configuration_detail(configuration_id)
    manual_schedule = detail.get("manual_schedule")
    if not isinstance(manual_schedule, dict):
        raise ValueError("Konfiguracija nima urnega Excel/user prikaza za one-down preverjanje.")
    requested_sector_counts = _manual_requested_sector_counts(manual_schedule)
    if requested_sector_counts is None:
        raise ValueError("Konfiguracija nima 24-urnega profila odprtosti.")

    manual_people = int(detail.get("parsed_total") or 0)
    target_people = manual_people - 1
    if target_people < 1:
        raise ValueError("One-down preverjanje zahteva vsaj 2 človeka v osnovni konfiguraciji.")

    ratio = _license_ratio_from_detail(detail)
    include_fmp = _configuration_has_fmp(detail)
    fmp_shift = _configuration_fmp_shift(detail) or "A9"
    settings = settings_for_manual_schedule_evaluation(
        manual_schedule,
        time_limit_seconds,
        base_settings=settings_override,
    )
    officer_staff = _officer_staff_from_detail(detail, settings)
    officer_count = sum(item.count for item in officer_staff)
    non_officer_target_people = max(0, target_people - officer_count)
    role_plus_one_settings = settings.model_copy(
        update={
            "v1_sector_limit": min(24, settings.v1_sector_limit + 1),
            "v2_sector_limit": min(24, settings.v2_sector_limit + 1),
            "v3_sector_limit": min(24, settings.v3_sector_limit + 1),
            "fmp_sector_limit": min(24, settings.fmp_sector_limit + 1),
        }
    )
    role_open_settings = settings.model_copy(
        update={
            "v1_sector_limit": 24,
            "v2_sector_limit": 24,
            "v3_sector_limit": 24,
            "fmp_sector_limit": 24,
        }
    )

    def make_request(
        current_ratio: dict[str, int],
        current_include_fmp: bool,
        current_settings: CalculatorSettings,
    ) -> CalculatorRequest:
        return CalculatorRequest(
            calculation_mode="demand_to_staff",
            total_people=non_officer_target_people,
            fl_count=0,
            aps_count=0,
            acs_count=0,
            include_fmp=current_include_fmp,
            fmp_shift_mode="fixed" if current_include_fmp else "auto",
            fmp_shift=fmp_shift,
            settings=current_settings,
            requested_sector_counts=requested_sector_counts,
            fixed_staff=[],
            locked_staff=[],
            officer_staff=officer_staff,
            office_pool=[],
            license_mix_percent={
                "fl": current_ratio["FL"],
                "aps": current_ratio["APS"],
                "acs": current_ratio["ACS"],
            },
            include_pareto=False,
            prefer_minimal_fl=False,
        )

    default_ratio = {"FL": 50, "APS": 0, "ACS": 50}
    variants: list[tuple[str, str, dict[str, int], bool, CalculatorSettings]] = [
        ("same_inputs", "Isti vhodni podatki", ratio, include_fmp, settings),
        ("ratio_50_50", "Spremenjeno razmerje 50/0/50", default_ratio, include_fmp, settings),
        ("roles_plus_one", "Vloge +1 sektor/uro", ratio, include_fmp, role_plus_one_settings),
        ("ratio_50_50_roles_plus_one", "50/0/50 + vloge +1", default_ratio, include_fmp, role_plus_one_settings),
        ("ratio_50_50_roles_open", "50/0/50 + odprti limiti vlog", default_ratio, include_fmp, role_open_settings),
    ]
    if include_fmp:
        variants.extend(
            [
                ("without_fmp_ratio_50_50", "Zadnja možnost: brez FMP, 50/0/50", default_ratio, False, settings),
                (
                    "without_fmp_ratio_50_50_roles_open",
                    "Zadnja možnost: brez FMP + odprti limiti vlog",
                    default_ratio,
                    False,
                    role_open_settings,
                ),
            ]
        )

    variant_results: list[dict[str, object]] = []
    best_variant: dict[str, object] | None = None

    total_variants = len(variants)
    for index, (variant_key, variant_label, variant_ratio, variant_include_fmp, variant_settings) in enumerate(variants, start=1):
        _check_variant_cancel(cancel_callback)
        variant_start = 8 + round(((index - 1) / total_variants) * 84)
        variant_end = 8 + round((index / total_variants) * 84)
        if progress_callback is not None:
            progress_callback(
                variant_start,
                f"One-down preizkuša varianto {index}/{total_variants}: {variant_label}.",
            )
        request = make_request(variant_ratio, variant_include_fmp, variant_settings)
        pattern_payload: dict[str, object] = {
            "checked": False,
            "feasible": None,
            "status": None,
            "pattern_count": None,
            "cache_status": None,
            "message": None,
        }
        if request.officer_staff or request.include_fmp:
            pattern_payload.update(
                {
                    "checked": True,
                    "message": (
                        "Pattern predpreverjanje je preskočeno, ker konfiguracija vsebuje FMP ali konkretne office izmene; "
                        "FMP/Vi pravilo preveri CP-SAT."
                    ),
                }
            )
        else:
            try:
                pattern_request = request.model_copy(update={"total_people": 0})
                library = load_or_build_pattern_library(pattern_request)
                pattern_result = _solve_pattern_model(
                    library,
                    pattern_request,
                    requested_sector_counts,
                    target_people,
                    optimize_quality=False,
                )
                pattern_payload.update(
                    {
                        "checked": True,
                        "feasible": pattern_result is not None,
                        "status": pattern_result.status if pattern_result else "INFEASIBLE",
                        "pattern_count": library.pattern_count,
                        "cache_status": library.cache_status,
                    }
                )
            except Exception as exc:  # pragma: no cover - surfaced to UI.
                pattern_payload.update({"checked": True, "message": str(exc)})

        started_at = time.perf_counter()
        result = calculate(
            request,
            progress_callback=_variant_progress_callback(
                progress_callback,
                base_progress=variant_start,
                span=max(1, variant_end - variant_start),
                variant_label=variant_label,
            ),
            cancel_callback=cancel_callback,
        )
        if variant_result_callback is not None:
            variant_result_callback(result)
        full_coverage = result.feasible and result.missing_sector_hours == 0
        variant_payload = {
            "variant": variant_key,
            "variant_label": variant_label,
            "license_ratio": variant_ratio,
            "include_fmp": variant_include_fmp,
            "role_limits": {
                "v1": variant_settings.v1_sector_limit,
                "v2": variant_settings.v2_sector_limit,
                "v3": variant_settings.v3_sector_limit,
                "fmp": variant_settings.fmp_sector_limit,
            },
            "pattern": pattern_payload,
            "calculator": {
                "feasible": full_coverage,
                "status": "covered" if full_coverage else "shortfall",
                "planned_people": result.planned_people,
                "active_people": result.active_people,
                "model_sector_hours": result.max_sector_hours,
                "missing_sector_hours": result.missing_sector_hours,
                "solver_status": result.solver_status,
                "elapsed_seconds": round(time.perf_counter() - started_at, 3),
                "result": result.model_dump(),
            },
        }
        variant_results.append(variant_payload)
        if _is_better_variant(variant_payload, best_variant):
            best_variant = variant_payload
            if progress_callback is not None:
                progress_callback(
                    variant_end,
                    f"Nova najboljša one-down varianta: {variant_label}, "
                    f"{result.max_sector_hours}/{result.requested_sector_hours} SH, manjka {result.missing_sector_hours}.",
                )
        if full_coverage:
            if progress_callback is not None:
                progress_callback(95, f"One-down polna pokritost najdena z varianto: {variant_label}.")
            break

    if best_variant is None:
        raise ValueError("One-down preverjanje ni uspelo zagnati nobene variante.")
    selected_calculator = best_variant["calculator"]
    return {
        "id": detail["id"],
        "name": detail["name"],
        "source_type": detail.get("source_type", "excel"),
        "manual_people": manual_people,
        "target_people": target_people,
        "requested_sector_hours": sum(requested_sector_counts),
        "license_ratio": best_variant["license_ratio"],
        "include_fmp": best_variant["include_fmp"],
        "selected_variant": best_variant["variant"],
        "selected_variant_label": best_variant["variant_label"],
        "pattern": best_variant["pattern"],
        "calculator": selected_calculator,
        "variants": variant_results,
    }


def _completion_people_limit(request: CalculatorRequest, current_result: CalculatorResponse | None) -> int:
    if current_result is not None and current_result.planned_people > 0:
        officer_count = sum(item.count for item in request.officer_staff)
        return max(1, current_result.planned_people - officer_count)
    if request.total_people > 0:
        return request.total_people
    raise ValueError("Dopolnitev potrebuje trenutno rešitev ali vpisan limit ljudi.")


def _result_license_counts(current_result: CalculatorResponse | None) -> dict[str, int] | None:
    if current_result is None:
        return None
    counts = Counter(
        person.license
        for person in current_result.people
        if person.license in LICENSES and person.source != "officer"
    )
    total = sum(counts.values())
    if total <= 0:
        return None
    return {license_name: counts[license_name] for license_name in LICENSES}


def _scaled_license_counts(weights: dict[str, int], total_people: int) -> dict[str, int]:
    weight_sum = sum(max(0, value) for value in weights.values())
    if weight_sum <= 0:
        weights = {"FL": 50, "APS": 0, "ACS": 50}
        weight_sum = 100

    raw_parts = {
        license_name: (max(0, weights.get(license_name, 0)) / weight_sum) * total_people
        for license_name in LICENSES
    }
    counts = {license_name: int(math.floor(value)) for license_name, value in raw_parts.items()}
    remainder = total_people - sum(counts.values())
    for license_name in sorted(LICENSES, key=lambda name: raw_parts[name] - counts[name], reverse=True):
        if remainder <= 0:
            break
        counts[license_name] += 1
        remainder -= 1
    return counts


def _request_license_ratio(request: CalculatorRequest, current_result: CalculatorResponse | None) -> dict[str, int]:
    if request.license_mix_percent is not None:
        ratio = {
            "FL": request.license_mix_percent.fl,
            "APS": request.license_mix_percent.aps,
            "ACS": request.license_mix_percent.acs,
        }
        if sum(ratio.values()) > 0:
            return ratio

    result_counts = _result_license_counts(current_result)
    if result_counts is not None and sum(result_counts.values()) > 0:
        return result_counts

    request_counts = {
        "FL": request.fl_count,
        "APS": request.aps_count,
        "ACS": request.acs_count,
    }
    if sum(request_counts.values()) > 0:
        return request_counts
    return {"FL": 50, "APS": 0, "ACS": 50}


def _completion_settings_variants(
    settings: CalculatorSettings,
    time_limit_seconds: int,
) -> tuple[CalculatorSettings, CalculatorSettings, CalculatorSettings]:
    base_settings = settings.model_copy(
        update={
            "cp_sat_time_limit_seconds": time_limit_seconds,
            "cp_sat_no_improvement_seconds": 0,
            "cp_sat_acceptable_sector_gap": 0,
        }
    )
    role_plus_one_settings = base_settings.model_copy(
        update={
            "v1_sector_limit": min(24, base_settings.v1_sector_limit + 1),
            "v2_sector_limit": min(24, base_settings.v2_sector_limit + 1),
            "v3_sector_limit": min(24, base_settings.v3_sector_limit + 1),
            "fmp_sector_limit": min(24, base_settings.fmp_sector_limit + 1),
        }
    )
    role_open_settings = base_settings.model_copy(
        update={
            "v1_sector_limit": 24,
            "v2_sector_limit": 24,
            "v3_sector_limit": 24,
            "fmp_sector_limit": 24,
        }
    )
    return base_settings, role_plus_one_settings, role_open_settings


def _completion_request_for_variant(
    base_request: CalculatorRequest,
    *,
    target_people: int,
    settings: CalculatorSettings,
    include_fmp: bool,
    license_ratio: dict[str, int],
    current_result: CalculatorResponse | None,
    preserve_exact_counts: bool,
) -> CalculatorRequest:
    request_payload = base_request.model_dump()
    if preserve_exact_counts:
        result_counts = _result_license_counts(current_result)
        if result_counts is not None and sum(result_counts.values()) == target_people:
            counts = result_counts
        elif base_request.fl_count + base_request.aps_count + base_request.acs_count == target_people:
            counts = {
                "FL": base_request.fl_count,
                "APS": base_request.aps_count,
                "ACS": base_request.acs_count,
            }
        else:
            counts = _scaled_license_counts(license_ratio, target_people)
        request_payload.update(
            {
                "calculation_mode": "staff_to_coverage",
                "total_people": target_people,
                "fl_count": counts["FL"],
                "aps_count": counts["APS"],
                "acs_count": counts["ACS"],
                "include_fmp": include_fmp,
                "settings": settings.model_dump(),
                "license_mix_percent": None,
                "include_pareto": False,
            }
        )
        return CalculatorRequest(**request_payload)

    request_payload.update(
        {
            "calculation_mode": "demand_to_staff",
            "total_people": target_people,
            "fl_count": 0,
            "aps_count": 0,
            "acs_count": 0,
            "include_fmp": include_fmp,
            "settings": settings.model_dump(),
            "license_mix_percent": {
                "fl": license_ratio["FL"],
                "aps": license_ratio["APS"],
                "acs": license_ratio["ACS"],
            },
            "include_pareto": False,
        }
    )
    return CalculatorRequest(**request_payload)


def _calculator_variant_payload(
    *,
    variant_key: str,
    variant_label: str,
    request: CalculatorRequest,
    license_ratio: dict[str, int] | None,
    started_at: float,
    result: CalculatorResponse,
) -> dict[str, object]:
    full_coverage = result.feasible and result.missing_sector_hours == 0
    return {
        "variant": variant_key,
        "variant_label": variant_label,
        "license_ratio": license_ratio,
        "include_fmp": request.include_fmp,
        "calculation_mode": request.calculation_mode,
        "role_limits": {
            "v1": request.settings.v1_sector_limit,
            "v2": request.settings.v2_sector_limit,
            "v3": request.settings.v3_sector_limit,
            "fmp": request.settings.fmp_sector_limit,
        },
        "calculator": {
            "feasible": full_coverage,
            "status": "covered" if full_coverage else "shortfall",
            "planned_people": result.planned_people,
            "active_people": result.active_people,
            "model_sector_hours": result.max_sector_hours,
            "missing_sector_hours": result.missing_sector_hours,
            "solver_status": result.solver_status,
            "elapsed_seconds": round(time.perf_counter() - started_at, 3),
            "result": result.model_dump(),
        },
    }


ProgressCallback = Callable[[int, str], None]
CancelCallback = Callable[[], bool]
VariantResultCallback = Callable[[CalculatorResponse], None]


def _check_variant_cancel(cancel_callback: CancelCallback | None) -> None:
    if cancel_callback is not None and cancel_callback():
        raise CalculationCancelled("Optimizacija variant je bila preklicana.")


def _variant_progress_callback(
    progress_callback: ProgressCallback | None,
    *,
    base_progress: int,
    span: int,
    variant_label: str,
) -> ProgressCallback | None:
    if progress_callback is None:
        return None

    def report(progress: int, message: str) -> None:
        mapped_progress = base_progress + round((max(0, min(100, progress)) / 100) * span)
        if message.startswith(CALC_PHASE_PROGRESS_PREFIX):
            try:
                payload = json.loads(message[len(CALC_PHASE_PROGRESS_PREFIX):])
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                for key in ("message", "detail"):
                    if isinstance(payload.get(key), str):
                        payload[key] = f"{variant_label}: {payload[key]}"
                if isinstance(payload.get("label"), str):
                    payload["label"] = f"{variant_label} · {payload['label']}"
                progress_callback(
                    mapped_progress,
                    CALC_PHASE_PROGRESS_PREFIX + json.dumps(payload, ensure_ascii=False),
                )
                return
        progress_callback(mapped_progress, f"{variant_label}: {message}")

    return report


def _is_better_variant(
    current_variant: dict[str, object],
    best_variant: dict[str, object] | None,
) -> bool:
    if best_variant is None:
        return True
    current_calculator = current_variant["calculator"]
    best_calculator = best_variant["calculator"]
    if bool(current_calculator["feasible"]) != bool(best_calculator["feasible"]):
        return bool(current_calculator["feasible"])
    if int(current_calculator["model_sector_hours"]) != int(best_calculator["model_sector_hours"]):
        return int(current_calculator["model_sector_hours"]) > int(best_calculator["model_sector_hours"])
    if int(current_calculator["missing_sector_hours"]) != int(best_calculator["missing_sector_hours"]):
        return int(current_calculator["missing_sector_hours"]) < int(best_calculator["missing_sector_hours"])
    return int(current_calculator["planned_people"]) < int(best_calculator["planned_people"])


def _run_calculator_variants(
    variants: list[tuple[str, str, CalculatorRequest, dict[str, int] | None]],
    *,
    progress_callback: ProgressCallback | None = None,
    cancel_callback: CancelCallback | None = None,
    variant_result_callback: VariantResultCallback | None = None,
    start_progress: int = 8,
    end_progress: int = 92,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    if not variants:
        raise ValueError("Ni pripravljenih variant za izračun.")

    variant_results: list[dict[str, object]] = []
    best_variant: dict[str, object] | None = None
    total_variants = len(variants)
    progress_span = max(1, end_progress - start_progress)

    for index, (variant_key, variant_label, request, license_ratio) in enumerate(variants, start=1):
        _check_variant_cancel(cancel_callback)
        variant_start = start_progress + round(((index - 1) / total_variants) * progress_span)
        variant_end = start_progress + round((index / total_variants) * progress_span)
        if progress_callback is not None:
            progress_callback(
                variant_start,
                f"Preizkušam varianto {index}/{total_variants}: {variant_label}.",
            )
        started_at = time.perf_counter()
        result = calculate(
            request,
            progress_callback=_variant_progress_callback(
                progress_callback,
                base_progress=variant_start,
                span=max(1, variant_end - variant_start),
                variant_label=variant_label,
            ),
            cancel_callback=cancel_callback,
        )
        if variant_result_callback is not None:
            variant_result_callback(result)
        variant_payload = _calculator_variant_payload(
            variant_key=variant_key,
            variant_label=variant_label,
            request=request,
            license_ratio=license_ratio,
            started_at=started_at,
            result=result,
        )
        variant_results.append(variant_payload)
        if _is_better_variant(variant_payload, best_variant):
            best_variant = variant_payload
            if progress_callback is not None:
                progress_callback(
                    variant_end,
                    f"Nova najboljša varianta: {variant_label}, "
                    f"{result.max_sector_hours}/{result.requested_sector_hours} SH, manjka {result.missing_sector_hours}.",
                )
        if result.feasible and result.missing_sector_hours == 0:
            if progress_callback is not None:
                progress_callback(95, f"Polna pokritost najdena z varianto: {variant_label}.")
            break

    if best_variant is None:
        raise ValueError("Optimizacija variant ni uspela zagnati nobene variante.")
    return best_variant, variant_results


def complete_configuration(
    payload: CompleteConfigurationRequest,
    *,
    progress_callback: ProgressCallback | None = None,
    cancel_callback: CancelCallback | None = None,
    variant_result_callback: VariantResultCallback | None = None,
) -> dict[str, object]:
    base_request = payload.request
    if base_request.requested_sector_counts is None:
        raise ValueError("Dopolnitev potrebuje ciljno odprtost po urah.")
    requested_sector_hours = sum(base_request.requested_sector_counts)
    if requested_sector_hours <= 0:
        raise ValueError("Dopolnitev potrebuje vsaj eno zahtevano sektorsko uro.")

    time_limit_seconds = max(1, min(120, payload.time_limit_seconds))
    target_people = _completion_people_limit(base_request, payload.current_result)
    current_ratio = _request_license_ratio(base_request, payload.current_result)
    default_ratio = {"FL": 50, "APS": 0, "ACS": 50}
    more_fl_ratio = {"FL": 60, "APS": 0, "ACS": 40}
    more_acs_ratio = {"FL": 40, "APS": 0, "ACS": 60}
    base_settings, role_plus_one_settings, role_open_settings = _completion_settings_variants(
        base_request.settings,
        time_limit_seconds,
    )

    variants: list[tuple[str, str, CalculatorSettings, bool, dict[str, int], bool]] = [
        ("same_inputs", "Isti vhodni podatki", base_settings, base_request.include_fmp, current_ratio, base_request.calculation_mode == "staff_to_coverage"),
        ("ratio_current", "Trenutno razmerje licenc", base_settings, base_request.include_fmp, current_ratio, False),
        ("ratio_50_50", "Razmerje 50/0/50", base_settings, base_request.include_fmp, default_ratio, False),
        ("ratio_more_fl", "Več FL: 60/0/40", base_settings, base_request.include_fmp, more_fl_ratio, False),
        ("ratio_more_acs", "Več ACS: 40/0/60", base_settings, base_request.include_fmp, more_acs_ratio, False),
        ("roles_plus_one", "Vloge +1 sektor/uro", role_plus_one_settings, base_request.include_fmp, current_ratio, False),
        ("ratio_50_50_roles_plus_one", "50/0/50 + vloge +1", role_plus_one_settings, base_request.include_fmp, default_ratio, False),
        ("ratio_more_fl_roles_plus_one", "60/0/40 + vloge +1", role_plus_one_settings, base_request.include_fmp, more_fl_ratio, False),
        ("ratio_more_acs_roles_plus_one", "40/0/60 + vloge +1", role_plus_one_settings, base_request.include_fmp, more_acs_ratio, False),
        ("ratio_50_50_roles_open", "50/0/50 + odprti limiti vlog", role_open_settings, base_request.include_fmp, default_ratio, False),
    ]
    if base_request.include_fmp:
        variants.extend(
            [
                ("without_fmp_ratio_50_50", "Zadnja možnost: brez FMP, 50/0/50", base_settings, False, default_ratio, False),
                (
                    "without_fmp_ratio_50_50_roles_open",
                    "Zadnja možnost: brez FMP + odprti limiti vlog",
                    role_open_settings,
                    False,
                    default_ratio,
                    False,
                ),
            ]
        )

    runnable_variants: list[tuple[str, str, CalculatorRequest, dict[str, int] | None]] = []
    for variant_key, variant_label, variant_settings, variant_include_fmp, variant_ratio, preserve_counts in variants:
        request = _completion_request_for_variant(
            base_request,
            target_people=target_people,
            settings=variant_settings,
            include_fmp=variant_include_fmp,
            license_ratio=variant_ratio,
            current_result=payload.current_result,
            preserve_exact_counts=preserve_counts,
        )
        runnable_variants.append((variant_key, variant_label, request, None if preserve_counts else variant_ratio))

    best_variant, variant_results = _run_calculator_variants(
        runnable_variants,
        progress_callback=progress_callback,
        cancel_callback=cancel_callback,
        variant_result_callback=variant_result_callback,
        start_progress=8,
        end_progress=94,
    )

    selected_calculator = best_variant["calculator"]
    selected_result = selected_calculator["result"]
    if isinstance(selected_result, dict):
        notes = selected_result.get("notes")
        if isinstance(notes, list):
            notes.append(f"Dopolnitev do polne konfiguracije je izbrala vzvod: {best_variant['variant_label']}.")
    return {
        "target_people": target_people,
        "requested_sector_hours": requested_sector_hours,
        "selected_variant": best_variant["variant"],
        "selected_variant_label": best_variant["variant_label"],
        "license_ratio": best_variant["license_ratio"],
        "include_fmp": best_variant["include_fmp"],
        "role_limits": best_variant["role_limits"],
        "calculator": selected_calculator,
        "variants": variant_results,
    }


def evaluate_configuration(
    configuration: ParsedConfiguration,
    settings: CalculatorSettings,
    requested_sector_counts: list[int] | None = None,
) -> int | None:
    if configuration.unsupported_rows or configuration.parsed_total <= 0:
        return None
    result = calculate(request_for_configuration(configuration, settings, requested_sector_counts))
    return result.max_sector_hours


def _manual_requested_sector_counts(manual_schedule: dict[str, object] | None) -> list[int] | None:
    if manual_schedule is None:
        return None
    hourly_coverage = manual_schedule.get("hourly_coverage")
    if not isinstance(hourly_coverage, list) or len(hourly_coverage) != 24:
        return None
    counts: list[int] = []
    for hour in hourly_coverage:
        if not isinstance(hour, dict):
            return None
        open_sectors = _excel_number(hour.get("open_sectors"))
        if open_sectors is None:
            return None
        counts.append(int(open_sectors))
    return counts


def _manual_assignment_label(assignment: object) -> str | None:
    if not isinstance(assignment, dict):
        return None
    lower = _clean_excel_label(assignment.get("lower_worker"))
    upper = _clean_excel_label(assignment.get("upper_worker"))
    if lower is None and upper is None:
        return None
    return f"{lower or '—'} / {upper or '—'}"


def _model_assignment_label(assignment: object) -> str | None:
    if assignment is None:
        return None
    lower = getattr(assignment, "lower_worker", None)
    upper = getattr(assignment, "upper_worker", None)
    if lower is None and upper is None:
        return None
    return f"{lower or '—'} / {upper or '—'}"


def _manual_sector_map(hour: object) -> dict[str, str]:
    if not isinstance(hour, dict):
        return {}
    sector_workers = hour.get("sector_workers")
    if not isinstance(sector_workers, list):
        return {}
    sector_map: dict[str, str] = {}
    for assignment in sector_workers:
        if not isinstance(assignment, dict):
            continue
        sector_name = _clean_excel_label(assignment.get("sector_name"))
        label = _manual_assignment_label(assignment)
        if sector_name and label:
            sector_map[sector_name] = label
    return sector_map


def _model_sector_map(hour: object) -> dict[str, str]:
    sector_workers = getattr(hour, "sector_workers", None)
    if not isinstance(sector_workers, list):
        return {}
    sector_map: dict[str, str] = {}
    for assignment in sector_workers:
        if assignment is None:
            continue
        sector_name = getattr(assignment, "sector_name", None)
        label = _model_assignment_label(assignment)
        if sector_name and label:
            sector_map[str(sector_name)] = label
    return sector_map


def _sector_cell_status(manual_label: str | None, model_label: str | None) -> str:
    if manual_label and model_label:
        return "same_workers" if manual_label == model_label else "same_sector"
    if manual_label:
        return "manual_only"
    if model_label:
        return "model_only"
    return "closed"


def _hourly_comparison(
    manual_schedule: dict[str, object] | None,
    result,
    max_sectors: int,
) -> list[dict[str, object]]:
    if manual_schedule is None:
        return []
    manual_hours = manual_schedule.get("hourly_coverage")
    if not isinstance(manual_hours, list):
        return []
    sector_names = sector_display_names_for_max(max_sectors)
    rows: list[dict[str, object]] = []
    for slot in range(min(len(manual_hours), len(result.hourly_coverage))):
        manual_hour = manual_hours[slot]
        model_hour = result.hourly_coverage[slot]
        manual_open = int(_excel_number(manual_hour.get("open_sectors")) or 0) if isinstance(manual_hour, dict) else 0
        model_open = model_hour.open_sectors
        manual_map = _manual_sector_map(manual_hour)
        model_map = _model_sector_map(model_hour)
        manual_sector_names = [name for name in sector_names if name in manual_map]
        model_sector_names = [name for name in sector_names if name in model_map]
        manual_workers = manual_hour.get("workers") if isinstance(manual_hour, dict) else []
        sectors = []
        for sector_name in sector_names:
            manual_label = manual_map.get(sector_name)
            model_label = model_map.get(sector_name)
            sectors.append(
                {
                    "sector_name": sector_name,
                    "manual": manual_label,
                    "model": model_label,
                    "status": _sector_cell_status(manual_label, model_label),
                }
            )
        rows.append(
            {
                "hour": _hour_label_for_slot(slot),
                "manual": manual_open,
                "model": model_open,
                "diff": model_open - manual_open,
                "manual_sectors": manual_sector_names,
                "model_sectors": model_sector_names,
                "matching_sectors": [name for name in sector_names if name in manual_map and name in model_map],
                "missing_sectors": [name for name in sector_names if name in manual_map and name not in model_map],
                "extra_sectors": [name for name in sector_names if name in model_map and name not in manual_map],
                "manual_workers": len(manual_workers) if isinstance(manual_workers, list) else 0,
                "model_workers": len(model_hour.workers),
                "worker_diff": len(model_hour.workers) - (len(manual_workers) if isinstance(manual_workers, list) else 0),
                "sectors": sectors,
            }
        )
    return rows


def manual_configuration_audit(
    names: list[str] | None = None,
    *,
    time_limit_seconds: int = 3,
) -> dict[str, object]:
    path = selected_configuration_library_path()
    workbook_path = selected_configuration_workbook_path()
    selected_names = names or FOCUS_CONFIGURATION_NAMES
    started_at = time.perf_counter()
    rows_out: list[dict[str, object]] = []

    if path is None:
        return {
            "source_path": None,
            "workbook_path": str(workbook_path) if workbook_path else None,
            "time_limit_seconds": time_limit_seconds,
            "focus_names": selected_names,
            "elapsed_seconds": 0,
            "rows": [
                {
                    "name": name,
                    "status": "missing_library",
                    "exists": False,
                    "message": "Baza ročnih konfiguracij ni najdena.",
                }
                for name in selected_names
            ],
        }

    rows = read_configuration_csv(path)
    columns_by_name = {name: column_index for column_index, name in configuration_columns(rows)}
    model_max_by_column = configuration_metric_by_column(rows, "MODEL_MAX_SH")
    status_by_column = configuration_metric_by_column(rows, "MODEL_STATUS")
    seconds_by_column = configuration_metric_by_column(rows, "MODEL_SECONDS")
    supported_shifts = {shift.code for shift in DEFAULT_SHIFTS}

    for name in selected_names:
        row_started_at = time.perf_counter()
        column_index = columns_by_name.get(name)
        if column_index is None:
            rows_out.append(
                {
                    "name": name,
                    "status": "missing",
                    "exists": False,
                    "message": "Konfiguracija ni najdena v CSV bazi.",
                    "elapsed_seconds": round(time.perf_counter() - row_started_at, 3),
                }
            )
            continue

        configuration = parse_configuration(rows, column_index, name, supported_shifts)
        manual_schedule = manual_schedule_for_configuration(name)
        manual_counts = _manual_requested_sector_counts(manual_schedule)
        manual_sector_hours = sum(manual_counts) if manual_counts else None
        csv_model_sector_hours = model_max_by_column.get(column_index)
        csv_model_numeric = _excel_number(csv_model_sector_hours)
        base_row: dict[str, object] = {
            "id": str(column_index),
            "name": name,
            "status": str(status_by_column.get(column_index) or ("unsupported" if configuration.unsupported_rows else "ok")),
            "exists": True,
            "parsed_total": configuration.parsed_total,
            "total_without_waiting": configuration.total_without_waiting,
            "waiting_count": configuration.waiting_count,
            "license_counts": configuration.license_counts,
            "unsupported_rows": configuration.unsupported_rows,
            "has_manual_schedule": manual_schedule is not None,
            "csv_model_sector_hours": csv_model_sector_hours,
            "csv_model_seconds": seconds_by_column.get(column_index),
            "manual_sector_hours": manual_sector_hours,
            "manual_scheduled_person_hours": manual_schedule.get("scheduled_person_hours") if manual_schedule else None,
            "csv_vs_manual_diff": (
                int(csv_model_numeric) - int(manual_sector_hours)
                if csv_model_numeric is not None and manual_sector_hours is not None
                else None
            ),
            "model_sector_hours": None,
            "model_missing_sector_hours": None,
            "model_vs_manual_diff": None,
            "model_coverage_percent": None,
            "model_planned_people": None,
            "model_active_people": None,
            "model_utilization_percent": None,
            "solver_status": None,
            "solver_upper_bound_sector_hours": None,
            "solver_gap_to_upper_bound": None,
            "manual_similarity_percent": None,
            "manual_similarity_sh_diff": None,
            "manual_similarity_people_diff": None,
            "manual_similarity_license_diff": None,
            "manual_similarity_role_hours_diff": None,
            "manual_similarity_sector_profile_diff": None,
            "manual_similarity_workload_diff": None,
            "hourly_comparison": [],
            "message": None,
        }

        if configuration.unsupported_rows:
            base_row["status"] = "unsupported"
            base_row["message"] = "Konfiguracija vsebuje nepodprte vrstice."
            base_row["elapsed_seconds"] = round(time.perf_counter() - row_started_at, 3)
            rows_out.append(base_row)
            continue
        if manual_counts is None:
            base_row["status"] = "missing_schedule"
            base_row["message"] = "Excel ročni urnik ni najden ali nima 24 ur."
            base_row["elapsed_seconds"] = round(time.perf_counter() - row_started_at, 3)
            rows_out.append(base_row)
            continue

        try:
            settings = settings_for_manual_schedule_evaluation(manual_schedule, time_limit_seconds)
            result = calculate(request_for_configuration(configuration, settings, manual_counts))
            model_vs_manual_diff = result.max_sector_hours - manual_sector_hours
            similarity = _candidate_similarity_payload(result, manual_configuration_detail(str(column_index)))
            base_row.update(
                {
                    "status": "covered" if result.missing_sector_hours == 0 else "shortfall",
                    "model_sector_hours": result.max_sector_hours,
                    "model_missing_sector_hours": result.missing_sector_hours,
                    "model_vs_manual_diff": model_vs_manual_diff,
                    "model_coverage_percent": (
                        round((result.max_sector_hours / manual_sector_hours) * 100)
                        if manual_sector_hours
                        else 100
                    ),
                    "model_planned_people": result.planned_people,
                    "model_active_people": result.active_people,
                    "model_utilization_percent": result.utilization_percent,
                    "solver_status": result.solver_status,
                    "solver_upper_bound_sector_hours": result.solver_upper_bound_sector_hours,
                    "solver_gap_to_upper_bound": result.solver_gap_to_upper_bound,
                    "manual_similarity_percent": similarity["similarity"],
                    "manual_similarity_sh_diff": similarity["sh_diff"],
                    "manual_similarity_people_diff": similarity["people_diff"],
                    "manual_similarity_license_diff": similarity["license_diff"],
                    "manual_similarity_role_hours_diff": similarity["role_hours_diff"],
                    "manual_similarity_sector_profile_diff": similarity["sector_profile_diff"],
                    "manual_similarity_workload_diff": similarity["workload_diff"],
                    "hourly_comparison": _hourly_comparison(manual_schedule, result, settings.max_sectors_per_hour),
                    "message": None if result.missing_sector_hours == 0 else f"Manjka {result.missing_sector_hours} SH.",
                }
            )
        except Exception as exc:  # pragma: no cover - surfaced to the UI as an audit row.
            base_row["status"] = "error"
            base_row["message"] = str(exc)

        base_row["elapsed_seconds"] = round(time.perf_counter() - row_started_at, 3)
        rows_out.append(base_row)

    return {
        "source_path": str(path),
        "workbook_path": str(workbook_path) if workbook_path else None,
        "time_limit_seconds": time_limit_seconds,
        "focus_names": selected_names,
        "elapsed_seconds": round(time.perf_counter() - started_at, 3),
        "rows": rows_out,
    }

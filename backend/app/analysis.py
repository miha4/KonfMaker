from __future__ import annotations

import base64
import csv
import math
import os
import re
import statistics
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from ortools.sat.python import cp_model
from pydantic import BaseModel, Field, field_validator


WEEKDAYS = ["PO", "TO", "SR", "ČE", "PE", "SO", "NE"]
WEEKDAY_DUMMIES = ["TO", "SR", "ČE", "PE", "SO", "NE"]
EXCEL_EPOCH = date(1899, 12, 30)
CELL_REF_RE = re.compile(r"([A-Z]+)(\d+)")
FIRST_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
NS_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
COEFFICIENT_SCALE = 10_000
CP_SAT_WORKERS = 8
CONFIG_LIBRARY_ENV = "KONFMAKER_CONFIG_LIBRARY_CSV"
OPERATIONAL_BLOCK_SH_TOLERANCE = 4
DEFAULT_CONFIG_LIBRARY_PATHS = [
    Path("/Users/mihamedvesek/Documents/Codex/konfiguracije_okzp_obogateno_vlimiti.csv"),
    Path("/Users/mihamedvesek/Documents/Codex/konfiguracije_okzp_obogateno.csv"),
]


class AnalysisMapping(BaseModel):
    sector_sheet: str = "23-26"
    adjusted_sector_sheet: str = "23-26"
    traffic_sheet: str = "PRELETI"
    forecast_traffic_sheet: str = ""
    first_col: int = Field(default=4, ge=1)
    last_col: int = Field(default=0, ge=0)
    year_rows: dict[str, int] = Field(
        default_factory=lambda: {"2026": 1, "2025": 24},
    )
    traffic_header_row: int = Field(default=1, ge=1)
    traffic_first_row: int = Field(default=2, ge=1)
    traffic_date_col: int = Field(default=1, ge=1)
    traffic_weekday_col: int = Field(default=3, ge=1)
    traffic_flights_col: int = Field(default=4, ge=1)


class AnalysisParams(BaseModel):
    fit_years: list[int] = Field(default_factory=lambda: [2025])
    test_year: int = 2026
    night_add: int = Field(default=5, ge=0, le=24)
    min_daily_sector_hours: float = Field(default=24, ge=0, le=120)
    max_sectors: int = Field(default=5, ge=1, le=8)
    year_weights: dict[str, float] = Field(default_factory=lambda: {"2025": 1.2})
    thresholds: dict[str, float] = Field(
        default_factory=lambda: {"1": 0.5, "2": 1.4, "3": 2.55, "4": 3.45, "5": 4.5},
    )
    intercept_override: float | None = None
    coefficient_override: float | None = None
    weekday_adjustment_overrides: dict[str, float] = Field(default_factory=dict)
    weekday_buffers: dict[str, float] = Field(default_factory=dict)
    optimize_with_cp_sat: bool = True
    optimize_thresholds: bool = True
    threshold_search_step: float = Field(default=0.05, gt=0, le=0.5)
    threshold_search_radius: float = Field(default=0.4, ge=0, le=2)
    lock_manual_coefficients: bool = False
    lock_intercept: bool = False
    lock_coefficient: bool = False
    lock_weekday_adjustments: bool = False
    lock_thresholds: bool = False
    cp_sat_time_limit_seconds: int = Field(default=8, ge=1, le=120)
    under_prediction_weight: int = Field(default=1, ge=1, le=100)
    over_prediction_weight: int = Field(default=1, ge=1, le=100)
    traffic_forecast_mode: str = "previous_year_growth"
    traffic_source_year: int | None = None
    use_actual_target_traffic: bool = True
    annual_traffic_growth_rates: dict[str, float] = Field(default_factory=dict)
    default_traffic_growth: float = Field(default=0.0, ge=-0.5, le=2)
    planning_safety_margin: float = Field(default=0.0, ge=0, le=1)
    analog_backtest_enabled: bool = True
    forecast_start_date: str | None = None
    forecast_end_date: str | None = None
    planning_start_date: str | None = None
    planning_end_date: str | None = None
    fatigue_enabled: bool = False
    fatigue_lambda: float = Field(default=0.0, ge=0, le=1)
    fatigue_apply_max: bool = True
    reference_year: int = 2025
    target_weekday_staff: float = Field(default=27, ge=0, le=100)
    target_weekend_staff: float = Field(default=28, ge=0, le=100)
    reference_weekday_staff: float = Field(default=27, ge=0, le=100)
    reference_weekend_staff: float = Field(default=28, ge=0, le=100)
    allowed_density_increase: float = Field(default=0.0, ge=-0.5, le=1)
    season_start_month: int = Field(default=6, ge=1, le=12)
    season_end_month: int = Field(default=9, ge=1, le=12)
    special_days: list[str] = Field(default_factory=list)
    special_day_buffer: float = Field(default=0.0, ge=-24, le=48)
    special_day_exclude_from_fit: bool = True

    @field_validator("fit_years")
    @classmethod
    def fit_years_must_exist(cls, value: list[int]) -> list[int]:
        cleaned = sorted(set(value))
        if not cleaned:
            raise ValueError("Izberi vsaj eno leto za fit.")
        return cleaned


class WorkbookPayload(BaseModel):
    file_name: str
    file_base64: str
    mapping: AnalysisMapping | None = None
    params: AnalysisParams | None = None


@dataclass
class SheetData:
    name: str
    cells: dict[tuple[int, int], Any]
    max_row: int
    max_col: int

    def value(self, row: int, col: int) -> Any:
        return self.cells.get((row, col))


@dataclass
class SectorRecord:
    block_year: int
    day_date: date
    slot_index: int
    weekday: str
    iso_week: int
    iso_weekday: int
    flights: float | None
    hourly: list[float]
    actual_total: float
    has_actual: bool = True


@dataclass
class TrafficRecord:
    day_date: date
    weekday: str
    flights: float


@dataclass
class ForecastTrafficRecord:
    slot_index: int
    day_date: date | None
    weekday: str
    flights: float
    source: str = "unknown"


def decode_workbook(payload: WorkbookPayload) -> bytes:
    try:
        raw = payload.file_base64.split(",", 1)[-1]
        return base64.b64decode(raw)
    except ValueError as exc:
        raise ValueError("Datoteke ni mogoče prebrati kot base64.") from exc


def col_name_to_index(name: str) -> int:
    index = 0
    for char in name:
        index = index * 26 + ord(char) - 64
    return index


def cell_ref_to_indexes(ref: str) -> tuple[int, int]:
    match = CELL_REF_RE.match(ref)
    if not match:
        return 0, 0
    return int(match.group(2)), col_name_to_index(match.group(1))


def excel_serial_to_date(value: float) -> date:
    return EXCEL_EPOCH + timedelta(days=int(value))


def normalize_weekday(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper().replace("CE", "ČE")
    if text in WEEKDAYS:
        return text
    return None


def weekday_from_date(day_date: date) -> str:
    return WEEKDAYS[day_date.weekday()]


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        if math.isfinite(float(value)):
            return float(value)
        return None
    text = str(value).strip().replace(",", ".")
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and 20_000 <= float(value) <= 80_000:
        return excel_serial_to_date(float(value))
    if isinstance(value, str):
        text = value.strip()
        for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                pass
    return None


def first_number(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(round(float(value)))
    match = FIRST_NUMBER_RE.search(str(value))
    if not match:
        return None
    return int(round(float(match.group(0).replace(",", "."))))


def sector_value(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if math.isfinite(float(value)) else None
    parsed = first_number(value)
    return float(parsed) if parsed is not None else None


def rounded(value: float | None, digits: int = 3) -> float | None:
    if value is None:
        return None
    return round(value, digits)


class XlsxReader:
    def __init__(self, data: bytes):
        self.archive = zipfile.ZipFile(BytesIO(data))
        self.shared_strings = self._read_shared_strings()
        self.sheet_paths = self._read_sheet_paths()
        self._sheet_cache: dict[str, SheetData] = {}

    def sheet_names(self) -> list[str]:
        return list(self.sheet_paths)

    def sheet(self, name: str) -> SheetData:
        if name not in self.sheet_paths:
            raise ValueError(f"List '{name}' ne obstaja v Excelu.")
        if name not in self._sheet_cache:
            self._sheet_cache[name] = self._read_sheet(name, self.sheet_paths[name])
        return self._sheet_cache[name]

    def _read_shared_strings(self) -> list[str]:
        if "xl/sharedStrings.xml" not in self.archive.namelist():
            return []
        root = ElementTree.fromstring(self.archive.read("xl/sharedStrings.xml"))
        strings: list[str] = []
        for item in root.findall(f"{NS_MAIN}si"):
            parts = [node.text or "" for node in item.iter(f"{NS_MAIN}t")]
            strings.append("".join(parts))
        return strings

    def _read_sheet_paths(self) -> dict[str, str]:
        workbook_root = ElementTree.fromstring(self.archive.read("xl/workbook.xml"))
        rels_root = ElementTree.fromstring(self.archive.read("xl/_rels/workbook.xml.rels"))
        rels: dict[str, str] = {}
        for rel in rels_root.findall(f"{NS_PKG_REL}Relationship"):
            rel_id = rel.attrib.get("Id")
            target = rel.attrib.get("Target", "")
            if rel_id and target:
                rels[rel_id] = f"xl/{target.lstrip('/')}" if not target.startswith("xl/") else target

        sheets: dict[str, str] = {}
        for sheet in workbook_root.findall(f".//{NS_MAIN}sheet"):
            name = sheet.attrib.get("name")
            rel_id = sheet.attrib.get(f"{NS_REL}id")
            if name and rel_id and rel_id in rels:
                sheets[name] = rels[rel_id]
        return sheets

    def _read_sheet(self, name: str, path: str) -> SheetData:
        root = ElementTree.fromstring(self.archive.read(path))
        cells: dict[tuple[int, int], Any] = {}
        max_row = 0
        max_col = 0
        for cell in root.iter(f"{NS_MAIN}c"):
            ref = cell.attrib.get("r", "")
            row, col = cell_ref_to_indexes(ref)
            if row <= 0 or col <= 0:
                continue
            value = self._cell_value(cell)
            if value is None:
                continue
            cells[(row, col)] = value
            max_row = max(max_row, row)
            max_col = max(max_col, col)
        return SheetData(name=name, cells=cells, max_row=max_row, max_col=max_col)

    def _cell_value(self, cell: ElementTree.Element) -> Any:
        cell_type = cell.attrib.get("t")
        if cell_type == "inlineStr":
            parts = [node.text or "" for node in cell.iter(f"{NS_MAIN}t")]
            return "".join(parts)

        value_node = cell.find(f"{NS_MAIN}v")
        if value_node is None or value_node.text is None:
            return None
        raw = value_node.text

        if cell_type == "s":
            index = int(raw)
            return self.shared_strings[index] if 0 <= index < len(self.shared_strings) else ""
        if cell_type in {"str", "e"}:
            return raw
        if cell_type == "b":
            return raw == "1"
        try:
            number = float(raw)
        except ValueError:
            return raw
        return int(number) if number.is_integer() else number


def detect_year_rows(sheet: SheetData) -> dict[str, int]:
    detected: dict[str, int] = {}
    for row in range(1, sheet.max_row + 1):
        value = sheet.value(row, 2)
        number = as_float(value)
        if number is None:
            continue
        year = int(number)
        if 2020 <= year <= 2035:
            detected[str(year)] = row
    return detected


def suggested_year_rows(mapping: AnalysisMapping, detected_year_rows: dict[str, int]) -> dict[str, int]:
    return {
        year: detected_year_rows.get(year, start_row)
        for year, start_row in mapping.year_rows.items()
    }


def fill_missing_year_rows(mapping: AnalysisMapping, detected_year_rows: dict[str, int]) -> dict[str, int]:
    return {
        year: detected_year_rows.get(year, start_row) if start_row <= 0 else start_row
        for year, start_row in mapping.year_rows.items()
    }


def parse_traffic(sheet: SheetData, mapping: AnalysisMapping) -> dict[date, TrafficRecord]:
    records: dict[date, TrafficRecord] = {}
    for row in range(mapping.traffic_first_row, sheet.max_row + 1):
        day_date = as_date(sheet.value(row, mapping.traffic_date_col))
        flights = as_float(sheet.value(row, mapping.traffic_flights_col))
        if day_date is None or flights is None:
            continue
        weekday = normalize_weekday(sheet.value(row, mapping.traffic_weekday_col)) or weekday_from_date(day_date)
        records[day_date] = TrafficRecord(day_date=day_date, weekday=weekday, flights=flights)
    return records


def parse_forecast_traffic(sheet: SheetData) -> dict[int, ForecastTrafficRecord]:
    records: dict[int, ForecastTrafficRecord] = {}
    for row in range(2, sheet.max_row + 1):
        slot_index = row - 2
        day_date = as_date(sheet.value(row, 1))
        weekday = normalize_weekday(sheet.value(row, 2)) or (weekday_from_date(day_date) if day_date else "PO")
        flights = as_float(sheet.value(row, 3))
        if flights is None:
            continue
        records[slot_index] = ForecastTrafficRecord(
            slot_index=slot_index,
            day_date=day_date,
            weekday=weekday,
            flights=flights,
            source=str(sheet.value(row, 4) or "excel_sheet"),
        )
    return records


def apply_forecast_traffic(records: list[SectorRecord], forecast_traffic: dict[int, ForecastTrafficRecord]) -> list[SectorRecord]:
    updated: list[SectorRecord] = []
    for record in records:
        forecast = forecast_traffic.get(record.slot_index)
        if forecast is None:
            updated.append(record)
            continue
        updated.append(
            SectorRecord(
                block_year=record.block_year,
                day_date=forecast.day_date or record.day_date,
                slot_index=record.slot_index,
                weekday=forecast.weekday,
                iso_week=record.iso_week,
                iso_weekday=record.iso_weekday,
                flights=forecast.flights,
                hourly=record.hourly,
                actual_total=record.actual_total,
                has_actual=record.has_actual,
            ),
        )
    return updated


def apply_forecast_calendar(records: list[SectorRecord], forecast_traffic: dict[int, ForecastTrafficRecord]) -> list[SectorRecord]:
    updated: list[SectorRecord] = []
    for record in records:
        forecast = forecast_traffic.get(record.slot_index)
        if forecast is None:
            updated.append(record)
            continue
        updated.append(
            SectorRecord(
                block_year=record.block_year,
                day_date=forecast.day_date or record.day_date,
                slot_index=record.slot_index,
                weekday=forecast.weekday,
                iso_week=record.iso_week,
                iso_weekday=record.iso_weekday,
                flights=record.flights,
                hourly=record.hourly,
                actual_total=record.actual_total,
                has_actual=record.has_actual,
            ),
        )
    return updated


def cumulative_growth_factor(source_year: int, target_year: int, params: AnalysisParams) -> float:
    if source_year >= target_year:
        return 1.0
    factor = 1.0
    for year in range(source_year, target_year):
        growth = params.annual_traffic_growth_rates.get(str(year), params.default_traffic_growth)
        factor *= 1 + float(growth)
    return factor


def source_record_for_target(
    records_by_year_slot: dict[tuple[int, int], SectorRecord],
    records_by_year_iso: dict[tuple[int, int, int], SectorRecord],
    target: SectorRecord,
    source_year: int,
) -> SectorRecord | None:
    by_slot = records_by_year_slot.get((source_year, target.slot_index))
    if by_slot and by_slot.flights is not None:
        return by_slot
    by_iso = records_by_year_iso.get((source_year, target.iso_week, target.iso_weekday))
    if by_iso and by_iso.flights is not None:
        return by_iso
    return None


def template_record_for_date(all_records: list[SectorRecord], day_date: date, params: AnalysisParams) -> SectorRecord | None:
    iso = day_date.isocalendar()
    preferred_years = [
        year
        for year in [
            params.traffic_source_year,
            params.test_year - 1,
            *sorted(params.fit_years, reverse=True),
        ]
        if year is not None
    ]
    seen_years: set[int] = set()
    for year in preferred_years:
        if year in seen_years:
            continue
        seen_years.add(year)
        for record in all_records:
            if (
                record.block_year == year
                and record.iso_week == int(iso.week)
                and record.iso_weekday == int(iso.weekday)
            ):
                return record
    for record in all_records:
        if record.iso_week == int(iso.week) and record.iso_weekday == int(iso.weekday):
            return record
    return None


def empty_target_record(day_date: date, all_records: list[SectorRecord], traffic: dict[date, TrafficRecord], params: AnalysisParams) -> SectorRecord:
    iso = day_date.isocalendar()
    template = template_record_for_date(all_records, day_date, params)
    traffic_record = traffic.get(day_date)
    return SectorRecord(
        block_year=day_date.year,
        day_date=day_date,
        slot_index=template.slot_index if template else day_date.timetuple().tm_yday - 1,
        weekday=traffic_record.weekday if traffic_record else weekday_from_date(day_date),
        iso_week=int(iso.week),
        iso_weekday=int(iso.weekday),
        flights=traffic_record.flights if traffic_record else None,
        hourly=template.hourly[:19] if template else [1.0 for _ in range(19)],
        actual_total=float(template.actual_total) if template else 0.0,
        has_actual=False,
    )


def select_target_records(
    sector_records: list[SectorRecord],
    traffic: dict[date, TrafficRecord],
    params: AnalysisParams,
) -> list[SectorRecord]:
    start = parse_param_date(params.forecast_start_date)
    end = parse_param_date(params.forecast_end_date)
    records_by_date = {record.day_date: record for record in sector_records if record.block_year == params.test_year}

    if start is not None and end is not None:
        return [
            records_by_date.get(day_date) or empty_target_record(day_date, sector_records, traffic, params)
            for day_date in date_range_days(start, end)
        ]

    records = [
        record for record in sector_records
        if record.block_year == params.test_year and record.flights is not None
    ]
    if not records:
        records = [
            record for record in sector_records
            if record.flights is not None and record.block_year not in params.fit_years
        ]
    if start is not None or end is not None:
        records = [
            record for record in records
            if date_in_absolute_window(record.day_date, start, end)
        ]
    return records


def forecast_traffic_by_date(forecast_traffic: dict[int, ForecastTrafficRecord]) -> dict[date, ForecastTrafficRecord]:
    return {
        forecast.day_date: forecast
        for forecast in forecast_traffic.values()
        if forecast.day_date is not None
    }


def apply_forecast_traffic_by_date(
    records: list[SectorRecord],
    forecast_traffic: dict[int, ForecastTrafficRecord],
) -> list[SectorRecord]:
    by_date = forecast_traffic_by_date(forecast_traffic)
    updated: list[SectorRecord] = []
    for record in records:
        forecast = by_date.get(record.day_date)
        if forecast is None:
            updated.append(record)
            continue
        updated.append(
            SectorRecord(
                block_year=record.block_year,
                day_date=record.day_date,
                slot_index=record.slot_index,
                weekday=forecast.weekday,
                iso_week=record.iso_week,
                iso_weekday=record.iso_weekday,
                flights=forecast.flights,
                hourly=record.hourly,
                actual_total=record.actual_total,
                has_actual=record.has_actual,
            ),
        )
    return updated


def build_generated_forecast_traffic(
    all_records: list[SectorRecord],
    target_records: list[SectorRecord],
    params: AnalysisParams,
) -> tuple[dict[int, ForecastTrafficRecord], dict[str, Any]]:
    mode = params.traffic_forecast_mode
    if mode not in {"previous_year_growth", "weighted_history"}:
        return {}, {
            "mode": mode,
            "generated_days": 0,
            "actual_days_kept": 0,
            "missing_days": 0,
            "sample": [],
        }

    records_by_year_slot: dict[tuple[int, int], SectorRecord] = {}
    records_by_year_iso: dict[tuple[int, int, int], SectorRecord] = {}
    for record in all_records:
        if record.flights is None:
            continue
        records_by_year_slot[(record.block_year, record.slot_index)] = record
        records_by_year_iso[(record.block_year, record.iso_week, record.iso_weekday)] = record

    generated: dict[int, ForecastTrafficRecord] = {}
    actual_days_kept = 0
    missing_days = 0
    sample: list[dict[str, Any]] = []
    source_year = params.traffic_source_year or params.test_year - 1

    for target in target_records:
        if params.use_actual_target_traffic and target.flights is not None:
            actual_days_kept += 1
            continue

        forecast_flights: float | None = None
        source_label = ""
        if mode == "previous_year_growth":
            source = source_record_for_target(records_by_year_slot, records_by_year_iso, target, source_year)
            if source is not None and source.flights is not None:
                factor = cumulative_growth_factor(source_year, target.block_year, params)
                forecast_flights = float(source.flights) * factor
                source_label = f"{source_year} * {factor:.4f}"
        else:
            weighted_sum = 0.0
            total_weight = 0.0
            used_sources: list[str] = []
            for fit_year in params.fit_years:
                if fit_year == target.block_year:
                    continue
                source = source_record_for_target(records_by_year_slot, records_by_year_iso, target, fit_year)
                if source is None or source.flights is None:
                    continue
                weight = float(params.year_weights.get(str(fit_year), 1.0))
                if weight <= 0:
                    continue
                factor = cumulative_growth_factor(fit_year, target.block_year, params)
                weighted_sum += float(source.flights) * factor * weight
                total_weight += weight
                used_sources.append(f"{fit_year}*{factor:.3f}w{weight:g}")
            if total_weight > 0:
                forecast_flights = weighted_sum / total_weight
                source_label = ", ".join(used_sources)

        if forecast_flights is None:
            missing_days += 1
            continue

        record = ForecastTrafficRecord(
            slot_index=target.slot_index,
            day_date=target.day_date,
            weekday=target.weekday,
            flights=forecast_flights,
            source=source_label,
        )
        generated[target.slot_index] = record
        if len(sample) < 10:
            sample.append({
                "date": target.day_date.isoformat(),
                "weekday": target.weekday,
                "flights": rounded(forecast_flights, 1),
                "source": source_label,
            })

    return generated, {
        "mode": mode,
        "generated_days": len(generated),
        "actual_days_kept": actual_days_kept,
        "missing_days": missing_days,
        "source_year": source_year,
        "annual_growth_rates": {
            key: rounded(value, 4)
            for key, value in params.annual_traffic_growth_rates.items()
        },
        "default_growth": params.default_traffic_growth,
        "sample": sample,
    }


def parse_sector_records(
    sheet: SheetData,
    traffic: dict[date, TrafficRecord],
    mapping: AnalysisMapping,
    night_add: int,
) -> list[SectorRecord]:
    records: list[SectorRecord] = []
    last_col = mapping.last_col if mapping.last_col > 0 else sheet.max_col
    for year_text, start_row in sorted(mapping.year_rows.items(), key=lambda item: item[1]):
        if start_row <= 0:
            continue
        try:
            block_year = int(year_text)
        except ValueError:
            continue

        date_row = start_row
        weekday_row = start_row + 1
        hour_start_row = start_row + 3
        total_row = start_row + 22

        for col in range(mapping.first_col, last_col + 1):
            day_date = as_date(sheet.value(date_row, col))
            if day_date is None:
                continue
            hourly = [
                count
                for count in (sector_value(sheet.value(hour_start_row + hour_index, col)) for hour_index in range(19))
                if count is not None
            ]
            if not hourly:
                continue
            actual_total = as_float(sheet.value(total_row, col))
            if actual_total is None:
                actual_total = float(sum(hourly) + night_add)
            weekday = normalize_weekday(sheet.value(weekday_row, col)) or weekday_from_date(day_date)
            iso = day_date.isocalendar()
            traffic_record = traffic.get(day_date)
            records.append(
                SectorRecord(
                    block_year=block_year,
                    day_date=day_date,
                    slot_index=col - mapping.first_col,
                    weekday=weekday,
                    iso_week=int(iso.week),
                    iso_weekday=int(iso.weekday),
                    flights=traffic_record.flights if traffic_record else None,
                    hourly=hourly[:19],
                    actual_total=actual_total,
                ),
            )
    return records


def solve_linear_system(matrix: list[list[float]], vector: list[float]) -> list[float]:
    n = len(vector)
    augmented = [row[:] + [vector[index]] for index, row in enumerate(matrix)]
    for pivot_index in range(n):
        best = max(range(pivot_index, n), key=lambda row: abs(augmented[row][pivot_index]))
        if abs(augmented[best][pivot_index]) < 1e-10:
            augmented[pivot_index][pivot_index] += 1e-8
            best = pivot_index
        augmented[pivot_index], augmented[best] = augmented[best], augmented[pivot_index]
        pivot = augmented[pivot_index][pivot_index]
        if abs(pivot) < 1e-12:
            continue
        for col in range(pivot_index, n + 1):
            augmented[pivot_index][col] /= pivot
        for row in range(n):
            if row == pivot_index:
                continue
            factor = augmented[row][pivot_index]
            if factor == 0:
                continue
            for col in range(pivot_index, n + 1):
                augmented[row][col] -= factor * augmented[pivot_index][col]
    return [augmented[row][n] for row in range(n)]


def fit_daily_regression(records: list[SectorRecord]) -> dict[str, Any]:
    rows = [
        record for record in records
        if record.flights is not None and record.actual_total is not None and record.weekday in WEEKDAYS
    ]
    if len(rows) < 12:
        raise ValueError("Premalo vrstic za fit. Preveri mapiranje sektorjev in preletov.")

    feature_count = 2 + len(WEEKDAY_DUMMIES)
    xtx = [[0.0 for _ in range(feature_count)] for _ in range(feature_count)]
    xty = [0.0 for _ in range(feature_count)]
    for record in rows:
        features = [1.0, float(record.flights or 0.0)]
        features.extend(1.0 if record.weekday == weekday else 0.0 for weekday in WEEKDAY_DUMMIES)
        y = float(record.actual_total)
        for i, left in enumerate(features):
            xty[i] += left * y
            for j, right in enumerate(features):
                xtx[i][j] += left * right

    coefficients = solve_linear_system(xtx, xty)
    weekday_adjustments = {"PO": 0.0}
    weekday_adjustments.update({
        weekday: coefficients[index + 2]
        for index, weekday in enumerate(WEEKDAY_DUMMIES)
    })
    return {
        "intercept": coefficients[0],
        "coefficient_per_flight": coefficients[1],
        "weekday_adjustments": weekday_adjustments,
        "sample_size": len(rows),
        "method": "least_squares",
    }


def coefficient_to_units(value: float) -> int:
    return int(round(value * COEFFICIENT_SCALE))


def coefficient_from_units(value: int) -> float:
    return value / COEFFICIENT_SCALE


def optimize_daily_coefficients_cp_sat(
    records: list[SectorRecord],
    params: AnalysisParams,
    initial_fit: dict[str, Any],
) -> dict[str, Any]:
    rows = [
        record for record in records
        if record.flights is not None and record.actual_total is not None and record.weekday in WEEKDAYS
    ]
    if len(rows) < 12:
        return initial_fit

    model = cp_model.CpModel()
    intercept = model.NewIntVar(0, 80 * COEFFICIENT_SCALE, "intercept")
    coefficient = model.NewIntVar(0, int(0.12 * COEFFICIENT_SCALE), "coefficient_per_flight")
    weekday_vars: dict[str, cp_model.IntVar | int] = {"PO": 0}

    if (params.lock_manual_coefficients or params.lock_intercept) and params.intercept_override is not None:
        model.Add(intercept == coefficient_to_units(params.intercept_override))
    else:
        model.AddHint(intercept, coefficient_to_units(float(initial_fit["intercept"])))

    if (params.lock_manual_coefficients or params.lock_coefficient) and params.coefficient_override is not None:
        model.Add(coefficient == coefficient_to_units(params.coefficient_override))
    else:
        model.AddHint(coefficient, coefficient_to_units(float(initial_fit["coefficient_per_flight"])))

    for weekday in WEEKDAY_DUMMIES:
        weekday_var = model.NewIntVar(-30 * COEFFICIENT_SCALE, 30 * COEFFICIENT_SCALE, f"weekday_{weekday}")
        if (params.lock_manual_coefficients or params.lock_weekday_adjustments) and weekday in params.weekday_adjustment_overrides:
            model.Add(weekday_var == coefficient_to_units(params.weekday_adjustment_overrides[weekday]))
        else:
            model.AddHint(
                weekday_var,
                coefficient_to_units(float(initial_fit["weekday_adjustments"].get(weekday, 0.0))),
            )
        weekday_vars[weekday] = weekday_var

    objective_terms: list[cp_model.LinearExpr] = []
    max_error = 120 * COEFFICIENT_SCALE
    min_target = coefficient_to_units(params.min_daily_sector_hours)
    min_target_var = model.NewConstant(min_target)
    for index, record in enumerate(rows):
        flights = int(round(float(record.flights or 0.0)))
        actual = coefficient_to_units(float(record.actual_total))
        linear_target = model.NewIntVar(-max_error, max_error * 2, f"linear_target_{index}")
        target = model.NewIntVar(0, max_error * 2, f"target_{index}")
        day_adjustment = weekday_vars.get(record.weekday, 0)
        model.Add(linear_target == intercept + coefficient * flights + day_adjustment)
        model.AddMaxEquality(target, [linear_target, min_target_var])

        diff = model.NewIntVar(-max_error, max_error, f"diff_{index}")
        over = model.NewIntVar(0, max_error, f"over_{index}")
        under = model.NewIntVar(0, max_error, f"under_{index}")
        model.Add(diff == target - actual)
        model.Add(over >= diff)
        model.Add(under >= -diff)
        objective_terms.append(params.over_prediction_weight * over)
        objective_terms.append(params.under_prediction_weight * under)

    model.Minimize(sum(objective_terms))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = params.cp_sat_time_limit_seconds
    solver.parameters.num_search_workers = CP_SAT_WORKERS
    status = solver.Solve(model)
    if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
        return {
            **initial_fit,
            "method": "least_squares_fallback",
            "solver_status": solver.StatusName(status),
            "objective_value": None,
            "best_objective_bound": None,
        }

    weekday_adjustments = {"PO": 0.0}
    weekday_adjustments.update({
        weekday: coefficient_from_units(int(solver.Value(weekday_vars[weekday])))
        for weekday in WEEKDAY_DUMMIES
    })
    return {
        "intercept": coefficient_from_units(int(solver.Value(intercept))),
        "coefficient_per_flight": coefficient_from_units(int(solver.Value(coefficient))),
        "weekday_adjustments": weekday_adjustments,
        "sample_size": len(rows),
        "method": "cp_sat",
        "solver_status": solver.StatusName(status),
        "objective_value": solver.ObjectiveValue(),
        "best_objective_bound": solver.BestObjectiveBound(),
    }


def tuned_coefficients(fit: dict[str, Any], params: AnalysisParams) -> dict[str, Any]:
    weekday_adjustments = {
        weekday: float(fit["weekday_adjustments"].get(weekday, 0.0))
        for weekday in WEEKDAYS
    }
    if params.lock_manual_coefficients or params.lock_weekday_adjustments:
        for weekday, override in params.weekday_adjustment_overrides.items():
            normalized = normalize_weekday(weekday)
            if normalized:
                weekday_adjustments[normalized] = float(override)
    for weekday, buffer in params.weekday_buffers.items():
        normalized = normalize_weekday(weekday)
        if normalized:
            weekday_adjustments[normalized] = weekday_adjustments.get(normalized, 0.0) + float(buffer)

    return {
        "intercept": (
            fit["intercept"]
            if not (params.lock_manual_coefficients or params.lock_intercept) or params.intercept_override is None
            else params.intercept_override
        ),
        "coefficient_per_flight": (
            fit["coefficient_per_flight"]
            if not (params.lock_manual_coefficients or params.lock_coefficient) or params.coefficient_override is None
            else params.coefficient_override
        ),
        "weekday_adjustments": weekday_adjustments,
        "method": fit.get("method", "least_squares"),
        "solver_status": fit.get("solver_status"),
        "objective_value": fit.get("objective_value"),
        "best_objective_bound": fit.get("best_objective_bound"),
    }


def daily_target(record: SectorRecord, coefficients: dict[str, Any], params: AnalysisParams) -> float:
    target = (
        float(coefficients["intercept"])
        + float(coefficients["coefficient_per_flight"]) * float(record.flights or 0.0)
        + float(coefficients["weekday_adjustments"].get(record.weekday, 0.0))
    )
    if is_special_day(record.day_date, params):
        target += params.special_day_buffer
    return max(params.min_daily_sector_hours, target)


def formula_text(record: SectorRecord, coefficients: dict[str, Any], params: AnalysisParams, target: float) -> str:
    intercept = float(coefficients["intercept"])
    coefficient = float(coefficients["coefficient_per_flight"])
    weekday_adjustment = float(coefficients["weekday_adjustments"].get(record.weekday, 0.0))
    special_buffer = params.special_day_buffer if is_special_day(record.day_date, params) else 0.0
    raw = intercept + coefficient * float(record.flights or 0.0) + weekday_adjustment + special_buffer
    buffer_part = f" + {special_buffer:.6f} special" if special_buffer else ""
    return (
        f"max({params.min_daily_sector_hours:.2f}; "
        f"{intercept:.6f} + {coefficient:.9f} * {float(record.flights or 0.0):.0f} "
        f"+ {weekday_adjustment:.6f}{buffer_part}) = {target:.2f}"
        if target != raw
        else (
            f"{intercept:.6f} + {coefficient:.9f} * {float(record.flights or 0.0):.0f} "
            f"+ {weekday_adjustment:.6f}{buffer_part} = {target:.2f}"
        )
    )


def metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "count": 0,
            "mae": None,
            "bias": None,
            "rmse": None,
            "within_3": None,
            "within_5": None,
            "within_10": None,
            "r2": None,
        }
    errors = [float(row["prediction"]) - float(row["actual"]) for row in rows]
    actuals = [float(row["actual"]) for row in rows]
    mae = statistics.mean(abs(error) for error in errors)
    bias = statistics.mean(errors)
    rmse = math.sqrt(statistics.mean(error * error for error in errors))
    mean_actual = statistics.mean(actuals)
    sst = sum((actual - mean_actual) ** 2 for actual in actuals)
    sse = sum(error * error for error in errors)
    return {
        "count": len(rows),
        "mae": rounded(mae),
        "bias": rounded(bias),
        "rmse": rounded(rmse),
        "within_3": rounded(100 * sum(abs(error) <= 3 for error in errors) / len(errors), 1),
        "within_5": rounded(100 * sum(abs(error) <= 5 for error in errors) / len(errors), 1),
        "within_10": rounded(100 * sum(abs(error) <= 10 for error in errors) / len(errors), 1),
        "r2": rounded(1 - sse / sst, 4) if sst > 0 else None,
    }


def build_profiles(records: list[SectorRecord], params: AnalysisParams) -> tuple[dict[tuple[int, int], float], dict[tuple[int, int], float]]:
    sums: dict[tuple[int, int], float] = {}
    weights: dict[tuple[int, int], float] = {}
    fallback_sums: dict[tuple[int, int], float] = {}
    fallback_weights: dict[tuple[int, int], float] = {}
    for record in records:
        weight = float(params.year_weights.get(str(record.block_year), 1.0))
        for hour_index, sector_count in enumerate(record.hourly[:19]):
            key = (record.slot_index, hour_index)
            sums[key] = sums.get(key, 0.0) + sector_count * weight
            weights[key] = weights.get(key, 0.0) + weight
            fallback_key = (record.iso_weekday, hour_index)
            fallback_sums[fallback_key] = fallback_sums.get(fallback_key, 0.0) + sector_count * weight
            fallback_weights[fallback_key] = fallback_weights.get(fallback_key, 0.0) + weight

    profiles = {key: sums[key] / weights[key] for key in sums if weights[key] > 0}
    fallbacks = {
        key: fallback_sums[key] / fallback_weights[key]
        for key in fallback_sums
        if fallback_weights[key] > 0
    }
    return profiles, fallbacks


def scaled_profile_details(
    base_profile: list[float],
    target_total: float,
    night_add: int,
    max_sectors: int,
) -> dict[str, Any]:
    target_daytime = max(0.0, float(target_total) - night_add)
    target_daytime = max(float(len(base_profile)), min(float(len(base_profile) * max_sectors), target_daytime))
    base_sum = sum(base_profile)
    if base_sum <= 0:
        factor = 1.0
        scaled = [1.0 for _ in base_profile]
    else:
        factor = target_daytime / base_sum
        scaled = [max(1.0, min(float(max_sectors), value * factor)) for value in base_profile]
    return {
        "target_daytime": target_daytime,
        "base_sum": base_sum,
        "factor": factor,
        "scaled": scaled,
    }


def sectors_from_scaled_profile(
    scaled: list[float],
    max_sectors: int,
    thresholds: dict[str, float],
) -> list[int]:
    ordered_thresholds = [
        float(thresholds.get(str(index), index - 0.5))
        for index in range(2, max_sectors + 1)
    ]
    return [
        max(1, min(max_sectors, 1 + sum(value >= threshold for threshold in ordered_thresholds)))
        for value in scaled
    ]


def raise_daytime_hours_to_total(
    daytime_hours: list[int],
    priority: list[float],
    target_total: float,
    night_add: int,
    max_sectors: int,
) -> list[int]:
    updated = list(daytime_hours)
    required_total = min(int(max_sectors * len(updated) + night_add), int(math.ceil(target_total)))
    current_total = sum(hourly_for_calculator(updated, night_add, max_sectors))
    if current_total >= required_total:
        return updated

    priority_order = sorted(
        range(len(updated)),
        key=lambda index: (-float(priority[index] if index < len(priority) else 0.0), index),
    )
    while current_total < required_total:
        changed = False
        for hour_index in priority_order:
            if updated[hour_index] >= max_sectors:
                continue
            updated[hour_index] += 1
            current_total += 1
            changed = True
            if current_total >= required_total:
                break
        if not changed:
            break
    return updated


def cap_daytime_hours_to_total(
    daytime_hours: list[int],
    priority: list[float],
    target_total: float,
    night_add: int,
    max_sectors: int,
    minimum_daytime_hours: list[int] | None = None,
) -> list[int]:
    updated = list(daytime_hours)
    floors = [
        max(1, min(max_sectors, int(value)))
        for value in (minimum_daytime_hours or [1] * len(updated))
    ]
    if len(floors) < len(updated):
        floors.extend([1] * (len(updated) - len(floors)))
    floors = floors[:len(updated)]
    floor_total = sum(hourly_for_calculator(floors, night_add, max_sectors))
    allowed_total = max(floor_total, int(math.ceil(target_total)))
    current_total = sum(hourly_for_calculator(updated, night_add, max_sectors))
    if current_total <= allowed_total:
        return updated

    priority_order = sorted(
        range(len(updated)),
        key=lambda index: (
            float(priority[index] if index < len(priority) else 0.0),
            -int(updated[index]),
            index,
        ),
    )
    while current_total > allowed_total:
        changed = False
        for hour_index in priority_order:
            if updated[hour_index] <= floors[hour_index]:
                continue
            updated[hour_index] -= 1
            current_total -= 1
            changed = True
            if current_total <= allowed_total:
                break
        if not changed:
            break
    return updated


def allocate_hourly(
    base_profile: list[float],
    target_total: float,
    night_add: int,
    max_sectors: int,
    thresholds: dict[str, float],
) -> list[int]:
    details = scaled_profile_details(base_profile, target_total, night_add, max_sectors)
    return sectors_from_scaled_profile(details["scaled"], max_sectors, thresholds)


def hourly_profile_for_record(
    record: SectorRecord,
    profiles: dict[tuple[int, int], float],
    fallbacks: dict[tuple[int, int], float],
) -> list[float]:
    profile = []
    for hour_index in range(19):
        key = (record.slot_index, hour_index)
        fallback_key = (record.iso_weekday, hour_index)
        profile.append(profiles.get(key, fallbacks.get(fallback_key, 1.0)))
    return profile


def summarize_weekdays(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary = []
    for weekday in WEEKDAYS:
        subset = [row for row in rows if row["weekday"] == weekday]
        if not subset:
            continue
        actual = statistics.mean(float(row["actual"]) for row in subset)
        prediction = statistics.mean(float(row["prediction"]) for row in subset)
        flights = [row["flights"] for row in subset if row["flights"] is not None]
        summary.append({
            "weekday": weekday,
            "count": len(subset),
            "avg_flights": rounded(statistics.mean(flights), 1) if flights else None,
            "avg_actual": rounded(actual),
            "avg_prediction": rounded(prediction),
            "bias": rounded(prediction - actual),
            "actual_density": rounded(statistics.mean(float(row["flights"]) / float(row["actual"]) for row in subset if row["flights"] and row["actual"]), 2),
        })
    return summary


def summarize_months(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary = []
    for month in range(1, 13):
        subset = [row for row in rows if row["date"].month == month]
        if not subset:
            continue
        month_metrics = metrics(subset)
        summary.append({
            "month": month,
            "count": len(subset),
            "actual_total": rounded(sum(float(row["actual"]) for row in subset), 1),
            "prediction_total": rounded(sum(float(row["prediction"]) for row in subset), 1),
            "mae": month_metrics["mae"],
            "bias": month_metrics["bias"],
        })
    return summary


def summarize_hours(hour_errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary = []
    for hour_index in range(19):
        subset = [row for row in hour_errors if row["hour_index"] == hour_index]
        if not subset:
            continue
        errors = [row["prediction"] - row["actual"] for row in subset]
        exact = sum(error == 0 for error in errors)
        within_one = sum(abs(error) <= 1 for error in errors)
        summary.append({
            "hour": f"{(6 + hour_index) % 24:02d}:00",
            "count": len(subset),
            "exact_percent": rounded(100 * exact / len(subset), 1),
            "within_one_percent": rounded(100 * within_one / len(subset), 1),
            "mae": rounded(statistics.mean(abs(error) for error in errors), 3),
            "bias": rounded(statistics.mean(errors), 3),
        })
    return summary


def hourly_for_calculator(daytime_hours: list[int], night_add: int, max_sectors: int) -> list[int]:
    start_hours = list(range(6, 24)) + [0]
    sectors_by_hour = {
        hour: max(0, min(max_sectors, int(daytime_hours[index])))
        for index, hour in enumerate(start_hours)
        if index < len(daytime_hours)
    }
    night_hours = [1, 2, 3, 4, 5]
    remaining_night = max(0, int(round(night_add)))
    for hour in night_hours:
        sectors_by_hour[hour] = 1 if remaining_night > 0 else 0
        remaining_night -= 1

    return [
        max(0, min(max_sectors, sectors_by_hour.get((7 + index) % 24, 0)))
        for index in range(24)
    ]


def parse_param_date(value: str | None) -> date | None:
    if not value:
        return None
    return as_date(value)


def date_in_absolute_window(day_date: date, start: date | None, end: date | None) -> bool:
    if start is not None and day_date < start:
        return False
    if end is not None and day_date > end:
        return False
    return True


def shift_year(day_date: date, years: int) -> date:
    target_year = day_date.year + years
    try:
        return day_date.replace(year=target_year)
    except ValueError:
        return day_date.replace(year=target_year, day=28)


def date_range_days(start: date, end: date) -> list[date]:
    if end < start:
        return []
    return [
        start + timedelta(days=offset)
        for offset in range((end - start).days + 1)
    ]


def date_in_month_day_window(day_date: date, start: date | None, end: date | None) -> bool:
    if start is None or end is None:
        return True
    day_key = (day_date.month, day_date.day)
    start_key = (start.month, start.day)
    end_key = (end.month, end.day)
    if start_key <= end_key:
        return start_key <= day_key <= end_key
    return day_key >= start_key or day_key <= end_key


def special_day_key(value: str) -> tuple[int | None, int, int] | None:
    text = value.strip()
    if not text:
        return None

    parsed = as_date(text)
    if parsed is not None:
        return parsed.year, parsed.month, parsed.day

    month_day_match = re.match(r"^(\d{1,2})[-/.](\d{1,2})$", text)
    if not month_day_match:
        return None

    first = int(month_day_match.group(1))
    second = int(month_day_match.group(2))
    if "." in text or "/" in text:
        day = first
        month = second
    elif first > 12 and 1 <= second <= 12:
        day = first
        month = second
    else:
        month = first
        day = second
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return None, month, day


def parse_special_day_text(value: str) -> list[str]:
    return [
        item.strip()
        for item in re.split(r"[,;\n]+", value)
        if item.strip()
    ]


def is_special_day(day_date: date, params: AnalysisParams) -> bool:
    for item in params.special_days:
        key = special_day_key(item)
        if key is None:
            continue
        year, month, day = key
        if day_date.month == month and day_date.day == day and (year is None or day_date.year == year):
            return True
    return False


def in_season(day_date: date, params: AnalysisParams) -> bool:
    month = day_date.month
    if params.season_start_month <= params.season_end_month:
        return params.season_start_month <= month <= params.season_end_month
    return month >= params.season_start_month or month <= params.season_end_month


def is_weekend(weekday: str) -> bool:
    return weekday in {"SO", "NE"}


def reference_density(records: list[SectorRecord], params: AnalysisParams) -> dict[str, float | None]:
    densities: dict[str, float | None] = {"weekday": None, "weekend": None}
    for key, weekend in (("weekday", False), ("weekend", True)):
        subset = [
            record
            for record in records
            if record.block_year == params.reference_year
            and record.flights is not None
            and record.actual_total > 0
            and in_season(record.day_date, params)
            and is_weekend(record.weekday) == weekend
        ]
        if subset:
            densities[key] = sum(float(record.flights or 0.0) for record in subset) / sum(
                float(record.actual_total)
                for record in subset
            )
    return densities


def fatigue_required_total(
    record: SectorRecord,
    hybrid_total: int,
    reference_densities: dict[str, float | None],
    params: AnalysisParams,
) -> int | None:
    if not params.fatigue_enabled or params.fatigue_lambda <= 0 or record.flights is None:
        return None
    if not in_season(record.day_date, params):
        return None
    density_key = "weekend" if is_weekend(record.weekday) else "weekday"
    density = reference_densities.get(density_key)
    if density is None or density <= 0:
        return None
    allowed_density = density * (1 + params.allowed_density_increase)
    if allowed_density <= 0:
        return None
    max_total = int(params.max_sectors * 19 + params.night_add)
    required = int(math.ceil(float(record.flights) / allowed_density))
    return max(hybrid_total, min(max_total, required))


def predict_record(
    record: SectorRecord,
    coefficients: dict[str, Any],
    profiles: dict[tuple[int, int], float],
    fallbacks: dict[tuple[int, int], float],
    reference_densities: dict[str, float | None],
    params: AnalysisParams,
    thresholds: dict[str, float],
) -> dict[str, Any]:
    target = daily_target(record, coefficients, params)
    base_profile = hourly_profile_for_record(record, profiles, fallbacks)
    hybrid_details = scaled_profile_details(base_profile, target, params.night_add, params.max_sectors)
    hybrid_hours = sectors_from_scaled_profile(hybrid_details["scaled"], params.max_sectors, thresholds)
    hybrid_hours = cap_daytime_hours_to_total(
        hybrid_hours,
        hybrid_details["scaled"],
        target,
        params.night_add,
        params.max_sectors,
    )
    final_hours = hybrid_hours
    calculator_hours = hourly_for_calculator(hybrid_hours, params.night_add, params.max_sectors)
    hybrid_total = sum(calculator_hours)
    fatigue_required = fatigue_required_total(record, hybrid_total, reference_densities, params)
    fatigue_adjusted_target: float | None = None
    fatigue_details: dict[str, Any] | None = None
    adjusted_hours: list[int] | None = None

    if fatigue_required is not None and fatigue_required > hybrid_total:
        fatigue_adjusted_target = hybrid_total + params.fatigue_lambda * (fatigue_required - hybrid_total)
        fatigue_details = scaled_profile_details(base_profile, fatigue_adjusted_target, params.night_add, params.max_sectors)
        adjusted_hours = sectors_from_scaled_profile(fatigue_details["scaled"], params.max_sectors, thresholds)
        adjusted_hours = cap_daytime_hours_to_total(
            adjusted_hours,
            fatigue_details["scaled"],
            fatigue_adjusted_target,
            params.night_add,
            params.max_sectors,
        )
        if params.fatigue_apply_max:
            final_hours = [max(hybrid, adjusted) for hybrid, adjusted in zip(hybrid_hours, adjusted_hours)]
            final_hours = cap_daytime_hours_to_total(
                final_hours,
                fatigue_details["scaled"],
                fatigue_adjusted_target,
                params.night_add,
                params.max_sectors,
                minimum_daytime_hours=hybrid_hours,
            )
        else:
            final_hours = adjusted_hours
        calculator_hours = hourly_for_calculator(final_hours, params.night_add, params.max_sectors)

    safety_base_total = sum(calculator_hours)
    if params.planning_safety_margin > 0:
        priority = fatigue_details["scaled"] if fatigue_details else hybrid_details["scaled"]
        final_hours = raise_daytime_hours_to_total(
            final_hours,
            priority,
            safety_base_total * (1 + params.planning_safety_margin),
            params.night_add,
            params.max_sectors,
        )
        calculator_hours = hourly_for_calculator(final_hours, params.night_add, params.max_sectors)

    return {
        "target": target,
        "base_profile": base_profile,
        "base_sum": hybrid_details["base_sum"],
        "target_daytime": hybrid_details["target_daytime"],
        "calibration_factor": hybrid_details["factor"],
        "scaled_profile": hybrid_details["scaled"],
        "hybrid_hours": hybrid_hours,
        "final_hours": final_hours,
        "hourly_for_calculator": calculator_hours,
        "prediction_total": sum(calculator_hours),
        "hybrid_total": hybrid_total,
        "fatigue_required_total": fatigue_required,
        "fatigue_adjusted_target": fatigue_adjusted_target,
        "fatigue_base_sum": fatigue_details["base_sum"] if fatigue_details else None,
        "fatigue_target_daytime": fatigue_details["target_daytime"] if fatigue_details else None,
        "fatigue_calibration_factor": fatigue_details["factor"] if fatigue_details else None,
        "fatigue_scaled_profile": fatigue_details["scaled"] if fatigue_details else None,
        "fatigue_adjusted_hours": adjusted_hours,
        "planning_safety_base_total": safety_base_total,
    }


def weighted_error_score(rows: list[dict[str, Any]], params: AnalysisParams) -> float:
    if not rows:
        return float("inf")
    total = 0.0
    for row in rows:
        error = float(row["prediction"]) - float(row["actual"])
        if error < 0:
            total += params.under_prediction_weight * abs(error)
        else:
            total += params.over_prediction_weight * error
    return total / len(rows)


def ordered_thresholds(thresholds: dict[str, float], max_sectors: int) -> dict[str, float]:
    cleaned = dict(thresholds)
    previous = 0.5
    for sector in range(2, max_sectors + 1):
        key = str(sector)
        value = float(cleaned.get(key, sector - 0.5))
        value = max(previous + 0.05, value)
        cleaned[key] = round(value, 4)
        previous = value
    return cleaned


def threshold_candidates(center: float, radius: float, step: float) -> list[float]:
    if radius <= 0:
        return [round(center, 4)]
    steps = int(round(radius / step))
    values = [center + offset * step for offset in range(-steps, steps + 1)]
    return sorted({round(max(0.05, value), 4) for value in values})


def operational_rows_for_records(
    records: list[SectorRecord],
    coefficients: dict[str, Any],
    profiles: dict[tuple[int, int], float],
    fallbacks: dict[tuple[int, int], float],
    reference_densities: dict[str, float | None],
    params: AnalysisParams,
    thresholds: dict[str, float],
) -> list[dict[str, Any]]:
    rows = []
    for record in records:
        if not record.has_actual:
            continue
        prediction = predict_record(record, coefficients, profiles, fallbacks, reference_densities, params, thresholds)
        rows.append({
            "date": record.day_date,
            "weekday": record.weekday,
            "flights": record.flights,
            "actual": record.actual_total,
            "prediction": prediction["prediction_total"],
            "traffic_target": prediction["target"],
        })
    return rows


def evaluation_rows_for_records(
    records: list[SectorRecord],
    coefficients: dict[str, Any],
    profiles: dict[tuple[int, int], float],
    fallbacks: dict[tuple[int, int], float],
    reference_densities: dict[str, float | None],
    params: AnalysisParams,
    thresholds: dict[str, float],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    traffic_rows: list[dict[str, Any]] = []
    operational_rows: list[dict[str, Any]] = []
    hour_errors: list[dict[str, Any]] = []
    for record in records:
        if not record.has_actual or record.flights is None:
            continue
        prediction = predict_record(record, coefficients, profiles, fallbacks, reference_densities, params, thresholds)
        traffic_rows.append({
            "date": record.day_date,
            "weekday": record.weekday,
            "flights": record.flights,
            "actual": record.actual_total,
            "prediction": prediction["target"],
        })
        operational_rows.append({
            "date": record.day_date,
            "weekday": record.weekday,
            "flights": record.flights,
            "actual": record.actual_total,
            "prediction": prediction["prediction_total"],
            "traffic_target": prediction["target"],
        })
        for hour_index, actual in enumerate(record.hourly[:19]):
            hour_errors.append({
                "date": record.day_date,
                "weekday": record.weekday,
                "hour_index": hour_index,
                "actual": actual,
                "prediction": prediction["final_hours"][hour_index],
            })
    return traffic_rows, operational_rows, hour_errors


def optimize_thresholds_coordinate_search(
    validation_records: list[SectorRecord],
    coefficients: dict[str, Any],
    profiles: dict[tuple[int, int], float],
    fallbacks: dict[tuple[int, int], float],
    reference_densities: dict[str, float | None],
    params: AnalysisParams,
) -> tuple[dict[str, float], dict[str, Any]]:
    current = ordered_thresholds(params.thresholds, params.max_sectors)
    if params.lock_thresholds or not params.optimize_thresholds or len(validation_records) < 7:
        return current, {
            "method": "manual_thresholds",
            "score": None,
            "passes": 0,
            "records": len(validation_records),
        }

    def score(thresholds: dict[str, float]) -> tuple[float, float]:
        rows = operational_rows_for_records(
            validation_records,
            coefficients,
            profiles,
            fallbacks,
            reference_densities,
            params,
            thresholds,
        )
        metric = metrics(rows)
        return weighted_error_score(rows, params), float(metric["mae"] or 0)

    best_score, best_mae = score(current)
    improvements = 0
    passes = 0
    for _ in range(3):
        changed = False
        passes += 1
        for sector in range(2, params.max_sectors + 1):
            key = str(sector)
            local_best = current
            local_score = best_score
            local_mae = best_mae
            for candidate in threshold_candidates(current[key], params.threshold_search_radius, params.threshold_search_step):
                trial = dict(current)
                trial[key] = candidate
                trial = ordered_thresholds(trial, params.max_sectors)
                if trial[key] != round(candidate, 4):
                    continue
                trial_score, trial_mae = score(trial)
                if (trial_score, trial_mae) < (local_score, local_mae):
                    local_best = trial
                    local_score = trial_score
                    local_mae = trial_mae
            if local_best != current:
                current = local_best
                best_score = local_score
                best_mae = local_mae
                improvements += 1
                changed = True
        if not changed:
            break

    return current, {
        "method": "coordinate_grid",
        "score": rounded(best_score, 4),
        "mae": rounded(best_mae, 4),
        "passes": passes,
        "improvements": improvements,
        "records": len(validation_records),
        "step": params.threshold_search_step,
        "radius": params.threshold_search_radius,
    }


def select_analog_records(all_records: list[SectorRecord], test_records: list[SectorRecord], params: AnalysisParams) -> list[SectorRecord]:
    if not params.analog_backtest_enabled:
        return []
    start = parse_param_date(params.planning_start_date)
    end = parse_param_date(params.planning_end_date)
    forecast_start = parse_param_date(params.forecast_start_date)
    forecast_end = parse_param_date(params.forecast_end_date)
    if start is None and end is None and forecast_start is not None and forecast_end is not None:
        start = shift_year(forecast_start, -1)
        end = shift_year(forecast_end, -1)
    if start is None or end is None:
        if not test_records:
            return []
        start = min(record.day_date for record in test_records)
        end = max(record.day_date for record in test_records)
    return [
        record
        for record in all_records
        if record.flights is not None
        and date_in_absolute_window(record.day_date, start, end)
    ]


def unique_records_by_date(records: list[SectorRecord]) -> list[SectorRecord]:
    seen: set[date] = set()
    unique: list[SectorRecord] = []
    for record in records:
        if record.day_date in seen:
            continue
        seen.add(record.day_date)
        unique.append(record)
    return unique


def select_checked_records(
    all_records: list[SectorRecord],
    test_records: list[SectorRecord],
    analog_records: list[SectorRecord],
    params: AnalysisParams,
) -> list[SectorRecord]:
    target_actual_records = [
        record
        for record in test_records
        if record.has_actual and record.flights is not None
    ]
    current_actual_records = [
        record
        for record in all_records
        if record.block_year == params.test_year
        and record.has_actual
        and record.flights is not None
    ]
    return unique_records_by_date([*target_actual_records, *current_actual_records, *analog_records])


def forecast_day_row(
    record: SectorRecord,
    prediction: dict[str, Any],
    coefficients: dict[str, Any],
    params: AnalysisParams,
    thresholds: dict[str, float],
) -> dict[str, Any]:
    calculator_hours = prediction["hourly_for_calculator"]
    explain_hours = []
    for hour_index, profile_value in enumerate(prediction["base_profile"]):
        explain_hours.append({
            "hour": f"{(6 + hour_index) % 24:02d}:00",
            "profile": rounded(profile_value, 3),
            "z": rounded(prediction["scaled_profile"][hour_index], 3),
            "hybrid_sector": prediction["hybrid_hours"][hour_index],
            "final_sector": prediction["final_hours"][hour_index],
            "actual_sector": record.hourly[hour_index] if record.has_actual and hour_index < len(record.hourly) else None,
        })

    return {
        "date": record.day_date.isoformat(),
        "weekday": record.weekday,
        "special_day": is_special_day(record.day_date, params),
        "special_day_buffer": params.special_day_buffer if is_special_day(record.day_date, params) else 0,
        "flights": rounded(record.flights, 1) if record.flights is not None else None,
        "traffic_target": rounded(prediction["target"], 2),
        "target_daytime_sector_hours": rounded(prediction["target_daytime"], 2),
        "base_profile_sum": rounded(prediction["base_sum"], 3),
        "calibration_factor": rounded(prediction["calibration_factor"], 5),
        "predicted_sector_hours": sum(calculator_hours),
        "actual_sector_hours": rounded(record.actual_total, 1) if record.has_actual else None,
        "has_actual": record.has_actual,
        "daytime_hours": prediction["final_hours"],
        "actual_daytime_hours": record.hourly[:19] if record.has_actual else [],
        "hourly_for_calculator": calculator_hours,
        "formula": formula_text(record, coefficients, params, prediction["target"]),
        "hybrid_sector_hours": prediction["hybrid_total"],
        "fatigue_required_sector_hours": prediction["fatigue_required_total"],
        "fatigue_adjusted_target": rounded(prediction["fatigue_adjusted_target"], 2),
        "fatigue_calibration_factor": rounded(prediction["fatigue_calibration_factor"], 5),
        "base_profile": [
            rounded(value, 3)
            for value in prediction["base_profile"]
        ],
        "z_values": [
            rounded(value, 3)
            for value in prediction["scaled_profile"]
        ],
        "explain_hours": explain_hours,
        "thresholds": {
            key: rounded(value, 4)
            for key, value in thresholds.items()
        },
    }


def summarize_patterns(forecast_days: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, ...], dict[str, Any]] = {}
    for day in forecast_days:
        values = tuple(int(value) for value in day.get("hourly_for_calculator", []))
        if len(values) != 24:
            continue
        item = grouped.setdefault(values, {
            "hourly_for_calculator": list(values),
            "count": 0,
            "dates": [],
            "weekdays": {},
            "sector_hours": sum(values),
        })
        item["count"] += 1
        if len(item["dates"]) < 12:
            item["dates"].append(day.get("date"))
        weekday = day.get("weekday")
        if weekday:
            item["weekdays"][weekday] = item["weekdays"].get(weekday, 0) + 1

    suggestions = sorted(
        grouped.values(),
        key=lambda item: (-int(item["count"]), -int(item["sector_hours"]), item["hourly_for_calculator"]),
    )[:limit]

    for index, suggestion in enumerate(suggestions, start=1):
        weekday_summary = ", ".join(
            f"{weekday}:{count}"
            for weekday, count in sorted(
                suggestion["weekdays"].items(),
                key=lambda item: (-item[1], WEEKDAYS.index(item[0]) if item[0] in WEEKDAYS else 99),
            )
        )
        suggestion["rank"] = index
        suggestion["label"] = (
            f"Vzorec {index}: {suggestion['sector_hours']} SH, "
            f"{suggestion['count']} dni"
            + (f" ({weekday_summary})" if weekday_summary else "")
        )
    return suggestions


def last_sunday(year: int, month: int) -> date:
    current = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    while current.weekday() != 6:
        current -= timedelta(days=1)
    return current


def month_label(day_date: date) -> str:
    labels = {
        1: "januar",
        2: "februar",
        3: "marec",
        4: "april",
        5: "maj",
        6: "junij",
        7: "julij",
        8: "avgust",
        9: "september",
        10: "oktober",
        11: "november",
        12: "december",
    }
    return labels[day_date.month]


def operational_period(day_text: str) -> str:
    day_date = datetime.strptime(day_text, "%Y-%m-%d").date()
    if day_date.month == 10:
        transition = last_sunday(day_date.year, 10)
        if day_date < transition:
            return "oktober pred prestavitvijo ure"
        return "oktober po prestavitvi ure"
    return month_label(day_date)


def operational_day_type(weekday: str, special_day: bool = False) -> str:
    if special_day:
        return "posebni dnevi"
    if weekday in {"PO", "TO", "SR", "ČE"}:
        return "PO-ČE"
    if weekday == "PE":
        return "PE"
    return "SO-NE"


def configuration_capacity_library() -> list[dict[str, Any]]:
    paths: list[Path] = []
    configured = os.environ.get(CONFIG_LIBRARY_ENV)
    if configured:
        paths.append(Path(configured))
    paths.extend(DEFAULT_CONFIG_LIBRARY_PATHS)

    selected_path = next((path for path in paths if path.exists()), None)
    if selected_path is None:
        return []

    try:
        with selected_path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle, delimiter=";"))
    except OSError:
        return []
    if not rows:
        return []

    headers = rows[0][1:]
    data = {row[0]: row[1:] for row in rows[1:] if row}
    max_row = data.get("MODEL_MAX_SH", [])
    total_row = data.get("MODEL_TOTAL", [])
    status_row = data.get("MODEL_STATUS", [])
    candidates: list[dict[str, Any]] = []

    for index, name in enumerate(headers):
        if not name or name == "Mušter":
            continue
        try:
            max_sh = int(float(str(max_row[index]).replace(",", ".")))
            total = int(float(str(total_row[index]).replace(",", ".")))
        except (IndexError, ValueError):
            continue
        status = status_row[index] if index < len(status_row) else ""
        if status and status != "OK":
            continue
        candidates.append({
            "name": name,
            "total_people": total,
            "max_sector_hours": max_sh,
            "reserve_sector_hours": 0,
            "status": status or "OK",
        })

    return candidates


def candidates_for_block(required_sector_hours: int, library: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    if not library:
        return []

    feasible: list[dict[str, Any]] = []
    for candidate in library:
        reserve = int(candidate["max_sector_hours"]) - required_sector_hours
        if reserve >= 0:
            feasible.append({**candidate, "reserve_sector_hours": reserve})
    if feasible:
        return sorted(
            feasible,
            key=lambda item: (
                int(item["total_people"]),
                int(item["reserve_sector_hours"]),
                int(item["max_sector_hours"]),
                str(item["name"]),
            ),
        )[:limit]

    below = [
        {**candidate, "reserve_sector_hours": int(candidate["max_sector_hours"]) - required_sector_hours}
        for candidate in library
    ]
    return sorted(
        below,
        key=lambda item: (
            -int(item["max_sector_hours"]),
            int(item["total_people"]),
            str(item["name"]),
        ),
    )[:limit]


def cluster_operational_days(days: list[dict[str, Any]], tolerance: int) -> list[list[dict[str, Any]]]:
    remaining = sorted(days, key=lambda item: (-int(item["sector_hours"]), str(item["date"])))
    clusters: list[list[dict[str, Any]]] = []
    while remaining:
        anchor_total = int(remaining[0]["sector_hours"])
        cluster: list[dict[str, Any]] = []
        next_remaining: list[dict[str, Any]] = []
        for item in remaining:
            if anchor_total - int(item["sector_hours"]) <= tolerance:
                cluster.append(item)
            else:
                next_remaining.append(item)
        clusters.append(cluster)
        remaining = next_remaining
    return clusters


def summarize_operational_blocks(forecast_days: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for day in forecast_days:
        day_text = day.get("date")
        values = [int(value) for value in day.get("hourly_for_calculator", [])]
        if not day_text or len(values) != 24:
            continue
        period = operational_period(str(day_text))
        day_type = operational_day_type(str(day.get("weekday")), bool(day.get("special_day")))
        key = (period, day_type)
        item = grouped.setdefault(key, {
            "period": period,
            "day_type": day_type,
            "count": 0,
            "dates": [],
            "all_dates": [],
            "weekday_counts": {},
            "days": [],
            "day_sector_hours": [],
        })
        sector_hours = sum(values)
        item["count"] += 1
        item["all_dates"].append(day_text)
        if len(item["dates"]) < 12:
            item["dates"].append(day_text)
        weekday = day.get("weekday")
        if weekday:
            item["weekday_counts"][weekday] = item["weekday_counts"].get(weekday, 0) + 1
        item["days"].append({
            "date": day_text,
            "weekday": weekday,
            "flights": day.get("flights"),
            "hourly_for_calculator": values,
            "sector_hours": sector_hours,
        })
        item["day_sector_hours"].append(sector_hours)

    library = configuration_capacity_library()
    blocks = []
    period_order = {
        "oktober pred prestavitvijo ure": 10.1,
        "oktober po prestavitvi ure": 10.2,
        "november": 11,
        "december": 12,
        "januar": 13,
    }
    day_type_order = {"PO-ČE": 1, "PE": 2, "SO-NE": 3, "posebni dnevi": 4}

    for item in grouped.values():
        for cluster in cluster_operational_days(item["days"], OPERATIONAL_BLOCK_SH_TOLERANCE):
            representative = max(
                cluster,
                key=lambda day: (
                    int(day["sector_hours"]),
                    float(day["flights"]) if day.get("flights") is not None else -1.0,
                    max(day["hourly_for_calculator"]),
                    day["hourly_for_calculator"],
                    str(day["date"]),
                ),
            )
            values = list(representative["hourly_for_calculator"])
            required = int(representative["sector_hours"])
            dates = sorted(str(day["date"]) for day in cluster)
            day_totals = [int(day["sector_hours"]) for day in cluster]
            flights = [
                float(day["flights"])
                for day in cluster
                if day.get("flights") is not None
            ]
            weekday_counts: dict[str, int] = {}
            for day in cluster:
                weekday = day.get("weekday")
                if weekday:
                    weekday_counts[weekday] = weekday_counts.get(weekday, 0) + 1

            label = f"{item['period']} · {item['day_type']} · {required} SH"
            if representative.get("date"):
                label = f"{label} · profil {representative['date']}"

            blocks.append({
                "rank": 0,
                "label": label,
                "period": item["period"],
                "day_type": item["day_type"],
                "count": len(cluster),
                "date_start": dates[0] if dates else None,
                "date_end": dates[-1] if dates else None,
                "dates": dates[:12],
                "weekday_counts": weekday_counts,
                "sector_hours": required,
                "avg_day_sector_hours": rounded(statistics.mean(day_totals), 1) if day_totals else None,
                "min_day_sector_hours": min(day_totals) if day_totals else None,
                "max_day_sector_hours": max(day_totals) if day_totals else None,
                "representative_date": representative.get("date"),
                "representative_flights": rounded(representative.get("flights"), 1),
                "max_flights": rounded(max(flights), 1) if flights else None,
                "sector_hour_tolerance": OPERATIONAL_BLOCK_SH_TOLERANCE,
                "hourly_for_calculator": values,
                "config_candidates": candidates_for_block(required, library),
            })

    blocks.sort(
        key=lambda item: (
            period_order.get(item["period"], 99),
            day_type_order.get(item["day_type"], 99),
            -int(item["sector_hours"]),
            item["date_start"] or "",
        )
    )
    for index, block in enumerate(blocks, start=1):
        block["rank"] = index
    return blocks


def compute_analysis(reader: XlsxReader, mapping: AnalysisMapping, params: AnalysisParams) -> dict[str, Any]:
    sector_sheet = reader.sheet(mapping.sector_sheet)
    adjusted_sheet = (
        reader.sheet(mapping.adjusted_sector_sheet)
        if mapping.adjusted_sector_sheet in reader.sheet_names()
        else sector_sheet
    )
    traffic_sheet = reader.sheet(mapping.traffic_sheet)
    forecast_traffic_sheet = (
        reader.sheet(mapping.forecast_traffic_sheet)
        if mapping.forecast_traffic_sheet in reader.sheet_names()
        else None
    )
    detected_year_rows = detect_year_rows(sector_sheet)
    full_mapping = mapping.model_copy(update={
        "year_rows": fill_missing_year_rows(mapping, detected_year_rows),
    })
    traffic = parse_traffic(traffic_sheet, full_mapping)
    forecast_sheet_traffic = parse_forecast_traffic(forecast_traffic_sheet) if forecast_traffic_sheet else {}
    forecast_traffic: dict[int, ForecastTrafficRecord] = {}
    traffic_forecast_summary: dict[str, Any] = {
        "mode": params.traffic_forecast_mode,
        "generated_days": 0,
        "actual_days_kept": 0,
        "missing_days": 0,
        "sample": [],
    }
    sector_records = parse_sector_records(sector_sheet, traffic, full_mapping, params.night_add)
    profile_records = parse_sector_records(adjusted_sheet, traffic, full_mapping, params.night_add)
    fit_records = [
        record for record in sector_records
        if record.block_year in params.fit_years and record.flights is not None
        and not (params.special_day_exclude_from_fit and is_special_day(record.day_date, params))
    ]
    fit_profile_records = [
        record for record in profile_records
        if record.block_year in params.fit_years
    ]
    test_records = select_target_records(sector_records, traffic, params)
    if params.traffic_forecast_mode == "excel_sheet" and forecast_sheet_traffic:
        forecast_traffic = forecast_sheet_traffic
        traffic_forecast_summary = {
            "mode": "excel_sheet",
            "generated_days": len(forecast_traffic),
            "actual_days_kept": 0,
            "missing_days": 0,
            "sample": [
                {
                    "date": forecast.day_date.isoformat() if forecast.day_date else None,
                    "weekday": forecast.weekday,
                    "flights": rounded(forecast.flights, 1),
                    "source": forecast.source,
                }
                for forecast in list(forecast_traffic.values())[:10]
            ],
        }
    elif params.traffic_forecast_mode in {"previous_year_growth", "weighted_history"}:
        if forecast_sheet_traffic and not (params.forecast_start_date or params.forecast_end_date):
            test_records = apply_forecast_calendar(test_records, forecast_sheet_traffic)
        forecast_traffic, traffic_forecast_summary = build_generated_forecast_traffic(
            sector_records,
            test_records,
            params,
        )
    elif forecast_sheet_traffic and not (params.forecast_start_date or params.forecast_end_date):
        test_records = apply_forecast_calendar(test_records, forecast_sheet_traffic)

    if forecast_traffic:
        if params.forecast_start_date or params.forecast_end_date:
            test_records = apply_forecast_traffic_by_date(test_records, forecast_traffic)
        else:
            test_records = apply_forecast_traffic(test_records, forecast_traffic)

    regression_fit = fit_daily_regression(fit_records)
    optimized_fit = (
        optimize_daily_coefficients_cp_sat(fit_records, params, regression_fit)
        if params.optimize_with_cp_sat
        else regression_fit
    )
    coefficients = tuned_coefficients(optimized_fit, params)
    profiles, fallbacks = build_profiles(fit_profile_records or fit_records, params)
    reference_densities = reference_density(sector_records, params)
    analog_records = select_analog_records(sector_records, test_records, params)
    checked_records = select_checked_records(sector_records, test_records, analog_records, params)
    validation_records = checked_records or fit_records
    threshold_fit_params = params.model_copy(update={"planning_safety_margin": 0.0})
    optimized_thresholds, threshold_optimization = optimize_thresholds_coordinate_search(
        validation_records,
        coefficients,
        profiles,
        fallbacks,
        reference_densities,
        threshold_fit_params,
    )

    forecast_days: list[dict[str, Any]] = []
    for record in test_records:
        prediction = predict_record(
            record,
            coefficients,
            profiles,
            fallbacks,
            reference_densities,
            params,
            optimized_thresholds,
        )
        forecast_day = forecast_day_row(record, prediction, coefficients, params, optimized_thresholds)
        forecast_days.append(forecast_day)

    traffic_fit_rows, operational_rows, hour_errors = evaluation_rows_for_records(
        checked_records,
        coefficients,
        profiles,
        fallbacks,
        reference_densities,
        params,
        optimized_thresholds,
    )

    hourly_distribution: dict[str, int] = {}
    for row in hour_errors:
        error = int(row["prediction"] - row["actual"])
        hourly_distribution[str(error)] = hourly_distribution.get(str(error), 0) + 1
    exact = sum(row["prediction"] == row["actual"] for row in hour_errors)
    within_one = sum(abs(row["prediction"] - row["actual"]) <= 1 for row in hour_errors)
    hourly_metrics = {
        "count": len(hour_errors),
        "exact_percent": rounded(100 * exact / len(hour_errors), 1) if hour_errors else None,
        "within_one_percent": rounded(100 * within_one / len(hour_errors), 1) if hour_errors else None,
        "mae": rounded(statistics.mean(abs(row["prediction"] - row["actual"]) for row in hour_errors), 3) if hour_errors else None,
        "bias": rounded(statistics.mean(row["prediction"] - row["actual"] for row in hour_errors), 3) if hour_errors else None,
        "error_distribution": dict(sorted(hourly_distribution.items(), key=lambda item: int(item[0]))),
    }
    analog_rows = operational_rows_for_records(
        analog_records,
        coefficients,
        profiles,
        fallbacks,
        reference_densities,
        params,
        optimized_thresholds,
    )

    top_misses = sorted(
        operational_rows,
        key=lambda row: abs(float(row["prediction"]) - float(row["actual"])),
        reverse=True,
    )[:15]
    pattern_suggestions = summarize_patterns(forecast_days)
    operational_blocks = summarize_operational_blocks(forecast_days)

    return {
        "mapping": full_mapping.model_dump(),
        "data_counts": {
            "traffic_days": len(traffic),
            "sector_days": len(sector_records),
            "profile_days": len(fit_profile_records),
            "fit_days": len(fit_records),
            "test_days": len(test_records),
            "forecast_days": len(test_records),
            "checked_days": len(operational_rows),
            "analog_days": len(analog_records),
            "known_target_days": sum(1 for record in test_records if record.has_actual and record.flights is not None),
        },
        "formula": {
            "template": (
                "Prometni cilj sektorske ure = "
                "max(minimalne sektorske ure; intercept + koeficient_na_prelet * preleti + popravek_dneva). "
                "Če pragovi po urni pretvorbi presežejo prometni cilj, se najmanj obremenjene ure znižajo do cilja. "
                "Planerski safety se nato doda na koncni dnevni seštevek sektorjev."
            ),
            "example": forecast_days[0]["formula"] if forecast_days else None,
        },
        "optimization": {
            "method": coefficients.get("method", "least_squares"),
            "solver_status": coefficients.get("solver_status"),
            "objective_value": rounded(coefficients.get("objective_value"), 2),
            "best_objective_bound": rounded(coefficients.get("best_objective_bound"), 2),
            "cp_sat_time_limit_seconds": params.cp_sat_time_limit_seconds,
            "under_prediction_weight": params.under_prediction_weight,
            "over_prediction_weight": params.over_prediction_weight,
            "lock_manual_coefficients": params.lock_manual_coefficients,
            "lock_intercept": params.lock_intercept,
            "lock_coefficient": params.lock_coefficient,
            "lock_weekday_adjustments": params.lock_weekday_adjustments,
            "lock_thresholds": params.lock_thresholds,
        },
        "traffic_forecast": traffic_forecast_summary,
        "threshold_optimization": threshold_optimization,
        "reference_density": {
            key: rounded(value, 4)
            for key, value in reference_densities.items()
        },
        "raw_coefficients": {
            "intercept": rounded(regression_fit["intercept"], 6),
            "coefficient_per_flight": rounded(regression_fit["coefficient_per_flight"], 9),
            "weekday_adjustments": {
                key: rounded(value, 6)
                for key, value in regression_fit["weekday_adjustments"].items()
            },
            "sample_size": regression_fit["sample_size"],
        },
        "used_coefficients": {
            "intercept": rounded(coefficients["intercept"], 6),
            "coefficient_per_flight": rounded(coefficients["coefficient_per_flight"], 9),
            "weekday_adjustments": {
                key: rounded(value, 6)
                for key, value in coefficients["weekday_adjustments"].items()
            },
            "thresholds": {
                key: rounded(value, 4)
                for key, value in optimized_thresholds.items()
            },
        },
        "traffic_fit_metrics": metrics(traffic_fit_rows),
        "operational_fit_metrics": metrics(operational_rows),
        "checked_fit_metrics": metrics(operational_rows),
        "analog_fit_metrics": metrics(analog_rows),
        "hourly_metrics": hourly_metrics,
        "weekday_summary": summarize_weekdays(operational_rows),
        "monthly_summary": summarize_months(operational_rows),
        "hourly_summary": summarize_hours(hour_errors),
        "top_misses": [
            {
                "date": row["date"].isoformat(),
                "weekday": row["weekday"],
                "flights": rounded(row["flights"], 1) if row["flights"] is not None else None,
                "actual": rounded(row["actual"], 1),
                "prediction": rounded(row["prediction"], 1),
                "error": rounded(float(row["prediction"]) - float(row["actual"]), 1),
                "traffic_target": rounded(row["traffic_target"], 2),
            }
            for row in top_misses
        ],
        "pattern_suggestions": pattern_suggestions,
        "operational_blocks": operational_blocks,
        "forecast_days": forecast_days,
        "test_rows_sample": [
            {
                "date": row["date"].isoformat(),
                "weekday": row["weekday"],
                "flights": rounded(row["flights"], 1) if row["flights"] is not None else None,
                "actual": rounded(row["actual"], 1),
                "prediction": rounded(row["prediction"], 1),
                "error": rounded(float(row["prediction"]) - float(row["actual"]), 1),
            }
            for row in operational_rows[:30]
        ],
    }


def model_params_from_workbook(reader: XlsxReader) -> AnalysisParams:
    params = AnalysisParams()
    if "MODEL" not in reader.sheet_names():
        return params

    sheet = reader.sheet("MODEL")
    year_weights = dict(params.year_weights)
    thresholds = dict(params.thresholds)
    weekday_overrides = dict(params.weekday_adjustment_overrides)
    annual_growth_rates = dict(params.annual_traffic_growth_rates)
    special_days = list(params.special_days)

    for row in range(1, sheet.max_row + 1):
        label = str(sheet.value(row, 1) or sheet.value(row, 4) or "").strip().upper()
        text_value = str(sheet.value(row, 2) or "").strip()
        left_value = as_float(sheet.value(row, 2))
        right_label = str(sheet.value(row, 4) or "").strip().upper()
        right_value = as_float(sheet.value(row, 5))

        growth_match = re.search(r"RAST\s+(\d{2,4})\s*>\s*(\d{2,4})", label)
        if growth_match and left_value is not None:
            source_year = growth_match.group(1)
            if len(source_year) == 2:
                source_year = f"20{source_year}"
            annual_growth_rates[source_year] = left_value

        weight_match = re.search(r"UTEŽ\s+ZA\s+LETO\s+(\d{2,4})", label)
        if weight_match and left_value is not None:
            year = weight_match.group(1)
            if len(year) == 2:
                year = f"20{year}"
            year_weights[year] = left_value
        elif "NOČNI DODATEK" in label and left_value is not None:
            params = params.model_copy(update={"night_add": int(round(left_value))})
        elif "INTERCEPT" in label and left_value is not None:
            params = params.model_copy(update={"intercept_override": left_value})
        elif "KOEFICIENT NA PRELET" in label and left_value is not None:
            params = params.model_copy(update={"coefficient_override": left_value})
        elif label.startswith("DAN ") and left_value is not None:
            weekday = normalize_weekday(label.replace("DAN ", "", 1).strip())
            if weekday:
                weekday_overrides[weekday] = left_value
        elif "MAX SEKTORJEV" in label and left_value is not None:
            params = params.model_copy(update={"max_sectors": max(1, min(8, int(round(left_value))))})
        elif "2026 LJUDI DELAVNIK" in label and left_value is not None:
            params = params.model_copy(update={"target_weekday_staff": left_value})
        elif "2026 LJUDI VIKEND" in label and left_value is not None:
            params = params.model_copy(update={"target_weekend_staff": left_value})
        elif "DOVOLJEN DVIG NAD 2025" in label and left_value is not None:
            params = params.model_copy(update={"allowed_density_increase": left_value})
        elif "SEZONA OD MESECA" in label and left_value is not None:
            params = params.model_copy(update={"season_start_month": max(1, min(12, int(round(left_value))))})
        elif "SEZONA DO MESECA" in label and left_value is not None:
            params = params.model_copy(update={"season_end_month": max(1, min(12, int(round(left_value))))})
        elif "POSEBNI DNEVI" in label and text_value:
            special_days = parse_special_day_text(text_value)
        elif "SPECIAL BUFFER" in label and left_value is not None:
            params = params.model_copy(update={"special_day_buffer": left_value})
        elif "POSEBNI BUFFER" in label and left_value is not None:
            params = params.model_copy(update={"special_day_buffer": left_value})

        if right_label.startswith("PRAG ") and right_value is not None:
            match = re.search(r"PRAG\s+(\d)", right_label)
            if match:
                thresholds[match.group(1)] = right_value

    return params.model_copy(update={
        "year_weights": year_weights,
        "thresholds": thresholds,
        "weekday_adjustment_overrides": weekday_overrides,
        "annual_traffic_growth_rates": annual_growth_rates,
        "special_days": special_days,
    })


def workbook_profile(payload: WorkbookPayload) -> dict[str, Any]:
    reader = XlsxReader(decode_workbook(payload))
    sheets = []
    for name in reader.sheet_names():
        sheet = reader.sheet(name)
        sheets.append({
            "name": name,
            "max_row": sheet.max_row,
            "max_col": sheet.max_col,
        })

    mapping = payload.mapping or AnalysisMapping()
    detected_year_rows: dict[str, int] = {}
    if mapping.sector_sheet in reader.sheet_names():
        sector_sheet = reader.sheet(mapping.sector_sheet)
        detected_year_rows = detect_year_rows(sector_sheet)
        mapping = mapping.model_copy(update={"year_rows": suggested_year_rows(mapping, detected_year_rows)})

    return {
        "file_name": payload.file_name,
        "sheets": sheets,
        "suggested_mapping": mapping.model_dump(),
        "detected_year_rows": detected_year_rows,
        "suggested_params": model_params_from_workbook(reader).model_dump(),
    }


def run_model_analysis(payload: WorkbookPayload) -> dict[str, Any]:
    reader = XlsxReader(decode_workbook(payload))
    return compute_analysis(
        reader=reader,
        mapping=payload.mapping or AnalysisMapping(),
        params=payload.params or model_params_from_workbook(reader),
    )


def excel_col_name(index: int) -> str:
    name = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def xlsx_cell(value: Any = None, style: int = 0, formula: str | None = None) -> dict[str, Any]:
    return {
        "value": value,
        "style": style,
        "formula": formula,
    }


def style_row(values: list[Any], style: int) -> list[dict[str, Any]]:
    return [xlsx_cell(value, style=style) for value in values]


def write_cell_xml(ref: str, cell: Any) -> str:
    style = 0
    formula: str | None = None
    value = cell
    if isinstance(cell, dict):
        value = cell.get("value")
        style = int(cell.get("style") or 0)
        formula = cell.get("formula")

    attrs = [f'r="{ref}"']
    if style:
        attrs.append(f's="{style}"')

    if formula:
        formula_text_value = str(formula).lstrip("=")
        parts = [f"<c {' '.join(attrs)}><f>{escape(formula_text_value)}</f>"]
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
            parts.append(f"<v>{float(value):.10g}</v>")
        parts.append("</c>")
        return "".join(parts)

    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return f"<c {' '.join(attrs)} t=\"b\"><v>{1 if value else 0}</v></c>"
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return f"<c {' '.join(attrs)}><v>{float(value):.10g}</v></c>"

    text = escape(str(value))
    preserve = " xml:space=\"preserve\"" if str(value).strip() != str(value) else ""
    return f"<c {' '.join(attrs)} t=\"inlineStr\"><is><t{preserve}>{text}</t></is></c>"


def worksheet_xml(
    rows: list[list[Any]],
    *,
    freeze_row: int = 1,
    widths: dict[int, float] | None = None,
    conditional_ranges: list[str] | None = None,
    auto_filter: bool = True,
) -> str:
    max_row = len(rows)
    max_col = max((len(row) for row in rows), default=1)
    dimension = f"A1:{excel_col_name(max_col)}{max(max_row, 1)}"
    cols_xml = ""
    if widths:
        cols_xml = "<cols>" + "".join(
            f'<col min="{col}" max="{col}" width="{width}" customWidth="1"/>'
            for col, width in sorted(widths.items())
        ) + "</cols>"

    pane_xml = ""
    if freeze_row > 0:
        pane_xml = (
            f'<sheetViews><sheetView workbookViewId="0">'
            f'<pane ySplit="{freeze_row}" topLeftCell="A{freeze_row + 1}" activePane="bottomLeft" state="frozen"/>'
            f'</sheetView></sheetViews>'
        )
    else:
        pane_xml = '<sheetViews><sheetView workbookViewId="0"/></sheetViews>'

    row_xml: list[str] = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for col_index, cell in enumerate(row, start=1):
            cell_xml = write_cell_xml(f"{excel_col_name(col_index)}{row_index}", cell)
            if cell_xml:
                cells.append(cell_xml)
        row_xml.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    conditional_xml = ""
    for index, sqref in enumerate(conditional_ranges or [], start=1):
        conditional_xml += (
            f'<conditionalFormatting sqref="{sqref}">'
            f'<cfRule type="colorScale" priority="{index}">'
            '<colorScale>'
            '<cfvo type="min"/>'
            '<cfvo type="num" val="3"/>'
            '<cfvo type="max"/>'
            '<color rgb="FFEAF4FB"/>'
            '<color rgb="FFFDE68A"/>'
            '<color rgb="FF17365D"/>'
            '</colorScale>'
            '</cfRule>'
            '</conditionalFormatting>'
        )
    auto_filter_xml = f'<autoFilter ref="{dimension}"/>' if auto_filter and max_row > 1 else ""

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<dimension ref="{dimension}"/>'
        f'{pane_xml}'
        f'{cols_xml}'
        f'<sheetData>{"".join(row_xml)}</sheetData>'
        f'{auto_filter_xml}'
        f'{conditional_xml}'
        '</worksheet>'
    )


def workbook_styles_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="4">'
        '<font><sz val="11"/><color theme="1"/><name val="Calibri"/><family val="2"/></font>'
        '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/><family val="2"/></font>'
        '<font><b/><sz val="14"/><color rgb="FF17365D"/><name val="Calibri"/><family val="2"/></font>'
        '<font><b/><sz val="11"/><color rgb="FF17365D"/><name val="Calibri"/><family val="2"/></font>'
        '</fonts>'
        '<fills count="5">'
        '<fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FF17365D"/><bgColor indexed="64"/></patternFill></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFEAF4FB"/><bgColor indexed="64"/></patternFill></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFF8FAFC"/><bgColor indexed="64"/></patternFill></fill>'
        '</fills>'
        '<borders count="2">'
        '<border><left/><right/><top/><bottom/><diagonal/></border>'
        '<border><left style="thin"><color rgb="FFD8E2EC"/></left><right style="thin"><color rgb="FFD8E2EC"/></right>'
        '<top style="thin"><color rgb="FFD8E2EC"/></top><bottom style="thin"><color rgb="FFD8E2EC"/></bottom><diagonal/></border>'
        '</borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="6">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/>'
        '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
        '<xf numFmtId="0" fontId="3" fillId="3" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/>'
        '<xf numFmtId="2" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1"/>'
        '<xf numFmtId="0" fontId="0" fillId="4" borderId="1" xfId="0" applyFill="1" applyBorder="1"/>'
        '</cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        '<dxfs count="0"/>'
        '<tableStyles count="0" defaultTableStyle="TableStyleMedium2" defaultPivotStyle="PivotStyleLight16"/>'
        '</styleSheet>'
    )


def metric_block(title: str, metric_set: dict[str, Any]) -> list[list[Any]]:
    return [
        style_row([title, ""], 3),
        ["Število dni", metric_set.get("count")],
        ["MAE", metric_set.get("mae")],
        ["Bias", metric_set.get("bias")],
        ["RMSE", metric_set.get("rmse")],
        ["Znotraj ±3 SH", metric_set.get("within_3")],
        ["Znotraj ±5 SH", metric_set.get("within_5")],
        ["Znotraj ±10 SH", metric_set.get("within_10")],
        ["R2", metric_set.get("r2")],
        [],
    ]


def summary_sheet_rows(result: dict[str, Any], source_file_name: str, params: AnalysisParams) -> list[list[Any]]:
    rows: list[list[Any]] = [
        style_row(["KonfMaker model - povzetek", ""], 2),
        ["Vir", source_file_name],
        ["Testno leto", params.test_year],
        ["Leta za fit", ", ".join(str(year) for year in params.fit_years)],
        ["Način napovedi preletov", result["traffic_forecast"].get("mode")],
        ["Dni v napovedi", result["data_counts"].get("forecast_days")],
        ["Dni preverjanja", result["data_counts"].get("checked_days")],
        ["Dni z realnim ciljnim obdobjem", result["data_counts"].get("known_target_days")],
        ["Dni za fit", result["data_counts"].get("fit_days")],
        ["Planerski safety (%)", params.planning_safety_margin * 100],
        [],
        style_row(["Glavni rezultat", ""], 3),
        ["Dnevni MAE", result["operational_fit_metrics"].get("mae")],
        ["Dnevni bias", result["operational_fit_metrics"].get("bias")],
        ["Ure točno (%)", result["hourly_metrics"].get("exact_percent")],
        ["Ure v toleranci ±1 sektor (%)", result["hourly_metrics"].get("within_one_percent")],
        ["Analog MAE", result["analog_fit_metrics"].get("mae")],
        [],
        style_row(["Uporabljeni koeficienti", ""], 3),
        ["Intercept", result["used_coefficients"].get("intercept")],
        ["Koeficient na prelet", result["used_coefficients"].get("coefficient_per_flight")],
    ]
    for weekday in WEEKDAYS:
        rows.append([f"Dan {weekday}", result["used_coefficients"]["weekday_adjustments"].get(weekday, 0)])
    rows.extend([
        [],
        style_row(["Pragovi sektorjev", ""], 3),
    ])
    for sector in range(1, params.max_sectors + 1):
        rows.append([f"Meja {sector}S", result["used_coefficients"]["thresholds"].get(str(sector))])
    rows.extend([
        [],
        style_row(["Obremenitveni popravek", ""], 3),
        ["Vključen", "DA" if params.fatigue_enabled else "NE"],
        ["Lambda", params.fatigue_lambda],
        ["Max funkcija", "DA" if params.fatigue_apply_max else "NE"],
        ["Referenčno leto", params.reference_year],
        ["Ref. gostota delavnik", result["reference_density"].get("weekday")],
        ["Ref. gostota vikend", result["reference_density"].get("weekend")],
        ["Dovoljen dvig gostote (%)", params.allowed_density_increase * 100],
        ["Posebni dnevi", ", ".join(params.special_days)],
        ["Posebni dnevi buffer", params.special_day_buffer],
        ["Posebni dnevi izločeni iz fita", "DA" if params.special_day_exclude_from_fit else "NE"],
        [],
        style_row(["Formula", ""], 3),
        ["Prometni cilj", result["formula"].get("template")],
        ["Primer", result["formula"].get("example")],
    ])
    return rows


def opening_sheet_rows(result: dict[str, Any]) -> tuple[list[list[Any]], str | None]:
    hours = [f"{(7 + index) % 24:02d}:00" for index in range(24)]
    rows: list[list[Any]] = [
        style_row(["Datum", "Dan", "Preleti", "Napoved SH", "Hibrid SH", "Fatigue cilj", *hours], 1),
    ]
    for row_index, day in enumerate(result["forecast_days"], start=2):
        hour_start = excel_col_name(7)
        hour_end = excel_col_name(30)
        rows.append([
            day["date"],
            day["weekday"],
            day["flights"],
            xlsx_cell(day["predicted_sector_hours"], formula=f"SUM({hour_start}{row_index}:{hour_end}{row_index})"),
            day["hybrid_sector_hours"],
            day["fatigue_required_sector_hours"],
            *day["hourly_for_calculator"],
        ])
    if len(rows) <= 1:
        return rows, None
    return rows, f"G2:AD{len(rows)}"


def forecast_matrix_sheet_rows(result: dict[str, Any]) -> tuple[list[list[Any]], str | None, dict[int, float]]:
    days = result["forecast_days"]
    hours = [f"{(7 + index) % 24:02d}:00" for index in range(24)]
    rows: list[list[Any]] = [
        style_row(["Napoved odprtosti sektorjev"], 2),
        style_row(["Datum", *[day["date"] for day in days]], 1),
        style_row(["Dan", *[day["weekday"] for day in days]], 3),
        style_row(["Preleti", *[day["flights"] for day in days]], 3),
        style_row(["Prometni cilj", *[day["traffic_target"] for day in days]], 3),
        style_row(["SH", *[day["predicted_sector_hours"] for day in days]], 3),
    ]
    for hour_index, hour in enumerate(hours, start=6):
        rows.append([
            hour,
            *[
                day["hourly_for_calculator"][hour_index - 6]
                if hour_index - 6 < len(day.get("hourly_for_calculator", []))
                else None
                for day in days
            ],
        ])
    first_value_col = 2
    last_value_col = first_value_col + len(days) - 1
    value_start_row = 6
    value_end_row = value_start_row + len(hours) - 1
    cf_range = (
        f"{excel_col_name(first_value_col)}{value_start_row}:"
        f"{excel_col_name(last_value_col)}{value_end_row}"
        if days
        else None
    )
    widths = {1: 14}
    for col_index in range(first_value_col, last_value_col + 1):
        widths[col_index] = 11
    return rows, cf_range, widths


def theory_sheet_rows(result: dict[str, Any], params: AnalysisParams, mapping: AnalysisMapping) -> list[list[Any]]:
    rows: list[list[Any]] = [
        style_row(["Teorija modela", ""], 2),
        ["Namen", "Model napove odprtost sektorjev po urah za izbrano prihodnje obdobje."],
        ["Vhod 1", f"Odprtja sektorjev iz izbranega lista ({mapping.sector_sheet}): uporabljajo se zgodovinske ure po istem ISO tednu, dnevu in uri."],
        ["Vhod 2", f"Preleti iz izbranega lista ({mapping.traffic_sheet}): uporabljajo se za fit prometnega cilja in za napoved prometa."],
        [],
        style_row(["1. Prometni cilj T(d)", ""], 3),
        ["Formula", "T(d) = max(min SH, intercept + beta * preleti(d) + popravek_dneva + special_buffer(d))"],
        ["Pomen", "T(d) je ciljna dnevna vrednost sektorskih ur pred razporeditvijo po urah."],
        ["Primer", result["formula"].get("example")],
        ["Planerski safety", f"Končni dnevni seštevek se po izračunu dvigne za {params.planning_safety_margin:.1%} in dodatne sektorske ure se dodajo v najbolj obremenjene ure."],
        [],
        style_row(["2. Zgodovinski urni profil P", ""], 3),
        ["Formula", "P(w,d,h) = tehtano povprecje zgodovinskih odprtij za isti ISO teden, dan in uro."],
        ["Utezi", "Letne utezi dolocijo, koliko posamezno zgodovinsko leto vpliva na profil."],
        [],
        style_row(["3. Razteg profila", ""], 3),
        ["B", "B = vsota urnega profila P po dnevnih urah."],
        ["k", "k = (T(d) - nocni_dodatek) / B"],
        ["Z", "Z(h) = P(h) * k"],
        [],
        style_row(["4. Pretvorba v sektorje", ""], 3),
        ["Pragovi", "Z(h) se primerja s pragovi sektorjev: 1S, 2S, 3S, 4S, 5S."],
        ["Pravilo", "Finalni sektor v uri je stevilo pragov, ki jih Z preseze, omejeno z max sektorji."],
        ["Dnevni cap", "Ce vsota po pragovih preseze T(d), model zniza najmanj obremenjene ure do zgornje meje ceil(T(d))."],
        [],
        style_row(["5. Obremenitveni popravek", ""], 3),
        ["Vkljucen", "DA" if params.fatigue_enabled else "NE"],
        ["Lambda", params.fatigue_lambda],
        ["Referencno leto", params.reference_year],
        ["Dovoljen dvig gostote (%)", params.allowed_density_increase * 100],
        ["Pravilo", "Ce napoved preseze dovoljeno gostoto preletov na sektorsko uro, model dvigne zahtevane sektorske ure."],
        [],
        style_row(["6. Preverjanje napake", ""], 3),
        ["Dni za fit", result["data_counts"].get("fit_days")],
        ["Dni preverjanja", result["data_counts"].get("checked_days")],
        ["Dnevni MAE", result["operational_fit_metrics"].get("mae")],
        ["Dnevni bias", result["operational_fit_metrics"].get("bias")],
        ["Ure tocno (%)", result["hourly_metrics"].get("exact_percent")],
        ["Ure +/-1 sektor (%)", result["hourly_metrics"].get("within_one_percent")],
    ]
    return rows


def explain_day_sheet_rows(result: dict[str, Any], params: AnalysisParams) -> list[list[Any]]:
    threshold_headers = [f"Meja {sector}S" for sector in range(2, params.max_sectors + 1)]
    rows: list[list[Any]] = [
        style_row([
            "Datum",
            "Dan",
            "Poseben",
            "Preleti",
            "Intercept",
            "Koef/prelet",
            "Popravek dneva",
            "Special buffer",
            "Min SH",
            "T(d)",
            "B",
            "Noč",
            "k",
            "Hibrid SH",
            "Fatigue cilj",
            "Final SH",
            "Realno SH",
            "Napaka",
            *threshold_headers,
        ], 1),
    ]
    intercept = result["used_coefficients"].get("intercept")
    coefficient = result["used_coefficients"].get("coefficient_per_flight")
    weekday_adjustments = result["used_coefficients"].get("weekday_adjustments", {})
    for row_index, day in enumerate(result["forecast_days"], start=2):
        thresholds = day.get("thresholds", {})
        rows.append([
            day["date"],
            day["weekday"],
            "DA" if day.get("special_day") else "NE",
            day["flights"],
            intercept,
            coefficient,
            weekday_adjustments.get(day["weekday"], 0),
            day.get("special_day_buffer", 0),
            params.min_daily_sector_hours,
            xlsx_cell(day["traffic_target"], formula=f"MAX(I{row_index},E{row_index}+F{row_index}*D{row_index}+G{row_index}+H{row_index})"),
            day["base_profile_sum"],
            params.night_add,
            xlsx_cell(day["calibration_factor"], formula=f'IF(K{row_index}>0,(J{row_index}-L{row_index})/K{row_index},"")'),
            day["hybrid_sector_hours"],
            day["fatigue_required_sector_hours"],
            xlsx_cell(day["predicted_sector_hours"], formula=f"SUM(ODPRTOST!G{row_index}:AD{row_index})"),
            day["actual_sector_hours"],
            xlsx_cell(
                (
                    float(day["predicted_sector_hours"]) - float(day["actual_sector_hours"])
                    if day.get("actual_sector_hours") is not None
                    else None
                ),
                formula=f"P{row_index}-Q{row_index}",
            ),
            *[thresholds.get(str(sector)) for sector in range(2, params.max_sectors + 1)],
        ])
    return rows


def explain_hour_sheet_rows(result: dict[str, Any], params: AnalysisParams) -> list[list[Any]]:
    threshold_headers = [f"Meja {sector}S" for sector in range(2, params.max_sectors + 1)]
    hybrid_col = 9 + len(threshold_headers)
    final_col = hybrid_col + 1
    actual_col = final_col + 1
    error_col = actual_col + 1
    final_col_name = excel_col_name(final_col)
    actual_col_name = excel_col_name(actual_col)
    error_col_name = excel_col_name(error_col)
    rows: list[list[Any]] = [
        style_row([
            "Datum",
            "Dan",
            "Ura",
            "P profil",
            "T(d)",
            "B",
            "k",
            "Z",
            *threshold_headers,
            "Hibrid sektor",
            "Final sektor",
            "Realno sektor",
            "Napaka",
        ], 1),
    ]

    current_row = 2
    for day in result["forecast_days"]:
        thresholds = day.get("thresholds", {})
        for hour in day.get("explain_hours", []):
            z_formula = f"MAX(1,MIN({params.max_sectors},D{current_row}*G{current_row}))"
            rows.append([
                day["date"],
                day["weekday"],
                hour["hour"],
                hour["profile"],
                day["traffic_target"],
                day["base_profile_sum"],
                day["calibration_factor"],
                xlsx_cell(hour["z"], formula=z_formula),
                *[thresholds.get(str(sector)) for sector in range(2, params.max_sectors + 1)],
                hour["hybrid_sector"],
                hour["final_sector"],
                hour["actual_sector"],
                xlsx_cell(
                    (
                        float(hour["final_sector"]) - float(hour["actual_sector"])
                        if hour.get("actual_sector") is not None
                        else None
                    ),
                    formula=f'{final_col_name}{current_row}-{actual_col_name}{current_row}',
                ),
            ])
            current_row += 1
    return rows


def metrics_sheet_rows(result: dict[str, Any]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    rows.extend(metric_block("Dnevni operativni fit", result["operational_fit_metrics"]))
    rows.extend(metric_block("Prometni cilj", result["traffic_fit_metrics"]))
    rows.extend(metric_block("Analog backtest", result["analog_fit_metrics"]))
    rows.extend([
        style_row(["Po dnevih", "", "", "", "", "", ""], 3),
        style_row(["Dan", "Dni", "Preleti", "Realno SH", "Model SH", "Bias", "Preleti/SH"], 1),
    ])
    for item in result["weekday_summary"]:
        rows.append([
            item["weekday"],
            item["count"],
            item["avg_flights"],
            item["avg_actual"],
            item["avg_prediction"],
            item["bias"],
            item["actual_density"],
        ])
    rows.extend([
        [],
        style_row(["Po mesecih", "", "", "", "", ""], 3),
        style_row(["Mesec", "Dni", "Realno skupaj", "Model skupaj", "MAE", "Bias"], 1),
    ])
    for item in result["monthly_summary"]:
        rows.append([
            item["month"],
            item["count"],
            item["actual_total"],
            item["prediction_total"],
            item["mae"],
            item["bias"],
        ])
    rows.extend([
        [],
        style_row(["Po urah", "", "", "", "", ""], 3),
        style_row(["Ura", "Št. ur", "Točno %", "±1 sektor %", "MAE", "Bias"], 1),
    ])
    for item in result["hourly_summary"]:
        rows.append([
            item["hour"],
            item["count"],
            item["exact_percent"],
            item["within_one_percent"],
            item["mae"],
            item["bias"],
        ])
    return rows


def top_misses_sheet_rows(result: dict[str, Any]) -> list[list[Any]]:
    rows: list[list[Any]] = [
        style_row(["Datum", "Dan", "Preleti", "Realno SH", "Model SH", "Napaka", "Prometni cilj"], 1),
    ]
    for item in result["top_misses"]:
        rows.append([
            item["date"],
            item["weekday"],
            item["flights"],
            item["actual"],
            item["prediction"],
            item["error"],
            item["traffic_target"],
        ])
    return rows


def pattern_sheet_rows(result: dict[str, Any]) -> list[list[Any]]:
    hours = [f"{(7 + index) % 24:02d}:00" for index in range(24)]
    rows: list[list[Any]] = [
        style_row(["Rank", "Oznaka", "Dni", "SH", "Primeri datumov", *hours], 1),
    ]
    for item in result.get("pattern_suggestions", []):
        rows.append([
            item.get("rank"),
            item.get("label"),
            item.get("count"),
            item.get("sector_hours"),
            ", ".join(str(day) for day in item.get("dates", [])),
            *item.get("hourly_for_calculator", []),
        ])
    return rows


def build_analysis_workbook(
    result: dict[str, Any],
    source_file_name: str,
    params: AnalysisParams,
    mapping: AnalysisMapping,
) -> bytes:
    opening_rows, opening_cf_range = opening_sheet_rows(result)
    matrix_rows, matrix_cf_range, matrix_widths = forecast_matrix_sheet_rows(result)
    sheets = [
        ("POVZETEK", summary_sheet_rows(result, source_file_name, params), {1: 28, 2: 58}, []),
        ("NAPOVED_URNIK", matrix_rows, matrix_widths, [matrix_cf_range] if matrix_cf_range else []),
        ("ODPRTOST", opening_rows, {1: 13, 2: 8, 3: 11, 4: 12, 5: 12, 6: 13}, [opening_cf_range] if opening_cf_range else []),
        ("TEORIJA_MODELA", theory_sheet_rows(result, params, mapping), {1: 24, 2: 100}, []),
        ("EXPLAIN_DNEVI", explain_day_sheet_rows(result, params), {1: 13, 2: 8, 3: 11, 8: 12, 11: 10, 16: 10}, []),
        ("EXPLAIN_URE", explain_hour_sheet_rows(result, params), {1: 13, 2: 8, 3: 8, 4: 11, 8: 9}, []),
        ("METRIKE", metrics_sheet_rows(result), {1: 18, 2: 12, 3: 12, 4: 13, 5: 12, 6: 12, 7: 12}, []),
        ("TOP_MISSI", top_misses_sheet_rows(result), {1: 13, 2: 8, 3: 11, 4: 11, 5: 11, 6: 10, 7: 14}, []),
        ("VZORCI", pattern_sheet_rows(result), {1: 8, 2: 42, 3: 8, 4: 8, 5: 34}, []),
    ]

    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets>'
        + "".join(
            f'<sheet name="{escape(name)}" sheetId="{index}" r:id="rId{index}"/>'
            for index, (name, _, _, _) in enumerate(sheets, start=1)
        )
        + '</sheets>'
        + '<calcPr calcId="124519" fullCalcOnLoad="1" forceFullCalc="1"/>'
        + '</workbook>'
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(
            f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>'
            for index in range(1, len(sheets) + 1)
        )
        + f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        + '</Relationships>'
    )
    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        + "".join(
            f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for index in range(1, len(sheets) + 1)
        )
        + '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        + '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        + '</Types>'
    )
    root_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
        '</Relationships>'
    )
    created = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    core_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:dcmitype="http://purl.org/dc/dcmitype/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        '<dc:creator>KonfMaker</dc:creator>'
        '<cp:lastModifiedBy>KonfMaker</cp:lastModifiedBy>'
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{created}</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{created}</dcterms:modified>'
        '</cp:coreProperties>'
    )
    app_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        '<Application>KonfMaker</Application>'
        f'<HeadingPairs><vt:vector size="2" baseType="variant"><vt:variant><vt:lpstr>Worksheets</vt:lpstr></vt:variant><vt:variant><vt:i4>{len(sheets)}</vt:i4></vt:variant></vt:vector></HeadingPairs>'
        f'<TitlesOfParts><vt:vector size="{len(sheets)}" baseType="lpstr">'
        + "".join(f'<vt:lpstr>{escape(name)}</vt:lpstr>' for name, _, _, _ in sheets)
        + '</vt:vector></TitlesOfParts>'
        '</Properties>'
    )

    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types_xml)
        archive.writestr("_rels/.rels", root_rels_xml)
        archive.writestr("docProps/core.xml", core_xml)
        archive.writestr("docProps/app.xml", app_xml)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", rels_xml)
        archive.writestr("xl/styles.xml", workbook_styles_xml())
        for index, (_, rows, widths, conditional_ranges) in enumerate(sheets, start=1):
            archive.writestr(
                f"xl/worksheets/sheet{index}.xml",
                worksheet_xml(rows, widths=widths, conditional_ranges=conditional_ranges),
            )
    return output.getvalue()


def export_model_analysis(payload: WorkbookPayload) -> bytes:
    reader = XlsxReader(decode_workbook(payload))
    params = payload.params or model_params_from_workbook(reader)
    mapping = payload.mapping or AnalysisMapping()
    result = compute_analysis(
        reader=reader,
        mapping=mapping,
        params=params,
    )
    used_mapping = AnalysisMapping.model_validate(result["mapping"])
    return build_analysis_workbook(result, payload.file_name, params, used_mapping)

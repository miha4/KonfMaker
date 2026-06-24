from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config_library import (  # noqa: E402
    configuration_columns,
    evaluate_configuration,
    parse_configuration,
    read_configuration_csv,
    settings_for_configuration_evaluation,
)


def append_metric_row(rows: list[list[str]], label: str, values: dict[int, str | int | None]) -> None:
    column_count = max(len(row) for row in rows)
    row = [""] * column_count
    row[0] = label
    for column_index, value in values.items():
        if column_index < column_count:
            row[column_index] = "" if value is None else str(value)
    rows.append(row)


def main() -> int:
    parser = argparse.ArgumentParser(description="Enrich OKZP configuration CSV with calculated sector hours.")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--time-limit", type=int, default=10, help="CP-SAT time limit per configuration in seconds.")
    parser.add_argument("--max-configs", type=int, default=0, help="Optional limit for quick test runs.")
    args = parser.parse_args()

    rows = read_configuration_csv(args.input)
    settings = settings_for_configuration_evaluation(time_limit_seconds=args.time_limit)
    supported_shifts = {shift.code for shift in settings.shifts}

    hours: dict[int, str | int | None] = {}
    totals: dict[int, str | int | None] = {}
    fl_counts: dict[int, str | int | None] = {}
    aps_counts: dict[int, str | int | None] = {}
    acs_counts: dict[int, str | int | None] = {}
    waiting_counts: dict[int, str | int | None] = {}
    statuses: dict[int, str | int | None] = {}
    unsupported: dict[int, str | int | None] = {}
    seconds: dict[int, str | int | None] = {}

    columns = configuration_columns(rows)
    if args.max_configs > 0:
        columns = columns[: args.max_configs]
    for position, (column_index, name) in enumerate(columns, start=1):
        started = time.monotonic()
        configuration = parse_configuration(rows, column_index, name, supported_shifts)
        totals[column_index] = configuration.parsed_total
        fl_counts[column_index] = configuration.license_counts["FL"]
        aps_counts[column_index] = configuration.license_counts["APS"]
        acs_counts[column_index] = configuration.license_counts["ACS"]
        waiting_counts[column_index] = configuration.waiting_count
        unsupported[column_index] = ", ".join(configuration.unsupported_rows)

        if configuration.unsupported_rows:
            hours[column_index] = ""
            statuses[column_index] = "NEPODPRTE VRSTICE"
        elif configuration.parsed_total != configuration.total_without_waiting:
            hours[column_index] = ""
            statuses[column_index] = f"SKUPAJ-W {configuration.total_without_waiting}, PARSER {configuration.parsed_total}"
        else:
            calculated_hours = evaluate_configuration(configuration, settings)
            hours[column_index] = calculated_hours
            statuses[column_index] = "OK" if calculated_hours is not None else "NI IZRAČUNA"

        seconds[column_index] = f"{time.monotonic() - started:.2f}"
        print(f"{position}/{len(columns)} {name}: {statuses[column_index]} {hours[column_index]}", flush=True)

    append_metric_row(rows, "MODEL_MAX_SH", hours)
    append_metric_row(rows, "MODEL_TOTAL", totals)
    append_metric_row(rows, "MODEL_FL", fl_counts)
    append_metric_row(rows, "MODEL_APS", aps_counts)
    append_metric_row(rows, "MODEL_ACS", acs_counts)
    append_metric_row(rows, "MODEL_W", waiting_counts)
    append_metric_row(rows, "MODEL_STATUS", statuses)
    append_metric_row(rows, "MODEL_UNSUPPORTED_ROWS", unsupported)
    append_metric_row(rows, "MODEL_SECONDS", seconds)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerows(rows)

    print(f"Saved {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

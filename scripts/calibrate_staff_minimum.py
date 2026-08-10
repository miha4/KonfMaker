#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.calibration import run_staff_minimum_calibration  # noqa: E402
from app.config_library import FOCUS_CONFIGURATION_NAMES  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Primerja ročno število ljudi z minimalnim številom ljudi, ki ga najde ATCConfMaker.",
    )
    parser.add_argument(
        "--time-limit",
        type=int,
        default=8,
        help="CP-SAT časovni limit v sekundah za posamezen preverjeni limit ljudi.",
    )
    parser.add_argument(
        "--names",
        nargs="*",
        default=None,
        help="Seznam konfiguracij. Če ni podan, uporabi fokusni audit seznam.",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Izpiše samo zgoščeno tabelo rezultatov.",
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Pri compact izpisu obdela konfiguracije eno po eno in sproti izpisuje vrstice.",
    )
    return parser


def compact_lines(result: dict[str, object]) -> list[str]:
    lines: list[str] = []
    rows = result.get("rows")
    if not isinstance(rows, list):
        return lines
    for row in rows:
        if not isinstance(row, dict):
            continue
        variants = row.get("variants")
        if not isinstance(variants, list):
            lines.append(f"{row.get('name')}\t{row.get('status')}\t{row.get('message', '')}")
            continue
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            lines.append(
                "\t".join(
                    [
                        str(row.get("name")),
                        str(variant.get("variant")),
                        f"manual={row.get('manual_people')}",
                        f"model={variant.get('planned_people')}",
                        f"diff={variant.get('people_diff')}",
                        f"status={variant.get('status')}",
                        f"proof={variant.get('proof_status')}",
                        f"lb={variant.get('lower_bound')}",
                        f"limits={len(variant.get('limit_results') or [])}",
                        f"sec={variant.get('elapsed_seconds')}",
                    ]
                )
            )
    return lines


def main() -> int:
    args = build_parser().parse_args()
    if args.compact and args.stream:
        names = args.names or FOCUS_CONFIGURATION_NAMES
        print("name\tvariant\tmanual\tmodel\tdiff\tstatus\tproof\tlb\tlimits\tsec", flush=True)
        for name in names:
            result = run_staff_minimum_calibration([name], time_limit_seconds=args.time_limit)
            for line in compact_lines(result):
                print(line, flush=True)
        return 0

    result = run_staff_minimum_calibration(args.names, time_limit_seconds=args.time_limit)
    if args.compact:
        print("name\tvariant\tmanual\tmodel\tdiff\tstatus\tproof\tlb\tlimits\tsec")
        for line in compact_lines(result):
            print(line)
        return 0
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

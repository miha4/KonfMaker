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

from app.calibration import run_sector_profile_calibration  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Primerja ročne Excel konfiguracije s trenutnim ATCConfMaker modelom in predlaga sektor profile.",
    )
    parser.add_argument(
        "--time-limit",
        type=int,
        default=3,
        help="CP-SAT časovni limit v sekundah za posamezno konfiguracijo.",
    )
    parser.add_argument(
        "--names",
        nargs="*",
        default=None,
        help="Seznam konfiguracij. Če ni podan, uporabi fokusni audit seznam.",
    )
    parser.add_argument(
        "--no-solver",
        action="store_true",
        help="Izvede samo hitro profilno diagnostiko brez zagona CP-SAT solverja.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = run_sector_profile_calibration(
        args.names,
        time_limit_seconds=args.time_limit,
        run_solver=not args.no_solver,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

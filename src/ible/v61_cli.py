from __future__ import annotations

import argparse
import json
from pathlib import Path

from ible.v61_shadow import run_v61


def main() -> int:
    parser = argparse.ArgumentParser(description="Industry Boom V6.1 prospective policy ledger")
    parser.add_argument("--root", default=".")
    parser.add_argument("--output-dir", default="outputs/v61_prospective_policy")
    parser.add_argument("--v50-output-dir", default="outputs/v50_final_validator")
    parser.add_argument("--v60-output-dir", default="outputs/v60_champion_challenger")
    parser.add_argument("--run-date", default=None)
    args = parser.parse_args()
    summary = run_v61(
        Path(args.root).resolve(),
        Path(args.output_dir).resolve(),
        args.run_date or None,
        Path(args.v50_output_dir).resolve(),
        Path(args.v60_output_dir).resolve(),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

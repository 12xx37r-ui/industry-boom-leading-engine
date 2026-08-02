from __future__ import annotations

import argparse
import json
from pathlib import Path

from ible.v60_challenger import run_v60


def main() -> int:
    parser = argparse.ArgumentParser(description="Industry Boom V6.0 Champion-Challenger research comparison")
    parser.add_argument("--root", default=".")
    parser.add_argument("--output-dir", default="outputs/v60_champion_challenger")
    parser.add_argument("--v51-output-dir", default="outputs/v51_historical_audit")
    parser.add_argument("--run-date", default=None)
    args = parser.parse_args()
    summary = run_v60(
        Path(args.root).resolve(),
        Path(args.output_dir).resolve(),
        args.run_date or None,
        Path(args.v51_output_dir).resolve(),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

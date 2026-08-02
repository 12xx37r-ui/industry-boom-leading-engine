from __future__ import annotations

import argparse
import json
from pathlib import Path

from ible.v3_data_engine import run_v3_data


def main() -> int:
    parser = argparse.ArgumentParser(description="V3 GitHub live public-data phase-1 collector")
    parser.add_argument("--root", default=".")
    parser.add_argument("--output-dir", default="outputs/v3_data")
    parser.add_argument("--run-date", default=None)
    args = parser.parse_args()
    summary = run_v3_data(Path(args.root).resolve(), Path(args.output_dir).resolve(), args.run_date or None)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

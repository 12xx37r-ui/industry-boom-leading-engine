from __future__ import annotations

import argparse
import json
from pathlib import Path

from ible.v50_engine import run_v50


def main() -> int:
    parser = argparse.ArgumentParser(description="Industry Boom V5 prospective outcome validator")
    parser.add_argument("--root", default=".")
    parser.add_argument("--output-dir", default="outputs/v50_final_validator")
    parser.add_argument("--run-date", default=None)
    args = parser.parse_args()
    summary = run_v50(Path(args.root).resolve(), Path(args.output_dir).resolve(), args.run_date or None)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

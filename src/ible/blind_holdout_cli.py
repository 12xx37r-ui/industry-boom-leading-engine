from __future__ import annotations

import argparse
import json
from pathlib import Path

from ible.blind_holdout import run_blind_holdout


def main() -> int:
    parser = argparse.ArgumentParser(description="Run V1.1 sealed blind theme/date holdout")
    parser.add_argument("--root", default=".")
    parser.add_argument("--output-dir", default="outputs/v1_1_blind")
    args = parser.parse_args()
    summary = run_blind_holdout(Path(args.root).resolve(), Path(args.output_dir).resolve())
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

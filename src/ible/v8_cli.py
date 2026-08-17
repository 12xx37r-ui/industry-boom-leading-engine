from __future__ import annotations

import argparse
import json
from pathlib import Path

from ible.v8_layers import run_v8


def main() -> int:
    parser = argparse.ArgumentParser(description="Industry Boom Leading Engine V8 validation, proxy quality and discovery layers")
    parser.add_argument("--root", default=".")
    parser.add_argument("--output-dir", default="outputs/v8_layers")
    parser.add_argument("--v70-output-dir", default="outputs/v70_final_engine")
    parser.add_argument("--run-date", default=None)
    args = parser.parse_args()
    summary = run_v8(Path(args.root).resolve(), Path(args.output_dir).resolve(), args.run_date, Path(args.v70_output_dir).resolve())
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

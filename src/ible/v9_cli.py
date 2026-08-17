from __future__ import annotations

import argparse
import json
from pathlib import Path

from ible.v9_learning import run_v9


def main() -> int:
    parser = argparse.ArgumentParser(description="Industry Boom Leading Engine V9 evidence-gated learning layer")
    parser.add_argument("--root", default=".")
    parser.add_argument("--output-dir", default="outputs/v9_learning")
    parser.add_argument("--run-date", default=None)
    args = parser.parse_args()
    summary = run_v9(Path(args.root).resolve(), Path(args.output_dir).resolve(), args.run_date)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

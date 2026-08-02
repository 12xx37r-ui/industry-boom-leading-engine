from __future__ import annotations

import argparse
from pathlib import Path

from ible.global_validation import run_global_holdout


def main() -> None:
    parser = argparse.ArgumentParser(description="Run V0.8.3 exposure-gated global financial holdout")
    parser.add_argument("--root", default=".")
    parser.add_argument("--output-dir", default="outputs/global_holdout")
    args = parser.parse_args()
    summary = run_global_holdout(Path(args.root).resolve(), Path(args.output_dir))
    print(f"[GLOBAL] finished status={summary['status']} metrics={summary['metrics']}", flush=True)


if __name__ == "__main__":
    main()

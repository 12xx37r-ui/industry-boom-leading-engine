from __future__ import annotations

import argparse
from pathlib import Path

from ible.walkforward_validation import run_walkforward


def main() -> None:
    parser = argparse.ArgumentParser(description="Run frozen V0.9.1 independent walkforward validation")
    parser.add_argument("--root", default=".")
    parser.add_argument("--output-dir", default="outputs/walkforward_v100")
    args = parser.parse_args()
    run_walkforward(Path(args.root).resolve(), Path(args.output_dir).resolve())


if __name__ == "__main__":
    main()

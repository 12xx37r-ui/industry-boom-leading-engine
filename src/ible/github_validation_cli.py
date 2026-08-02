from __future__ import annotations

import argparse
import json
from pathlib import Path

from ible.github_validation import run_github_validation


def main() -> int:
    parser = argparse.ArgumentParser(description="Run GitHub-only locked historical walk-forward validation")
    parser.add_argument("--root", default=".")
    parser.add_argument("--output-dir", default="outputs/v1_github")
    args = parser.parse_args()
    summary = run_github_validation(Path(args.root).resolve(), Path(args.output_dir).resolve())
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

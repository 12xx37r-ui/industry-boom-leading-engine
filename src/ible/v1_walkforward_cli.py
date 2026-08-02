from __future__ import annotations

import argparse
from pathlib import Path

from ible.model_lock import ModelLockError
from ible.v1_walkforward import run_v1_walkforward


def main() -> None:
    parser = argparse.ArgumentParser(description="Run V1.0 zero-network independent walk-forward holdout")
    parser.add_argument("--root", default=".")
    parser.add_argument("--output-dir", default="outputs/v1_walkforward")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    try:
        summary = run_v1_walkforward(root, root / args.output_dir)
    except ModelLockError as exc:
        raise SystemExit(f"[V1-LOCK-ERROR] {exc}") from exc
    print(f"[V1] finished status={summary['status']} metrics={summary['metrics']}", flush=True)


if __name__ == "__main__":
    main()

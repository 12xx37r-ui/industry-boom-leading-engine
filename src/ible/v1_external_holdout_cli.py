from __future__ import annotations

import argparse
from pathlib import Path

from ible.model_lock import ModelLockError
from ible.v1_external_holdout import run_v1_external_holdout


def main() -> None:
    parser = argparse.ArgumentParser(description="Run V1.0 zero-network external walk-forward holdout")
    parser.add_argument("--root", default=".")
    parser.add_argument("--output-dir", default="outputs/v1_external")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    try:
        summary = run_v1_external_holdout(root, root / args.output_dir)
    except ModelLockError as exc:
        raise SystemExit(f"[V1-LOCK-ERROR] {exc}") from exc
    print(f"[V1-EXTERNAL] finished status={summary['status']} metrics={summary['metrics']}", flush=True)


if __name__ == "__main__":
    main()

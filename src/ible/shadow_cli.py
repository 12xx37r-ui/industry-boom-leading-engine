from __future__ import annotations

import argparse
import json
from pathlib import Path

from ible.shadow import ShadowError, run_shadow


def main() -> int:
    parser = argparse.ArgumentParser(description="V2.1 immutable shadow ledger with 50-theme universe")
    parser.add_argument("--root", default=".")
    parser.add_argument("--output-dir", default="outputs/shadow")
    parser.add_argument("--run-date", default=None)
    args = parser.parse_args()
    try:
        summary = run_shadow(Path(args.root).resolve(), Path(args.output_dir).resolve(), args.run_date)
    except ShadowError as exc:
        print(f"[V2-SHADOW-ERROR] {exc}")
        return 2
    print(json.dumps({
        "status": summary["status"],
        "history_action": summary["history_action"],
        "history_count": summary["history_count"],
        "forecast_eligible": summary["current_snapshot_forecast_eligible"],
        "universe_theme_count": summary["theme_universe"]["theme_count"],
        "scored_theme_count": summary["theme_universe"]["scored_theme_count"],
        "pending_theme_count": summary["theme_universe"]["pending_theme_count"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

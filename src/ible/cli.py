from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

from ible.pipeline import EnginePipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Industry Boom Leading Engine")
    parser.add_argument("--as-of", default=dt.date.today().isoformat(), help="Current scoring cutoff YYYY-MM-DD")
    parser.add_argument("--replay-as-of", default="2022-10-31", help="Historical replay cutoff or empty string")
    parser.add_argument("--include-dart", action="store_true", help="Deprecated: OpenDART is always the core source")
    parser.add_argument("--use-sec", action="store_true", help="Try SEC only as an optional supplement")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    pipeline = EnginePipeline(root)
    pipeline.run(
        current_as_of=args.as_of,
        replay_as_of=args.replay_as_of or None,
        include_dart=True,
        use_sec=args.use_sec,
    )


if __name__ == "__main__":
    main()

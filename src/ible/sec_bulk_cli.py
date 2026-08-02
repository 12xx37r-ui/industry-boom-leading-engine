from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from ible.collectors.sec_bulk import SecBulkClient
from ible.config import load_yaml


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare exposure-gated SEC Company Facts subset")
    parser.add_argument("--root", default=".")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--source", choices=("auto", "api", "bulk"), default=None)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    profiles = load_yaml(root / "config" / "theme_exposures.yml")
    cik_map = json.loads((root / "config" / "sec_cik_map.json").read_text(encoding="utf-8"))
    tickers = [
        company["ticker"]
        for theme in profiles.get("themes", [])
        for company in theme.get("us_companies", [])
        if float(company.get("exposure", 0)) >= float(profiles.get("minimum_exposure", 0.30))
    ]
    client = SecBulkClient(
        root / ".cache" / "sec_bulk",
        os.getenv("SEC_USER_AGENT", ""),
    )
    result = client.prepare_subset(
        cik_map,
        tickers,
        force_archive=args.force,
        source_mode=args.source,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

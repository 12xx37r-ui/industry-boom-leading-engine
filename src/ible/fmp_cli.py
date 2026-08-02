from __future__ import annotations

import argparse
import os
from pathlib import Path

from ible.collectors.fmp import FmpClient
from ible.config import load_yaml


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare FMP quarterly statement cache")
    parser.add_argument("--root", default=".")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    config = load_yaml(root / "config" / "global_holdouts.yml")
    exposures = load_yaml(root / "config" / "theme_exposures.yml")
    minimum = float(exposures.get("minimum_exposure", 0.30))
    themes = {row["id"]: row for row in exposures.get("themes", [])}
    tickers = [
        company["ticker"]
        for theme_id in config["cohort"]["theme_ids"]
        for company in themes[theme_id].get("us_companies", [])
        if float(company.get("exposure", 0)) >= minimum
    ]
    client = FmpClient(root / ".cache" / "fmp", os.getenv("FMP_API_KEY", ""))
    result = client.prepare_subset(tickers, force=args.force)
    print(result)


if __name__ == "__main__":
    main()

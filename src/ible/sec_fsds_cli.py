from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from ible.collectors.sec_fsds import SecFsdsClient
from ible.config import load_yaml


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare point-in-time SEC Financial Statement Data Set seed")
    parser.add_argument("--root", default=".")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    exposure = load_yaml(root / "config" / "theme_exposures.yml")
    holdout = load_yaml(root / "config" / "global_holdouts.yml")
    minimum = float(exposure.get("minimum_exposure", 0.30))
    cohort_ids = set(holdout["cohort"]["theme_ids"])
    tickers = sorted(
        {
            str(company["ticker"]).upper()
            for theme in exposure.get("themes", [])
            if theme.get("id") in cohort_ids
            for company in theme.get("us_companies", [])
            if float(company.get("exposure", 0)) >= minimum
        }
    )
    cik_map = json.loads((root / "config" / "sec_cik_map.json").read_text(encoding="utf-8"))
    client = SecFsdsClient(root / ".cache" / "sec_fsds", os.getenv("SEC_USER_AGENT", ""))
    status = client.prepare_seed(
        cik_map,
        tickers,
        str(holdout["cohort"]["as_of"]),
        root / "validation_seed" / "sec_fsds_fy2021.json",
        force=args.force,
    )
    print(json.dumps(status, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

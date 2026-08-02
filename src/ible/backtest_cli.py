from __future__ import annotations

import argparse
from pathlib import Path

from ible.backtest import evaluate_scenario, load_scenarios
from ible.pipeline import EnginePipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one historical industry validation scenario")
    parser.add_argument("--scenario-id", required=True)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    scenarios = load_scenarios(root)
    if args.scenario_id not in scenarios:
        raise SystemExit(f"unknown scenario: {args.scenario_id}")
    scenario = scenarios[args.scenario_id]
    pipeline = EnginePipeline(root)
    print(
        f"[BACKTEST] scenario={scenario['id']} label={scenario['label']} "
        f"as_of={scenario['as_of']} cohort={len(scenario.get('comparison_theme_ids', []))}",
        flush=True,
    )
    ranking, metadata = pipeline.score_dart_as_of(
        str(scenario["as_of"]), list(scenario.get("comparison_theme_ids") or [])
    )
    result = evaluate_scenario(scenario, ranking)
    result["metadata"] = metadata
    output = Path(args.output) if args.output else root / "outputs" / "backtests" / f"{scenario['id']}.json"
    pipeline._write_json(output, result)
    print(
        f"[BACKTEST] finished scenario={scenario['id']} status={result['status']} "
        f"rank={result.get('observed', {}).get('rank')} score={result.get('observed', {}).get('boom_score')}",
        flush=True,
    )


if __name__ == "__main__":
    main()

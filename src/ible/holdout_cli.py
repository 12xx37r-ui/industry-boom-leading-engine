from __future__ import annotations

import argparse
from pathlib import Path

from ible.holdout import (
    aggregate_holdout_results,
    evaluate_holdout_scenarios,
    load_holdout_config,
    verify_model_lock,
)
from ible.pipeline import EnginePipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Run frozen-model holdout validation")
    parser.add_argument("--output-dir", default="outputs/holdout")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    output_dir = Path(args.output_dir)
    config = load_holdout_config(root)
    lock = verify_model_lock(root)
    if not lock["matches"]:
        raise SystemExit(f"MODEL_LOCK_MISMATCH: {lock}")

    cohort = config["cohort"]
    as_of = str(cohort["as_of"])
    theme_ids = list(cohort["theme_ids"])
    print(
        f"[HOLDOUT] frozen_model={lock.get('frozen_model_version')} as_of={as_of} "
        f"themes={len(theme_ids)} scenarios={len(config['scenarios'])}",
        flush=True,
    )

    pipeline = EnginePipeline(root)
    ranking, metadata = pipeline.score_dart_as_of(as_of, theme_ids)
    results = evaluate_holdout_scenarios(config, ranking)
    summary = aggregate_holdout_results(results, lock)

    snapshot = {
        "benchmark_name": config.get("benchmark_name"),
        "as_of": as_of,
        "theme_ids": theme_ids,
        "ranking": ranking,
        "metadata": metadata,
        "model_lock": lock,
    }
    scenario_table = [
        {
            "scenario_id": row.get("scenario_id"),
            "name": row.get("scenario_name"),
            "label": row.get("label"),
            "status": row.get("status"),
            "passed": row.get("passed"),
            "alert_triggered": row.get("alert_triggered"),
            **dict(row.get("observed") or {}),
        }
        for row in results
    ]
    validation = {
        "status": summary["status"],
        "investment_use_allowed": False,
        "reason": summary["reason"],
        "stage3_metrics": summary["metrics"],
        "stage3_criteria": summary["criteria"],
        "model_lock": lock,
        "next_requirements": [
            "미국 원천 CAPEX·수주·정부계약 직접 편입",
            "실물신호 대비 주가·뉴스 시장 미반영도 계산",
            "복수 시점의 완전 신규 홀드아웃 검증",
            "거래비용·최대낙폭·포지션 크기를 포함한 투자 백테스트",
        ],
    }

    pipeline._write_json(output_dir / "holdout_snapshot.json", snapshot)
    pipeline._write_json(output_dir / "holdout_scenarios.json", {"scenarios": results})
    pipeline._write_json(
        output_dir / "holdout_summary.json",
        {
            "status": summary["status"],
            "investment_use_allowed": False,
            "metrics": summary["metrics"],
            "criteria": summary["criteria"],
            "scenario_table": scenario_table,
        },
    )
    pipeline._write_json(output_dir / "model_validation_stage3.json", validation)
    pipeline._write_json(output_dir / "model_lock_verification.json", lock)
    print(f"[HOLDOUT] finished status={summary['status']} metrics={summary['metrics']}", flush=True)


if __name__ == "__main__":
    main()

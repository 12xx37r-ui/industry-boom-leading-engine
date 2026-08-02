from __future__ import annotations

import argparse
import json
from pathlib import Path

from ible.backtest import aggregate_results
from ible.pipeline import EnginePipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregate historical validation scenarios")
    parser.add_argument("--input-dir", default="outputs/backtests")
    parser.add_argument("--output-dir", default="outputs")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    results = []
    for path in sorted(input_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("scenario_id"):
            results.append(payload)
    if not results:
        raise SystemExit(f"no scenario results found in {input_dir}")

    summary = aggregate_results(results)
    EnginePipeline._write_json(output_dir / "walkforward_backtest.json", summary)
    EnginePipeline._write_json(
        output_dir / "backtest_summary.json",
        {
            "status": summary["status"],
            "investment_use_allowed": False,
            "metrics": summary["metrics"],
            "criteria": summary["criteria"],
            "scenario_table": [
                {
                    "scenario_id": row.get("scenario_id"),
                    "name": row.get("scenario_name"),
                    "label": row.get("label"),
                    "status": row.get("status"),
                    "alert_triggered": row.get("alert_triggered"),
                    **dict(row.get("observed") or {}),
                }
                for row in results
            ],
        },
    )
    EnginePipeline._write_json(
        output_dir / "model_validation.json",
        {
            "status": summary["status"],
            "investment_use_allowed": False,
            "reason": summary["reason"],
            "stage2_metrics": summary["metrics"],
            "stage2_criteria": summary["criteria"],
            "next_requirements": [
                "미국 빅테크·산업 원천 CAPEX 직접 편입",
                "실물신호 대비 주가·뉴스 선반영도 검증",
                "보유기간·거래비용·최대낙폭을 포함한 포트폴리오 백테스트",
                "사전 정의되지 않은 신규 산업 자동발견",
            ],
        },
    )
    print(
        f"[BACKTEST] aggregate status={summary['status']} metrics={summary['metrics']}",
        flush=True,
    )


if __name__ == "__main__":
    main()

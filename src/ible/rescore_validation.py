from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ible.analytics.scoring import build_dart_theme_result
from ible.backtest import evaluate_scenario, load_scenarios
from ible.models import Signal
from ible.pipeline import EnginePipeline


def _signal_from_dict(payload: dict[str, Any]) -> Signal:
    allowed = {
        "name", "score", "level", "velocity", "acceleration",
        "persistence", "breadth", "coverage", "raw", "warnings",
    }
    return Signal(**{key: payload[key] for key in allowed if key in payload})


def _rescore_theme(row: dict[str, Any]) -> dict[str, Any]:
    engines = row.get("engines") or {}
    required = {
        "capital_events", "capital_amounts", "supply_contracts",
        "cashflow_capex", "contract_amounts", "revenue",
        "operating_margin", "research_momentum",
    }
    missing = required - set(engines)
    if missing:
        raise ValueError(f"theme {row.get('theme_id')} missing engines: {sorted(missing)}")
    coverage = row.get("coverage") or {}
    result = build_dart_theme_result(
        theme_id=str(row["theme_id"]),
        theme_name=str(row.get("theme_name") or row["theme_id"]),
        as_of=str(row.get("as_of") or ""),
        signals={key: _signal_from_dict(value) for key, value in engines.items()},
        requested_companies=int(coverage.get("requested_companies") or 0),
        usable_companies=int(coverage.get("usable_companies") or 0),
        invalidations=list(row.get("invalidations") or []),
    )
    return result.to_dict()


def rescore_directory(root: Path, input_dir: Path, output_dir: Path) -> list[dict[str, Any]]:
    scenarios = load_scenarios(root)
    output_backtests = output_dir / "backtests"
    output_backtests.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []

    for path in sorted(input_dir.glob("*.json")):
        old = json.loads(path.read_text(encoding="utf-8"))
        scenario_id = str(old.get("scenario_id") or "")
        if not scenario_id or scenario_id not in scenarios:
            continue
        ranking = [_rescore_theme(row) for row in old.get("ranking") or []]
        ranking.sort(key=lambda row: (row["boom_score"], row["data_confidence"]), reverse=True)
        result = evaluate_scenario(scenarios[scenario_id], ranking)
        result["metadata"] = {
            **dict(old.get("metadata") or {}),
            "rescored_from_existing_signals": True,
            "rescoring_engine_version": "0.6.0",
        }
        EnginePipeline._write_json(output_backtests / f"{scenario_id}.json", result)
        results.append(result)
    if not results:
        raise RuntimeError(f"no scenario JSON files found in {input_dir}")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Rescore prior validation artifacts without API recollection")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-dir", default="outputs/rescored_validation")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    results = rescore_directory(root, input_dir, output_dir)

    # Reuse the normal aggregate writer through a subprocess-free import.
    from ible.backtest import aggregate_results
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
            "rescored_from_existing_signals": True,
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
            "rescored_from_existing_signals": True,
            "warning": "기존 검증자료로 구조를 보정한 인샘플 재평가이며 홀드아웃 검증 전 투자에 사용할 수 없습니다.",
            "next_requirements": [
                "새로운 홀드아웃 산업·시점 검증",
                "미국 빅테크·산업 원천 CAPEX 직접 편입",
                "실물신호 대비 주가·뉴스 선반영도 검증",
                "보유기간·거래비용·최대낙폭을 포함한 포트폴리오 백테스트",
                "사전 정의되지 않은 신규 산업 자동발견",
            ],
        },
    )
    print(f"[RESCORE] scenarios={len(results)} status={summary['status']} metrics={summary['metrics']}", flush=True)


if __name__ == "__main__":
    main()

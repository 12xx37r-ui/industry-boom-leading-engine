from __future__ import annotations

import datetime as dt
import hashlib
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from ible.backtest import evaluate_scenario
from ible.collectors.sec_fsds import SecFsdsClient
from ible.config import load_yaml
from ible.global_validation import _theme_result

VERSION = "1.0.0"
FROZEN_MODEL_VERSION = "0.9.1"


def _verify_model_lock(root: Path) -> dict[str, Any]:
    path = root / "config" / "model_lock_v100.json"
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"model lock unavailable: {exc}") from exc
    if lock.get("frozen_model_version") != FROZEN_MODEL_VERSION:
        raise RuntimeError("model lock version mismatch")
    mismatches: list[dict[str, str]] = []
    actual: dict[str, str] = {}
    for relative, expected in (lock.get("files") or {}).items():
        file_path = root / str(relative)
        digest = hashlib.sha256(file_path.read_bytes()).hexdigest() if file_path.exists() else "MISSING"
        actual[str(relative)] = digest
        if digest != expected:
            mismatches.append({"file": str(relative), "expected": str(expected), "actual": digest})
    if mismatches:
        raise RuntimeError("MODEL_LOCK_MISMATCH: " + json.dumps(mismatches, ensure_ascii=False))
    return {"passed": True, "frozen_model_version": FROZEN_MODEL_VERSION, "files": actual}


def _load_seed_research(path: Path) -> tuple[dict[str, Any], dict[str, str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {}, {"_seed": str(exc)}
    research = {
        str(key): value
        for key, value in (payload.get("research") or {}).items()
        if isinstance(value, dict)
    }
    status = payload.get("status") or {}
    errors = {str(k): str(v) for k, v in (status.get("research_errors") or {}).items()}
    return research, errors


def _months_between(start: str, end: str) -> float:
    a = dt.date.fromisoformat(start)
    b = dt.date.fromisoformat(end)
    return round((b - a).days / 30.4375, 2)


def _pairwise_auc(positives: list[dict[str, Any]], negatives: list[dict[str, Any]]) -> float | None:
    values: list[float] = []
    for positive in positives:
        for negative in negatives:
            p = float((positive.get("observed") or {}).get("boom_score") or 0.0)
            n = float((negative.get("observed") or {}).get("boom_score") or 0.0)
            values.append(1.0 if p > n else 0.5 if p == n else 0.0)
    return statistics.mean(values) if values else None


def _metrics(scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [row for row in scenarios if row.get("status") != "INSUFFICIENT_DATA"]
    positives = [row for row in eligible if row.get("label") == "positive"]
    negatives = [row for row in eligible if row.get("label") == "negative"]
    recall = sum(bool(row.get("passed")) for row in positives) / len(positives) if positives else None
    false_alarm = (
        sum(bool(row.get("alert_triggered")) for row in negatives) / len(negatives)
        if negatives
        else None
    )
    auc = _pairwise_auc(positives, negatives)
    return {
        "scenario_count": len(scenarios),
        "eligible_scenarios": len(eligible),
        "positive_count": len(positives),
        "negative_count": len(negatives),
        "positive_recall": round(recall, 4) if recall is not None else None,
        "false_alarm_rate": round(false_alarm, 4) if false_alarm is not None else None,
        "pairwise_auc": round(auc, 4) if auc is not None else None,
    }


def _persistence(scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    by_theme: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in scenarios:
        if row.get("status") == "INSUFFICIENT_DATA":
            continue
        by_theme[str(row.get("target_theme_id"))].append(row)

    positive_themes = []
    negative_themes = []
    for theme_id, rows in by_theme.items():
        rows.sort(key=lambda x: str(x.get("as_of") or ""))
        if len(rows) < 2:
            continue
        label = str(rows[0].get("label") or "")
        if label == "positive":
            positive_themes.append(
                {
                    "theme_id": theme_id,
                    "observations": len(rows),
                    "alerts": sum(bool(row.get("alert_triggered")) for row in rows),
                    "persistent": all(bool(row.get("alert_triggered")) for row in rows),
                }
            )
        elif label == "negative":
            negative_themes.append(
                {
                    "theme_id": theme_id,
                    "observations": len(rows),
                    "false_alerts": sum(bool(row.get("alert_triggered")) for row in rows),
                    "stable_rejection": all(not bool(row.get("alert_triggered")) for row in rows),
                }
            )
    positive_rate = (
        sum(bool(row["persistent"]) for row in positive_themes) / len(positive_themes)
        if positive_themes
        else None
    )
    negative_rate = (
        sum(bool(row["stable_rejection"]) for row in negative_themes) / len(negative_themes)
        if negative_themes
        else None
    )
    return {
        "positive_theme_count": len(positive_themes),
        "negative_theme_count": len(negative_themes),
        "positive_persistence_rate": round(positive_rate, 4) if positive_rate is not None else None,
        "negative_rejection_stability": round(negative_rate, 4) if negative_rate is not None else None,
        "positive_themes": positive_themes,
        "negative_themes": negative_themes,
    }


def run_walkforward(root: Path, output_dir: Path) -> dict[str, Any]:
    model_lock = _verify_model_lock(root)
    holdouts = load_yaml(root / "config" / "walkforward_holdouts.yml")
    theme_config = load_yaml(root / "config" / "walkforward_themes.yml")
    minimum_exposure = float(theme_config.get("minimum_exposure", 0.30))
    themes_by_id = {str(row["id"]): row for row in theme_config.get("themes", [])}
    all_scenarios: list[dict[str, Any]] = []
    snapshots_out: list[dict[str, Any]] = []

    for snapshot in holdouts.get("snapshots", []):
        snapshot_id = str(snapshot["id"])
        as_of = str(snapshot["as_of"])
        seed_path = root / str(snapshot["seed_file"])
        theme_ids = [str(value) for value in snapshot.get("theme_ids") or []]
        themes = [themes_by_id[theme_id] for theme_id in theme_ids]
        tickers = sorted(
            {
                str(company["ticker"]).upper()
                for theme in themes
                for company in theme.get("us_companies", [])
                if float(company.get("exposure", 0)) >= minimum_exposure
            }
        )
        client = SecFsdsClient(root / ".cache" / "walkforward" / snapshot_id, "offline-seed@example.invalid")
        all_series, financial_status, financial_errors = client.load_seed(seed_path, tickers)
        historically_eligible = set(financial_status.get("historically_eligible") or [])
        research, research_errors = _load_seed_research(seed_path)
        required_research = set(theme_ids)
        research_available = len(required_research & set(research))
        dataset_gate = bool(
            financial_status.get("status") == "READY"
            and float(financial_status.get("coverage_of_historically_eligible") or 0) >= 0.68
            and int(financial_status.get("available") or 0) >= 18
            and research_available >= max(1, len(required_research) - 1)
        )
        print(
            f"[WALKFORWARD] snapshot={snapshot_id} cutoff={as_of} gate={dataset_gate} "
            f"financial={financial_status.get('available', 0)} research={research_available}/{len(required_research)}",
            flush=True,
        )

        ranking = [
            _theme_result(
                theme,
                all_series,
                research.get(theme["id"]),
                as_of,
                minimum_exposure,
                historically_eligible,
            )
            for theme in themes
        ]
        ranking.sort(key=lambda row: (row["boom_score"], row["data_confidence"]), reverse=True)

        snapshot_scenarios: list[dict[str, Any]] = []
        for raw in snapshot.get("scenarios", []):
            scenario = dict(raw)
            scenario["as_of"] = as_of
            scenario["snapshot_id"] = snapshot_id
            scenario["comparison_theme_ids"] = theme_ids
            evaluated = evaluate_scenario(scenario, ranking)
            evaluated["snapshot_id"] = snapshot_id
            expected = scenario.get("expected_boom_start")
            if expected:
                evaluated["expected_boom_start"] = expected
                evaluated["lead_months_if_alerted"] = _months_between(as_of, str(expected)) if evaluated.get("alert_triggered") else None
            if not dataset_gate:
                evaluated["status"] = "INSUFFICIENT_DATA"
                evaluated["passed"] = False
                evaluated["alert_triggered"] = False
                evaluated["reason"] = "walkforward seed completeness gate failed"
            snapshot_scenarios.append(evaluated)
            all_scenarios.append(evaluated)

        snapshot_metrics = _metrics(snapshot_scenarios)
        snapshots_out.append(
            {
                "snapshot_id": snapshot_id,
                "as_of": as_of,
                "seed_file": str(snapshot["seed_file"]),
                "dataset_gate_passed": dataset_gate,
                "financial_status": financial_status,
                "financial_errors": financial_errors,
                "research_errors": research_errors,
                "ranking": ranking,
                "scenarios": snapshot_scenarios,
                "metrics": snapshot_metrics,
            }
        )

    overall = _metrics(all_scenarios)
    persistence = _persistence(all_scenarios)
    criteria = dict(holdouts.get("criteria") or {})
    passed = bool(
        model_lock.get("passed")
        and int(overall.get("eligible_scenarios") or 0) >= int(criteria.get("minimum_eligible_scenarios", 9))
        and float(overall.get("positive_recall") or 0) >= float(criteria.get("positive_recall_min", 0.67))
        and float(overall.get("false_alarm_rate") if overall.get("false_alarm_rate") is not None else 1.0)
        <= float(criteria.get("false_alarm_rate_max", 0.33))
        and float(overall.get("pairwise_auc") or 0) >= float(criteria.get("pairwise_auc_min", 0.70))
        and float(persistence.get("positive_persistence_rate") or 0)
        >= float(criteria.get("positive_persistence_min", 0.67))
    )
    positive_leads = [
        float(row["lead_months_if_alerted"])
        for row in all_scenarios
        if row.get("label") == "positive" and row.get("lead_months_if_alerted") is not None
    ]
    summary = {
        "status": "PASSED_V1_INDEPENDENT_WALKFORWARD" if passed else "FAILED_V1_INDEPENDENT_WALKFORWARD",
        "investment_use_allowed": False,
        "model_version": FROZEN_MODEL_VERSION,
        "validation_engine_version": VERSION,
        "validation_role": "independent_walkforward_holdout",
        "model_lock": model_lock,
        "criteria": criteria,
        "metrics": {
            **overall,
            "positive_persistence_rate": persistence.get("positive_persistence_rate"),
            "negative_rejection_stability": persistence.get("negative_rejection_stability"),
            "median_lead_months_for_triggered_positives": round(statistics.median(positive_leads), 2) if positive_leads else None,
        },
        "persistence": persistence,
        "snapshots": snapshots_out,
        "scenarios": all_scenarios,
        "known_limitations": [
            "V0.9.1 점수파일을 SHA-256으로 동결한 뒤 새로운 2019년 시점과 산업군에 적용합니다.",
            "두 관측시점은 독립 파일이지만 일부 SEC 분기 원자료를 공유하므로 완전히 독립적인 표본은 아닙니다.",
            "산업 성공·실패 라벨은 연구용이며 투자수익률·거래비용·시장 선반영도 검증은 포함하지 않습니다.",
            "통과해도 최소 한 개의 추가 시대 코호트와 실시간 전진검증 전에는 투자 사용을 허용하지 않습니다.",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "walkforward_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "walkforward_scenarios.json").write_text(json.dumps(all_scenarios, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "walkforward_snapshots.json").write_text(json.dumps(snapshots_out, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary

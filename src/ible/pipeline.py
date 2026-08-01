from __future__ import annotations

import datetime as dt
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from ible.analytics.dart_metrics import (
    REPORTS,
    build_quarterly_financial_series,
    event_series,
    report_available,
)
from ible.analytics.scoring import (
    build_dart_theme_result,
    build_event_signal,
    build_margin_signal,
    build_metric_signal,
    build_theme_result,
)
from ible.analytics.sec_metrics import FLOW_TAGS, quarterly_flow
from ible.collectors.bea import BeaClient
from ible.collectors.fred import FredClient
from ible.collectors.opendart import OpenDartClient
from ible.collectors.sec import SecClient
from ible.config import load_yaml
from ible.http import JsonHttpClient, redact_text


class EnginePipeline:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.config = load_yaml(root / "config" / "themes.yml")
        user_agent = os.getenv("SEC_USER_AGENT", "Industry Boom Leading Engine contact@example.com").strip()
        self.http = JsonHttpClient(
            user_agent=user_agent,
            timeout=15,
            min_interval=0.28,
            retries=1,
            cache_dir=root / ".cache",
        )
        self.sec = SecClient(self.http, root / "config" / "sec_cik_map.json")
        self.fred = FredClient(os.getenv("FRED_API_KEY", ""), self.http) if os.getenv("FRED_API_KEY") else None
        self.bea = BeaClient(os.getenv("BEA_API_KEY", ""), self.http) if os.getenv("BEA_API_KEY") else None
        self.dart = OpenDartClient(os.getenv("OPENDART_API_KEY", ""), self.http) if os.getenv("OPENDART_API_KEY") else None

    @staticmethod
    def _sanitize_payload(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: EnginePipeline._sanitize_payload(v) for k, v in value.items()}
        if isinstance(value, list):
            return [EnginePipeline._sanitize_payload(v) for v in value]
        if isinstance(value, tuple):
            return [EnginePipeline._sanitize_payload(v) for v in value]
        if isinstance(value, str):
            return redact_text(value)
        return value

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        safe_payload = EnginePipeline._sanitize_payload(payload)
        path.write_text(json.dumps(safe_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _fetch_companyfacts_optional(self, enabled: bool) -> tuple[dict[str, dict[str, Any]], dict[str, str], str]:
        if not enabled:
            return {}, {}, "SKIPPED"
        requested = sorted({ticker for theme in self.config["themes"] for ticker in theme.get("us_tickers", [])})
        facts: dict[str, dict[str, Any]] = {}
        errors: dict[str, str] = {}
        print(f"[SEC] optional probe/collection for {len(requested)} companies", flush=True)
        for ticker in requested:
            try:
                facts[ticker] = self.sec.companyfacts(ticker)
            except Exception as exc:
                errors[ticker] = str(exc)
                if "403 Client Error" in str(exc) and len(errors) >= 2 and not facts:
                    print("[SEC] GitHub-hosted runner is blocked with HTTP 403; continuing with OpenDART core.", flush=True)
                    return {}, errors, "BLOCKED_403"
            if len(facts) + len(errors) >= 8 and not facts:
                return {}, errors, "UNAVAILABLE"
        return facts, errors, "CONNECTED" if facts else "UNAVAILABLE"

    def score_sec_as_of(self, as_of: str, facts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        results = []
        for theme in self.config["themes"]:
            ticker_list = theme.get("us_tickers", [])
            metric_series: dict[str, dict[str, list[tuple[str, float]]]] = {
                metric: {} for metric in ("capex", "rd", "revenue", "gross_profit")
            }
            usable = set()
            for ticker in ticker_list:
                companyfacts = facts.get(ticker)
                if not companyfacts:
                    for metric in metric_series:
                        metric_series[metric][ticker] = []
                    continue
                for metric in metric_series:
                    _, series = quarterly_flow(companyfacts, FLOW_TAGS[metric], as_of)
                    metric_series[metric][ticker] = series
                    if metric in {"capex", "rd", "revenue"} and len(series) >= 5:
                        usable.add(ticker)
            signals = {
                "capex": build_metric_signal("capital_expenditure", metric_series["capex"]),
                "rd": build_metric_signal("research_and_development", metric_series["rd"]),
                "revenue": build_metric_signal("revenue_demand", metric_series["revenue"]),
                "margin": build_margin_signal(metric_series["revenue"], metric_series["gross_profit"]),
            }
            result = build_theme_result(
                theme_id=theme["id"],
                theme_name=theme["name"],
                as_of=as_of,
                signals=signals,
                requested_companies=len(ticker_list),
                usable_companies=len(usable),
                invalidations=theme.get("invalidations", []),
            )
            results.append(result.to_dict())
        return sorted(results, key=lambda x: (x["boom_score"], x["data_confidence"]), reverse=True)

    def _dart_disclosures(self, as_of: dt.date) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
        if not self.dart:
            raise RuntimeError("OPENDART_API_KEY is required for the GitHub-compatible core engine")
        stock_codes = sorted({code for theme in self.config["themes"] for code in theme.get("kr_stock_codes", [])})
        begin = as_of - dt.timedelta(days=8 * 91 + 14)
        collected: dict[str, list[dict[str, Any]]] = {}
        errors: dict[str, str] = {}
        print(f"[DART] disclosures {begin}~{as_of}, companies={len(stock_codes)}", flush=True)

        def fetch(code: str) -> list[dict[str, Any]]:
            return self.dart.disclosures(code, begin.strftime("%Y%m%d"), as_of.strftime("%Y%m%d"), 100)

        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(fetch, code): code for code in stock_codes}
            for future in as_completed(futures):
                code = futures[future]
                try:
                    collected[code] = future.result()
                except Exception as exc:
                    errors[code] = str(exc)
                done = len(collected) + len(errors)
                if done == len(stock_codes) or done % 8 == 0:
                    print(f"[DART] disclosure progress {done}/{len(stock_codes)} errors={len(errors)}", flush=True)
        print(f"[DART] disclosure collection finished in {time.monotonic() - started:.1f}s", flush=True)
        return collected, errors

    def _dart_financials(self, as_of: dt.date) -> tuple[dict[str, list[tuple[str, float]]], dict[str, list[tuple[str, float]]], dict[str, str]]:
        if not self.dart:
            raise RuntimeError("OPENDART_API_KEY is required for the GitHub-compatible core engine")
        stock_codes = sorted({code for theme in self.config["themes"] for code in theme.get("kr_stock_codes", [])})
        rows_by_report: dict[tuple[int, str], list[dict[str, Any]]] = {}
        errors: dict[str, str] = {}
        start_year = max(2018, as_of.year - 5)
        jobs = [
            (year, report_code)
            for year in range(start_year, as_of.year + 1)
            for report_code, _, _, _ in REPORTS
            if report_available(as_of, year, report_code)
        ]
        print(f"[DART] financial report batches={len(jobs)} companies={len(stock_codes)}", flush=True)
        for index, (year, report_code) in enumerate(jobs, start=1):
            try:
                rows_by_report[(year, report_code)] = self.dart.major_accounts_multi(stock_codes, year, report_code)
            except Exception as exc:
                errors[f"{year}:{report_code}"] = str(exc)
            if index == len(jobs) or index % 5 == 0:
                print(f"[DART] financial progress {index}/{len(jobs)} errors={len(errors)}", flush=True)
        revenue, operating_profit = build_quarterly_financial_series(rows_by_report, stock_codes)
        return revenue, operating_profit, errors

    def score_dart_as_of(self, as_of: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        date_value = dt.date.fromisoformat(as_of)
        disclosures, disclosure_errors = self._dart_disclosures(date_value)
        revenue, operating_profit, financial_errors = self._dart_financials(date_value)
        results: list[dict[str, Any]] = []
        for theme in self.config["themes"]:
            codes = theme.get("kr_stock_codes", [])
            capital_series = event_series(disclosures, codes, date_value, "CAPITAL_EVENT")
            contract_series = event_series(disclosures, codes, date_value, "CONTRACT_EVENT")
            revenue_series = {code: revenue.get(code, []) for code in codes}
            op_profit_series = {code: operating_profit.get(code, []) for code in codes}
            signals = {
                "capital_events": build_event_signal("facility_and_asset_investment", capital_series),
                "supply_contracts": build_event_signal("supply_contract_and_orders", contract_series),
                "revenue": build_metric_signal("revenue_demand", revenue_series),
                "operating_margin": build_margin_signal(revenue_series, op_profit_series),
            }
            usable = sum(
                1
                for code in codes
                if len(revenue_series.get(code, [])) >= 5
                or sum(value for _, value in capital_series.get(code, [])) > 0
                or sum(value for _, value in contract_series.get(code, [])) > 0
            )
            result = build_dart_theme_result(
                theme_id=theme["id"],
                theme_name=theme["name"],
                as_of=as_of,
                signals=signals,
                requested_companies=len(codes),
                usable_companies=usable,
                invalidations=theme.get("invalidations", []),
            )
            results.append(result.to_dict())
        metadata = {
            "source": "OpenDART",
            "disclosure_errors": disclosure_errors,
            "financial_errors": financial_errors,
            "disclosure_company_count": len(disclosures),
        }
        return sorted(results, key=lambda x: (x["boom_score"], x["data_confidence"]), reverse=True), metadata

    def fred_context(self, as_of: str) -> dict[str, Any]:
        if not self.fred:
            return {"status": "SKIPPED", "reason": "FRED_API_KEY missing", "series": {}}
        start_year = str(max(1990, int(as_of[:4]) - 8)) + "-01-01"
        output: dict[str, Any] = {"status": "OK", "as_of": as_of, "series": {}, "errors": {}}
        for item in self.config.get("fred_context", []):
            try:
                rows = self.fred.observations(
                    item["series_id"], observation_start=start_year, observation_end=as_of, as_of=as_of
                )
                clean = [
                    {"date": row.get("date"), "value": float(row["value"])}
                    for row in rows
                    if row.get("value") not in {None, "."}
                ]
                output["series"][item["name"]] = {
                    "series_id": item["series_id"],
                    "latest": clean[-1] if clean else None,
                    "observations": clean[-24:],
                }
            except Exception as exc:
                output["errors"][item["name"]] = str(exc)
        if output["errors"] and output["series"]:
            output["status"] = "PARTIAL"
        elif output["errors"] and not output["series"]:
            output["status"] = "ERROR"
        return output

    def bea_health(self) -> dict[str, Any]:
        if not self.bea:
            return {"status": "SKIPPED", "reason": "BEA_API_KEY missing"}
        try:
            payload = self.bea.dataset_list()
            results = payload.get("BEAAPI", {}).get("Results", {})
            error = results.get("Error") if isinstance(results, dict) else None
            datasets = results.get("Dataset", []) if isinstance(results, dict) else []
            if isinstance(datasets, dict):
                datasets = [datasets]
            if error or not datasets:
                return {
                    "status": "ERROR",
                    "dataset_count": 0,
                    "datasets": [],
                    "error": error or "BEA returned no dataset list",
                }
            return {
                "status": "CONNECTED",
                "dataset_count": len(datasets),
                "datasets": [row.get("DatasetName") for row in datasets],
                "note": "BEA 산업별 고정자산 점수 편입은 다음 검증 단계에서 진행합니다.",
            }
        except Exception as exc:
            return {"status": "ERROR", "error": str(exc)}

    def run(self, *, current_as_of: str, replay_as_of: str | None, include_dart: bool, use_sec: bool = False) -> dict[str, Any]:
        del include_dart  # OpenDART is now the core source, not an optional add-on.
        outputs = self.root / "outputs"
        run_started = time.monotonic()
        print(
            f"[ENGINE] start current_as_of={current_as_of} replay_as_of={replay_as_of} core=OpenDART use_sec={use_sec}",
            flush=True,
        )
        current, current_meta = self.score_dart_as_of(current_as_of)
        self._write_json(outputs / "industry_boom_ranking.json", current)
        self._write_json(outputs / "industry_boom_detail.json", {row["theme_id"]: row for row in current})

        replay: list[dict[str, Any]] = []
        replay_meta: dict[str, Any] = {}
        if replay_as_of:
            print("[SCORING] historical AI replay with report-availability cutoff", flush=True)
            replay, replay_meta = self.score_dart_as_of(replay_as_of)
        self._write_json(
            outputs / "ai_replay_2022.json",
            {
                "as_of": replay_as_of,
                "primary_source": "OpenDART",
                "methodology_warning": (
                    "이번 버전은 GitHub 호스팅 러너의 SEC 403 차단을 피하기 위해 한국 상장 공급망의 시설투자, "
                    "수주·공급계약, 매출, 영업이익률을 당시 보고서 이용 가능시점 기준으로 재현합니다. "
                    "현재 API로 과거 보고서를 조회하므로 당시 원본 빈티지와 이후 정정공시를 완전히 분리하지는 못합니다. 산업 분류는 사전 정의형입니다."
                ),
                "ranking": replay,
                "ai_theme_rank": next(
                    (index + 1 for index, row in enumerate(replay) if row["theme_id"] == "AI_COMPUTE_INFRA"), None
                ),
                "metadata": replay_meta,
            },
        )

        ai_rank = next((index + 1 for index, row in enumerate(replay) if row["theme_id"] == "AI_COMPUTE_INFRA"), None)
        ai_row = next((row for row in replay if row["theme_id"] == "AI_COMPUTE_INFRA"), None)
        validation_passed = bool(ai_rank is not None and ai_rank <= 3 and ai_row and ai_row.get("boom_score", 0) >= 67)
        model_validation = {
            "status": "PASSED" if validation_passed else "FAILED",
            "investment_use_allowed": False,
            "reason": (
                "AI 재현이 사전 기준을 통과했습니다. 추가 성공·실패 산업 워크포워드 검증이 필요합니다."
                if validation_passed else
                "2022-10-31 AI 재현이 상위 3위 및 67점 기준을 통과하지 못했습니다. 현재 순위는 투자판정에 사용할 수 없습니다."
            ),
            "criteria": {"ai_rank_max": 3, "ai_score_min": 67, "additional_backtests_required": True},
            "observed": {
                "ai_rank": ai_rank,
                "ai_score": ai_row.get("boom_score") if ai_row else None,
                "theme_count": len(replay),
            },
            "known_design_gaps": [
                "시설투자·공급계약의 금액이 아니라 공시 건수를 사용합니다.",
                "산업별 한국 대표기업이 4개뿐이라 확산도를 제대로 측정하지 못합니다.",
                "미국 원천수요·빅테크 CAPEX·벤처투자·고용·특허·정부지출이 핵심점수에 편입되지 않았습니다.",
                "사전 정의된 12개 산업만 비교하므로 아무도 모르는 신규 산업을 자동발견하지 못합니다.",
                "붐 확률은 백테스트로 보정되지 않은 단조 변환값입니다.",
            ],
        }
        self._write_json(outputs / "model_validation.json", model_validation)

        facts, sec_errors, sec_status = self._fetch_companyfacts_optional(use_sec)
        if facts:
            self._write_json(outputs / "sec_supplemental_ranking.json", self.score_sec_as_of(current_as_of, facts))

        print("[FRED] macro context", flush=True)
        macro = self.fred_context(current_as_of)
        self._write_json(outputs / "macro_context.json", macro)
        print("[BEA] connectivity check", flush=True)
        bea = self.bea_health()
        source_health = {
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "engine_version": "0.1.4",
            "current_as_of": current_as_of,
            "replay_as_of": replay_as_of,
            "sources": {
                "opendart": {"status": "CONNECTED", "metadata": current_meta},
                "sec": {"status": sec_status, "company_count": len(facts), "errors": dict(list(sec_errors.items())[:10])},
                "fred": {"status": macro.get("status")},
                "bea": bea,
            },
            "limitations": [
                "현재 GitHub 호스팅 러너에서 SEC가 HTTP 403을 반환하므로 OpenDART 한국 공급망 지표가 핵심 계산원입니다.",
                "V0.1.4는 보안·검증 안전판 버전이며 현재 순위는 투자판정에 사용할 수 없습니다.",
                "사전 정의된 산업을 순위화하며 완전한 신규 산업 자동발견 기능은 아직 포함하지 않습니다.",
                "시설투자·수주 공시는 금액이 아닌 건수 프록시입니다.",
                "붐 확률은 초기 점수 변환값이며 성공·실패 산업 워크포워드 백테스트로 보정해야 합니다.",
            ],
        }
        self._write_json(outputs / "engine_health.json", source_health)
        self._write_json(outputs / "korea_corroboration.json", current_meta)
        self._write_json(
            outputs / "run_manifest.json",
            {
                "files": [
                    "industry_boom_ranking.json",
                    "industry_boom_detail.json",
                    "ai_replay_2022.json",
                    "macro_context.json",
                    "korea_corroboration.json",
                    "engine_health.json",
                    "model_validation.json",
                ],
                "model_validation": model_validation,
                "current_top5": [
                    {"rank": i + 1, "theme_id": row["theme_id"], "name": row["theme_name"], "score": row["boom_score"]}
                    for i, row in enumerate(current[:5])
                ],
            },
        )
        print(f"[ENGINE] finished in {time.monotonic() - run_started:.1f}s", flush=True)
        return source_health

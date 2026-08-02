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
    build_annual_capex_series,
    normalize_annual_capex_by_revenue,
    classify_disclosure,
    enrich_disclosure_amount,
    event_amount_series,
    event_series,
    normalize_event_amounts_by_revenue,
    report_available,
)
from ible.analytics.scoring import (
    build_amount_event_signal,
    build_annual_capex_signal,
    build_dart_theme_result,
    build_event_signal,
    build_margin_signal,
    build_metric_signal,
    build_research_signal,
    build_theme_result,
)
from ible.analytics.sec_metrics import FLOW_TAGS, quarterly_flow
from ible.collectors.arxiv import ArxivClient
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
        self.arxiv = ArxivClient(self.http)
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
        serialized = json.dumps(safe_payload, ensure_ascii=False, indent=2)
        if "api_key=" in serialized.lower() and "<redacted>" not in serialized.lower():
            raise RuntimeError(f"SECRET_SCAN_FAILED: {path.name}")
        path.write_text(serialized, encoding="utf-8")

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
                    print("[SEC] GitHub runner blocked; continuing without SEC.", flush=True)
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
            raise RuntimeError("OPENDART_API_KEY is required")
        stock_codes = sorted({code for theme in self.config["themes"] for code in theme.get("kr_stock_codes", [])})
        begin = as_of - dt.timedelta(days=12 * 91 + 21)
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

    def _enrich_disclosure_amounts(
        self, disclosures: dict[str, list[dict[str, Any]]]
    ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
        if not self.dart:
            return disclosures, {"global": "OpenDART client missing"}
        enriched = {code: [dict(row) for row in rows] for code, rows in disclosures.items()}
        tasks: list[tuple[str, int, str]] = []
        for code, rows in enriched.items():
            for index, row in enumerate(rows):
                if not classify_disclosure(str(row.get("report_nm") or "")):
                    continue
                rcept_no = str(row.get("rcept_no") or "").strip()
                if rcept_no:
                    tasks.append((code, index, rcept_no))
        errors: dict[str, str] = {}
        print(f"[DART] original-document amount extraction targets={len(tasks)}", flush=True)
        if not tasks:
            return enriched, errors

        def fetch(task: tuple[str, int, str]) -> tuple[str, int, dict[str, Any]]:
            code, index, rcept_no = task
            text = self.dart.document_text(rcept_no)
            return code, index, enrich_disclosure_amount(enriched[code][index], text)

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(fetch, task): task for task in tasks}
            completed = 0
            for future in as_completed(futures):
                code, index, rcept_no = futures[future]
                try:
                    result_code, result_index, row = future.result()
                    enriched[result_code][result_index] = row
                except Exception as exc:
                    errors[rcept_no] = str(exc)
                    enriched[code][index]["event_type"] = classify_disclosure(
                        str(enriched[code][index].get("report_nm") or "")
                    )
                    enriched[code][index]["event_amount_metadata"] = {"status": "FETCH_ERROR"}
                completed += 1
                if completed == len(tasks) or completed % 20 == 0:
                    print(f"[DART] amount extraction {completed}/{len(tasks)} errors={len(errors)}", flush=True)
        return enriched, errors

    def _dart_financials(
        self, as_of: dt.date
    ) -> tuple[dict[str, list[tuple[str, float]]], dict[str, list[tuple[str, float]]], dict[str, str]]:
        if not self.dart:
            raise RuntimeError("OPENDART_API_KEY is required")
        stock_codes = sorted({code for theme in self.config["themes"] for code in theme.get("kr_stock_codes", [])})
        rows_by_report: dict[tuple[int, str], list[dict[str, Any]]] = {}
        errors: dict[str, str] = {}
        start_year = max(2017, as_of.year - 6)
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

    def _dart_annual_capex(
        self, as_of: dt.date, stock_codes: list[str]
    ) -> tuple[dict[str, list[tuple[str, float]]], dict[str, Any], dict[str, str]]:
        if not self.dart:
            raise RuntimeError("OPENDART_API_KEY is required")
        latest_year = as_of.year - 1 if as_of >= dt.date(as_of.year, 3, 31) else as_of.year - 2
        years = list(range(max(2015, latest_year - 3), latest_year + 1))
        jobs = [(code, year) for code in stock_codes for year in years]
        rows_by_company_year: dict[tuple[str, int], list[dict[str, Any]]] = {}
        errors: dict[str, str] = {}
        print(f"[DART] annual cash-flow CAPEX jobs={len(jobs)} years={years[0]}-{years[-1]}", flush=True)

        def fetch(job: tuple[str, int]) -> tuple[str, int, list[dict[str, Any]]]:
            code, year = job
            return code, year, self.dart.full_accounts(code, year, "11011")

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(fetch, job): job for job in jobs}
            completed = 0
            for future in as_completed(futures):
                code, year = futures[future]
                try:
                    result_code, result_year, rows = future.result()
                    rows_by_company_year[(result_code, result_year)] = rows
                except Exception as exc:
                    errors[f"{code}:{year}"] = str(exc)
                completed += 1
                if completed == len(jobs) or completed % 25 == 0:
                    print(f"[DART] annual CAPEX progress {completed}/{len(jobs)} errors={len(errors)}", flush=True)
        series, quality = build_annual_capex_series(rows_by_company_year, stock_codes)
        return series, quality, errors

    def _research_momentum(self, as_of: dt.date) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
        results: dict[str, dict[str, Any]] = {}
        errors: dict[str, str] = {}
        themes = self.config["themes"]
        print(f"[ARXIV] technology momentum themes={len(themes)} as_of={as_of}", flush=True)
        for index, theme in enumerate(themes, start=1):
            query = theme.get("arxiv_query")
            if not query:
                continue
            try:
                results[theme["id"]] = self.arxiv.momentum(query, as_of)
            except Exception as exc:
                errors[theme["id"]] = str(exc)
            print(f"[ARXIV] progress {index}/{len(themes)} errors={len(errors)}", flush=True)
        return results, errors

    def score_dart_as_of(self, as_of: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        date_value = dt.date.fromisoformat(as_of)
        disclosures, disclosure_errors = self._dart_disclosures(date_value)
        disclosures, amount_errors = self._enrich_disclosure_amounts(disclosures)
        revenue, operating_profit, financial_errors = self._dart_financials(date_value)
        all_codes = sorted({code for theme in self.config["themes"] for code in theme.get("kr_stock_codes", [])})
        annual_capex, annual_capex_quality, annual_capex_errors = self._dart_annual_capex(date_value, all_codes)
        normalized_annual_capex = normalize_annual_capex_by_revenue(annual_capex, revenue)
        research, research_errors = self._research_momentum(date_value)
        results: list[dict[str, Any]] = []
        amount_quality_by_theme: dict[str, Any] = {}

        for theme in self.config["themes"]:
            codes = theme.get("kr_stock_codes", [])
            capital_counts = event_series(disclosures, codes, date_value, "CAPITAL_EVENT")
            contract_counts = event_series(disclosures, codes, date_value, "CONTRACT_EVENT")
            capital_amounts, capital_quality = event_amount_series(
                disclosures, codes, date_value, "CAPITAL_EVENT"
            )
            contract_amounts, contract_quality = event_amount_series(
                disclosures, codes, date_value, "CONTRACT_EVENT"
            )
            revenue_series = {code: revenue.get(code, []) for code in codes}
            op_profit_series = {code: operating_profit.get(code, []) for code in codes}
            normalized_capital = normalize_event_amounts_by_revenue(capital_amounts, revenue_series)
            normalized_contracts = normalize_event_amounts_by_revenue(contract_amounts, revenue_series)
            theme_annual_capex = {code: normalized_annual_capex.get(code, []) for code in codes}
            capital_count_signal = build_event_signal("active_facility_investment_count", capital_counts)
            contract_count_signal = build_event_signal("active_supply_contract_count", contract_counts)
            signals = {
                "capital_events": capital_count_signal,
                "capital_amounts": build_amount_event_signal(
                    "facility_investment_amount",
                    normalized_capital,
                    capital_count_signal,
                    float(capital_quality["amount_coverage"]),
                ),
                "supply_contracts": contract_count_signal,
                "cashflow_capex": build_annual_capex_signal(
                    "cashflow_capex_intensity", theme_annual_capex
                ),
                "contract_amounts": build_amount_event_signal(
                    "supply_contract_amount",
                    normalized_contracts,
                    contract_count_signal,
                    float(contract_quality["amount_coverage"]),
                ),
                "revenue": build_metric_signal("revenue_demand", revenue_series),
                "operating_margin": build_margin_signal(revenue_series, op_profit_series),
                "research_momentum": build_research_signal(
                    "technology_research_diffusion", research.get(theme["id"])
                ),
            }
            usable = sum(
                1
                for code in codes
                if len(revenue_series.get(code, [])) >= 5
                or sum(value for _, value in capital_counts.get(code, [])) > 0
                or sum(value for _, value in contract_counts.get(code, [])) > 0
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
            amount_quality_by_theme[theme["id"]] = {
                "capital": capital_quality,
                "contracts": contract_quality,
            }
        metadata = {
            "sources": ["OpenDART", "arXiv"],
            "disclosure_errors": disclosure_errors,
            "amount_document_errors": dict(list(amount_errors.items())[:50]),
            "financial_errors": financial_errors,
            "annual_capex_errors": dict(list(annual_capex_errors.items())[:50]),
            "annual_capex_quality": annual_capex_quality,
            "research_errors": research_errors,
            "disclosure_company_count": len(disclosures),
            "technology_momentum": research,
            "event_amount_quality": amount_quality_by_theme,
        }
        return sorted(results, key=lambda x: (x["boom_score"], x["data_confidence"]), reverse=True), metadata

    def fred_context(self, as_of: str) -> dict[str, Any]:
        if not self.fred:
            return {"status": "SKIPPED", "reason": "FRED_API_KEY missing", "series": {}}
        start_year = str(max(1990, int(as_of[:4]) - 8)) + "-01-01"
        output: dict[str, Any] = {"status": "OK", "as_of": as_of, "series": {}, "errors": {}}
        for item in self.config.get("fred_context", []):
            try:
                rows, metadata = self.fred.observations(
                    item["series_id"], observation_start=start_year, observation_end=as_of, as_of=as_of
                )
                clean = [
                    {"date": row.get("date"), "value": float(row["value"])}
                    for row in rows
                    if row.get("value") not in {None, ".", ""}
                ]
                output["series"][item["name"]] = {
                    "series_id": item["series_id"],
                    "latest": clean[-1] if clean else None,
                    "observations": clean[-24:],
                    "source_metadata": metadata,
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
                    "error": error or "no datasets",
                    "key_format_warning": getattr(self.bea, "key_format_warning", None),
                }
            catalog_payload = self.bea.fixed_asset_table_catalog()
            catalog_results = catalog_payload.get("BEAAPI", {}).get("Results", {})
            values = catalog_results.get("ParamValue", []) if isinstance(catalog_results, dict) else []
            if isinstance(values, dict):
                values = [values]
            matches = [
                row for row in values
                if any(term in str(row).lower() for term in ("investment", "industry", "fixed assets"))
            ][:40]
            return {
                "status": "CONNECTED",
                "dataset_count": len(datasets),
                "datasets": [row.get("DatasetName") for row in datasets],
                "fixed_asset_catalog_matches": matches,
                "key_format_warning": getattr(self.bea, "key_format_warning", None),
            }
        except Exception as exc:
            return {"status": "ERROR", "error": str(exc)}

    def run(
        self,
        *,
        current_as_of: str,
        replay_as_of: str | None,
        include_dart: bool,
        use_sec: bool = False,
    ) -> dict[str, Any]:
        del include_dart
        outputs = self.root / "outputs"
        run_started = time.monotonic()
        print(
            f"[ENGINE] start version=0.4.1 current_as_of={current_as_of} replay_as_of={replay_as_of} use_sec={use_sec}",
            flush=True,
        )
        current, current_meta = self.score_dart_as_of(current_as_of)
        self._write_json(outputs / "industry_boom_ranking.json", current)
        self._write_json(outputs / "industry_boom_detail.json", {row["theme_id"]: row for row in current})
        self._write_json(outputs / "technology_momentum.json", current_meta.get("technology_momentum", {}))
        self._write_json(outputs / "event_amount_quality.json", current_meta.get("event_amount_quality", {}))
        self._write_json(outputs / "cashflow_capex_quality.json", current_meta.get("annual_capex_quality", {}))

        replay: list[dict[str, Any]] = []
        replay_meta: dict[str, Any] = {}
        if replay_as_of:
            print("[SCORING] historical AI replay", flush=True)
            replay, replay_meta = self.score_dart_as_of(replay_as_of)
        else:
            existing_replay_path = outputs / "ai_replay_2022.json"
            if existing_replay_path.exists():
                try:
                    existing_replay = json.loads(existing_replay_path.read_text(encoding="utf-8"))
                    replay = list(existing_replay.get("ranking") or [])
                    replay_meta = dict(existing_replay.get("metadata") or {})
                    replay_as_of = existing_replay.get("as_of")
                    print("[SCORING] replay skipped; reused committed AI replay output", flush=True)
                except Exception as exc:
                    print(f"[SCORING] replay reuse failed: {exc}", flush=True)
        ai_rank = next((i + 1 for i, row in enumerate(replay) if row["theme_id"] == "AI_COMPUTE_INFRA"), None)
        ai_row = next((row for row in replay if row["theme_id"] == "AI_COMPUTE_INFRA"), None)
        semi_row = next((row for row in replay if row["theme_id"] == "SEMICONDUCTOR_EQUIPMENT"), None)
        ai_value_chain_score = None
        ai_value_chain_rank = None
        if ai_row and semi_row:
            ai_value_chain_score = round(0.62 * float(ai_row.get("boom_score", 0)) + 0.38 * float(semi_row.get("boom_score", 0)), 2)
            ai_value_chain_rank = 1 + sum(1 for row in replay if float(row.get("boom_score", 0)) > ai_value_chain_score)
        ai_amount_coverage = ai_row.get("coverage", {}).get("amount_coverage", 0.0) if ai_row else 0.0
        ai_research = bool(ai_row and ai_row.get("coverage", {}).get("independent_research_source"))
        self._write_json(
            outputs / "ai_replay_2022.json",
            {
                "as_of": replay_as_of,
                "engine_version": "0.4.1",
                "primary_sources": ["OpenDART original documents", "OpenDART financials", "arXiv"],
                "methodology_warning": (
                    "투자·계약 원문 금액과 기술연구 확산을 추가했지만 미국 빅테크 CAPEX 원천자료와 "
                    "완전한 당시 빈티지 공시는 아직 부족합니다. 결과는 검증용이며 투자판정용이 아닙니다."
                ),
                "ranking": replay,
                "score_definition": {
                    "main_rank": "preboom_score",
                    "early_signal": "기술연구·실집행 CAPEX·초기매출·확산",
                    "commercial_realization": "투자공시·공급계약·매출·마진",
                    "transition_gap": "선행강도와 상업화 실현도의 격차",
                },
                "ai_theme_rank": ai_rank,
                "ai_signal_phase": ai_row.get("stage") if ai_row else None,
                "ai_early_signal_score": ai_row.get("early_signal_score") if ai_row else None,
                "ai_commercial_realization_score": ai_row.get("commercial_realization_score") if ai_row else None,
                "ai_cross_confirmation_score": ai_row.get("cross_confirmation_score") if ai_row else None,
                "ai_value_chain_diagnostic": {
                    "definition": "62% AI compute + 38% semiconductor equipment/advanced packaging",
                    "score": ai_value_chain_score,
                    "rank_equivalent": ai_value_chain_rank,
                    "validation_target": False,
                },
                "metadata": replay_meta,
            },
        )

        ai_stage = ai_row.get("stage") if ai_row else None
        ai_early_score = ai_row.get("early_signal_score") if ai_row else None
        ai_commercial_score = ai_row.get("commercial_realization_score") if ai_row else None
        ai_cross_confirmation = ai_row.get("cross_confirmation_score") if ai_row else None
        validation_passed = bool(
            ai_rank is not None
            and ai_rank <= 3
            and ai_row
            and float(ai_row.get("boom_score", 0)) >= 60
            and float(ai_early_score or 0) >= 60
            and ai_stage in {"EARLY_ACCUMULATION", "TRANSITION"}
            and ai_amount_coverage >= 0.35
            and ai_research
        )
        model_validation = {
            "status": "PASSED_STAGE1" if validation_passed else "FAILED",
            "investment_use_allowed": False,
            "reason": (
                "AI 붐 이전 선행축적 재현은 통과했지만 성공·실패 산업 전체 워크포워드 검증 전에는 투자에 사용할 수 없습니다."
                if validation_passed
                else "AI 선행축적 순위·초기점수·교차확인·금액추출률 기준 중 하나 이상을 통과하지 못했습니다."
            ),
            "criteria": {
                "ai_preboom_rank_max": 3,
                "ai_preboom_score_min": 60,
                "ai_early_signal_score_min": 60,
                "ai_phase_allowed": ["EARLY_ACCUMULATION", "TRANSITION"],
                "ai_amount_coverage_min": 0.35,
                "independent_research_source_required": True,
                "additional_backtests_required": True,
            },
            "observed": {
                "ai_preboom_rank": ai_rank,
                "ai_preboom_score": ai_row.get("boom_score") if ai_row else None,
                "ai_early_signal_score": ai_early_score,
                "ai_commercial_realization_score": ai_commercial_score,
                "ai_cross_confirmation_score": ai_cross_confirmation,
                "ai_phase": ai_stage,
                "ai_amount_coverage": ai_amount_coverage,
                "ai_research_source": ai_research,
                "ai_value_chain_score": ai_value_chain_score,
                "ai_value_chain_rank_equivalent": ai_value_chain_rank,
                "theme_count": len(replay),
            },
            "known_design_gaps": [
                "미국 빅테크 CAPEX·클라우드 원천수요가 핵심점수에 아직 직접 편입되지 않았습니다.",
                "산업별 한국 대표기업 표본이 작아 공급망 전체 확산도를 충분히 측정하지 못합니다.",
                "사전 정의된 12개 산업만 비교하므로 신규 산업 자동발견은 아직 제한적입니다.",
                "붐 확률은 다수 성공·실패 산업 백테스트로 아직 보정되지 않았습니다.",
            ],
        }
        self._write_json(outputs / "model_validation.json", model_validation)

        facts, sec_errors, sec_status = self._fetch_companyfacts_optional(use_sec)
        if facts:
            self._write_json(outputs / "sec_supplemental_ranking.json", self.score_sec_as_of(current_as_of, facts))

        print("[FRED] macro context with official CSV fallback", flush=True)
        macro = self.fred_context(current_as_of)
        self._write_json(outputs / "macro_context.json", macro)
        print("[BEA] dataset/catalog check", flush=True)
        bea = self.bea_health()
        self._write_json(outputs / "bea_context.json", bea)

        source_health = {
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "engine_version": "0.4.1",
            "current_as_of": current_as_of,
            "replay_as_of": replay_as_of,
            "sources": {
                "opendart": {"status": "CONNECTED", "metadata": current_meta},
                "arxiv": {"status": "PARTIAL" if current_meta.get("research_errors") else "CONNECTED"},
                "sec": {"status": sec_status, "company_count": len(facts), "errors": dict(list(sec_errors.items())[:10])},
                "fred": {"status": macro.get("status")},
                "bea": bea,
            },
            "limitations": [
                "현재 순위는 검증용이며 model_validation의 investment_use_allowed는 계속 false입니다.",
                "OpenDART 원문 금액은 표 구조 차이 때문에 추출 실패 가능성이 있어 추출률을 함께 표시합니다.",
                "FRED API가 GitHub에서 403이면 공식 CSV로 우회하지만 과거 재현은 수정값 기준 컷오프입니다.",
                "미국 기업 원천 CAPEX는 SEC GitHub 차단 때문에 선택적 보강자료로만 남아 있습니다.",
            ],
        }
        self._write_json(outputs / "engine_health.json", source_health)
        self._write_json(outputs / "korea_corroboration.json", current_meta)
        output_files = [
            "industry_boom_ranking.json",
            "industry_boom_detail.json",
            "ai_replay_2022.json",
            "technology_momentum.json",
            "event_amount_quality.json",
            "cashflow_capex_quality.json",
            "macro_context.json",
            "bea_context.json",
            "korea_corroboration.json",
            "engine_health.json",
            "model_validation.json",
        ]
        self._write_json(
            outputs / "run_manifest.json",
            {
                "files": output_files + ["run_manifest.json"],
                "model_validation": model_validation,
                "current_top5": [
                    {
                        "rank": i + 1,
                        "theme_id": row["theme_id"],
                        "name": row["theme_name"],
                        "preboom_score": row["boom_score"],
                        "early_signal_score": row.get("early_signal_score"),
                        "commercial_realization_score": row.get("commercial_realization_score"),
                        "stage": row.get("stage"),
                    }
                    for i, row in enumerate(current[:5])
                ],
            },
        )
        print(f"[ENGINE] finished in {time.monotonic() - run_started:.1f}s", flush=True)
        return source_health

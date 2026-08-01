from __future__ import annotations

import datetime as dt
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from ible.analytics.scoring import build_margin_signal, build_metric_signal, build_theme_result
from ible.analytics.sec_metrics import FLOW_TAGS, quarterly_flow
from ible.collectors.bea import BeaClient
from ible.collectors.fred import FredClient
from ible.collectors.opendart import OpenDartClient
from ible.collectors.sec import SecClient
from ible.config import load_yaml
from ible.http import JsonHttpClient


class EnginePipeline:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.config = load_yaml(root / "config" / "themes.yml")
        user_agent = os.getenv("SEC_USER_AGENT", "").strip()
        if not user_agent or "@" not in user_agent:
            raise RuntimeError(
                "SEC_USER_AGENT repository variable is required and must include a contact email. "
                "Example: IndustryBoomLeadingEngine/0.1 your-email@example.com"
            )
        self.http = JsonHttpClient(
            user_agent=user_agent,
            timeout=12,
            min_interval=0.30,
            retries=1,
            cache_dir=root / ".cache",
        )
        self.sec = SecClient(self.http, root / "config" / "sec_cik_map.json")
        self.fred = FredClient(os.getenv("FRED_API_KEY", ""), self.http) if os.getenv("FRED_API_KEY") else None
        self.bea = BeaClient(os.getenv("BEA_API_KEY", ""), self.http) if os.getenv("BEA_API_KEY") else None
        self.dart = OpenDartClient(os.getenv("OPENDART_API_KEY", ""), self.http) if os.getenv("OPENDART_API_KEY") else None

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    def _fetch_companyfacts(self) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
        requested = sorted({ticker for theme in self.config["themes"] for ticker in theme.get("us_tickers", [])})
        facts: dict[str, dict[str, Any]] = {}
        errors: dict[str, str] = {}
        started = time.monotonic()
        total = len(requested)
        print(f"[SEC] collecting {total} companyfacts with 3 workers (local CIK map)", flush=True)

        # Work in small batches so a broad SEC/network failure aborts quickly instead of hanging for 20+ minutes.
        batch_size = 12
        for batch_start in range(0, total, batch_size):
            batch = requested[batch_start : batch_start + batch_size]
            with ThreadPoolExecutor(max_workers=3) as pool:
                future_map = {pool.submit(self.sec.companyfacts, ticker): ticker for ticker in batch}
                for future in as_completed(future_map):
                    ticker = future_map[future]
                    try:
                        facts[ticker] = future.result()
                    except Exception as exc:  # one company must not break the entire engine
                        errors[ticker] = str(exc)
                    done = len(facts) + len(errors)
                    if done == total or done % 5 == 0:
                        elapsed = time.monotonic() - started
                        print(
                            f"[SEC] {done}/{total} complete | ok={len(facts)} error={len(errors)} | {elapsed:.1f}s",
                            flush=True,
                        )

            attempted = len(facts) + len(errors)
            # If the first broad sample cannot reach SEC, stop with a useful error in under ~1 minute.
            if attempted >= batch_size and len(facts) < 2 and len(errors) >= batch_size - 1:
                first_errors = list(errors.items())[:3]
                raise RuntimeError(
                    "SEC_BROAD_CONNECTIVITY_FAILURE: almost all initial companyfacts requests failed. "
                    f"sample_errors={first_errors}"
                )

        print(f"[SEC] finished in {time.monotonic() - started:.1f}s", flush=True)
        return facts, errors

    def score_as_of(self, as_of: str, facts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
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
        if output["errors"]:
            output["status"] = "PARTIAL"
        return output

    def bea_health(self) -> dict[str, Any]:
        if not self.bea:
            return {"status": "SKIPPED", "reason": "BEA_API_KEY missing"}
        try:
            payload = self.bea.dataset_list()
            datasets = payload.get("BEAAPI", {}).get("Results", {}).get("Dataset", [])
            return {
                "status": "CONNECTED",
                "dataset_count": len(datasets),
                "datasets": [row.get("DatasetName") for row in datasets],
                "note": "V0.1은 BEA 연결상태를 검증하고, 산업별 고정자산 매핑은 V0.2에서 점수에 편입합니다.",
            }
        except Exception as exc:
            return {"status": "ERROR", "error": str(exc)}

    def dart_corroboration(self, as_of: str, enabled: bool) -> dict[str, Any]:
        if not enabled:
            return {"status": "SKIPPED", "reason": "include_dart=false"}
        if not self.dart:
            return {"status": "SKIPPED", "reason": "OPENDART_API_KEY missing"}
        end = dt.date.fromisoformat(as_of)
        begin = end - dt.timedelta(days=550)
        facility_terms = ("신규시설투자", "시설투자", "타법인주식및출자증권취득결정")
        contract_terms = ("단일판매", "공급계약", "수주")
        output: dict[str, Any] = {"status": "OK", "as_of": as_of, "themes": {}, "errors": {}}

        theme_companies = {theme["id"]: theme.get("kr_stock_codes", [])[:4] for theme in self.config["themes"]}
        stock_codes = sorted({code for codes in theme_companies.values() for code in codes})
        disclosures_by_code: dict[str, list[dict[str, Any]]] = {}
        started = time.monotonic()
        print(f"[DART] collecting {len(stock_codes)} companies with 4 workers", flush=True)

        def fetch(code: str) -> list[dict[str, Any]]:
            return self.dart.disclosures(code, begin.strftime("%Y%m%d"), end.strftime("%Y%m%d"), 100)

        with ThreadPoolExecutor(max_workers=3) as pool:
            future_map = {pool.submit(fetch, code): code for code in stock_codes}
            for future in as_completed(future_map):
                code = future_map[future]
                try:
                    disclosures_by_code[code] = future.result()
                except Exception as exc:
                    output["errors"][code] = str(exc)
                done = len(disclosures_by_code) + len(output["errors"])
                if done == len(stock_codes) or done % 5 == 0:
                    print(
                        f"[DART] {done}/{len(stock_codes)} complete | error={len(output['errors'])}",
                        flush=True,
                    )

        for theme in self.config["themes"]:
            companies = theme_companies[theme["id"]]
            reports: list[dict[str, Any]] = []
            for stock_code in companies:
                for row in disclosures_by_code.get(stock_code, []):
                    name = row.get("report_nm", "")
                    category = None
                    if any(term in name for term in facility_terms):
                        category = "FACILITY_INVESTMENT"
                    elif any(term in name for term in contract_terms):
                        category = "SUPPLY_CONTRACT"
                    if category:
                        reports.append(
                            {
                                "stock_code": stock_code,
                                "category": category,
                                "report_name": name,
                                "receipt_date": row.get("rcept_dt"),
                                "receipt_no": row.get("rcept_no"),
                            }
                        )
            output["themes"][theme["id"]] = {
                "company_count": len(companies),
                "event_count": len(reports),
                "facility_event_count": sum(1 for row in reports if row["category"] == "FACILITY_INVESTMENT"),
                "contract_event_count": sum(1 for row in reports if row["category"] == "SUPPLY_CONTRACT"),
                "events": reports[:30],
            }
        if output["errors"]:
            output["status"] = "PARTIAL"
        print(f"[DART] finished in {time.monotonic() - started:.1f}s", flush=True)
        return output

    def run(self, *, current_as_of: str, replay_as_of: str | None, include_dart: bool) -> dict[str, Any]:
        outputs = self.root / "outputs"
        run_started = time.monotonic()
        print(f"[ENGINE] start current_as_of={current_as_of} replay_as_of={replay_as_of} include_dart={include_dart}", flush=True)
        facts, sec_errors = self._fetch_companyfacts()
        print("[SCORING] current ranking", flush=True)
        current = self.score_as_of(current_as_of, facts)
        self._write_json(outputs / "industry_boom_ranking.json", current)
        self._write_json(outputs / "industry_boom_detail.json", {row["theme_id"]: row for row in current})

        print("[SCORING] historical replay", flush=True)
        replay = self.score_as_of(replay_as_of, facts) if replay_as_of else []
        self._write_json(
            outputs / "ai_replay_2022.json",
            {
                "as_of": replay_as_of,
                "methodology_warning": (
                    "이 재현시험은 당시 공시일 기준 데이터만 사용하지만 산업 테마 정의 자체는 사후적으로 구성된 "
                    "taxonomy-aware replay입니다. 완전한 블라인드 산업발견 시험은 V0.3에서 별도 수행해야 합니다."
                ),
                "ranking": replay,
                "ai_theme_rank": next(
                    (index + 1 for index, row in enumerate(replay) if row["theme_id"] == "AI_COMPUTE_INFRA"), None
                ),
            },
        )

        print("[FRED] macro context", flush=True)
        macro = self.fred_context(current_as_of)
        self._write_json(outputs / "macro_context.json", macro)
        dart = self.dart_corroboration(current_as_of, include_dart)
        self._write_json(outputs / "korea_corroboration.json", dart)
        print("[BEA] connectivity check", flush=True)
        bea = self.bea_health()
        health = {
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "engine_version": "0.1.2",
            "current_as_of": current_as_of,
            "replay_as_of": replay_as_of,
            "sources": {
                "sec": {"status": "CONNECTED" if facts else "ERROR", "company_count": len(facts), "errors": sec_errors},
                "fred": {"status": macro.get("status")},
                "bea": bea,
                "opendart": {"status": dart.get("status")},
            },
            "limitations": [
                "V0.1은 사전 정의된 산업 테마를 순위화하며 완전한 신규 산업 자동발견 기능은 아직 포함하지 않습니다.",
                "붐 확률은 초기 단조 변환값이며 과거 성공·실패 산업의 워크포워드 백테스트로 보정해야 합니다.",
                "BEA 산업별 고정자산과 시장 주가 미반영 점수는 후속 버전에서 정식 편입합니다.",
            ],
        }
        self._write_json(outputs / "engine_health.json", health)
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
                ],
                "current_top5": [
                    {"rank": i + 1, "theme_id": row["theme_id"], "name": row["theme_name"], "score": row["boom_score"]}
                    for i, row in enumerate(current[:5])
                ],
            },
        )
        print(f"[ENGINE] finished in {time.monotonic() - run_started:.1f}s", flush=True)
        return health

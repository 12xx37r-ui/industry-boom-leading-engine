from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COUNTER_KEYS = (
    "network_calls",
    "cache_hits",
    "deduplicated_calls",
    "retries",
    "http_429",
    "http_5xx",
    "timeouts",
    "lkg_uses",
    "fallback_uses",
)


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _int(row: dict[str, Any], key: str) -> int:
    try:
        return max(0, int(row.get(key) or 0))
    except Exception:
        return 0


def _ratio(current: int, previous: int, min_baseline: int) -> float | None:
    if previous < min_baseline:
        return None
    return round(current / previous, 3) if previous else None


def build_report(
    current: dict[str, Any],
    previous: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    providers_now = current.get("providers") or {}
    providers_prev = previous.get("providers") or {}

    warn_ratio = float(config.get("provider_call_ratio_warn", 2.0))
    strong_ratio = float(config.get("provider_call_ratio_strong_warn", 3.0))
    total_warn_ratio = float(config.get("total_call_ratio_warn", 2.0))
    total_strong_ratio = float(config.get("total_call_ratio_strong_warn", 3.0))
    min_baseline = int(config.get("ratio_min_previous_calls", 3))
    warn_429 = int(config.get("http_429_warn", 1))
    strong_429 = int(config.get("http_429_strong_warn", 3))
    warn_timeout = int(config.get("timeouts_warn", 1))
    strong_timeout = int(config.get("timeouts_strong_warn", 3))
    warn_retry = int(config.get("retries_warn", 3))
    strong_retry = int(config.get("retries_strong_warn", 8))

    provider_rows = []
    warnings: list[dict[str, Any]] = []

    all_names = sorted(set(providers_now) | set(providers_prev))
    for name in all_names:
        now = providers_now.get(name) or {}
        prev = providers_prev.get(name) or {}
        network = _int(now, "network_calls")
        prev_network = _int(prev, "network_calls")
        ratio = _ratio(network, prev_network, min_baseline)

        row = {
            "provider": name,
            "status": str(now.get("status") or "UNAVAILABLE"),
            "network_calls": network,
            "previous_network_calls": prev_network,
            "network_call_ratio_vs_previous": ratio,
        }
        for key in COUNTER_KEYS:
            row[key] = _int(now, key)
        provider_rows.append(row)

        if ratio is not None and ratio >= strong_ratio:
            warnings.append({
                "severity": "STRONG_WARN",
                "provider": name,
                "type": "NETWORK_CALL_SPIKE",
                "message": f"{name}: network calls {network} vs previous {prev_network} ({ratio:.2f}x)",
            })
        elif ratio is not None and ratio >= warn_ratio:
            warnings.append({
                "severity": "WARN",
                "provider": name,
                "type": "NETWORK_CALL_SPIKE",
                "message": f"{name}: network calls {network} vs previous {prev_network} ({ratio:.2f}x)",
            })

        count_429 = _int(now, "http_429")
        if count_429 >= strong_429:
            warnings.append({
                "severity": "STRONG_WARN", "provider": name, "type": "HTTP_429",
                "message": f"{name}: HTTP 429 count={count_429}",
            })
        elif count_429 >= warn_429:
            warnings.append({
                "severity": "WARN", "provider": name, "type": "HTTP_429",
                "message": f"{name}: HTTP 429 count={count_429}",
            })

        timeouts = _int(now, "timeouts")
        if timeouts >= strong_timeout:
            warnings.append({
                "severity": "STRONG_WARN", "provider": name, "type": "TIMEOUT",
                "message": f"{name}: timeout count={timeouts}",
            })
        elif timeouts >= warn_timeout:
            warnings.append({
                "severity": "WARN", "provider": name, "type": "TIMEOUT",
                "message": f"{name}: timeout count={timeouts}",
            })

        retries = _int(now, "retries")
        if retries >= strong_retry:
            warnings.append({
                "severity": "STRONG_WARN", "provider": name, "type": "RETRY_PRESSURE",
                "message": f"{name}: retries={retries}",
            })
        elif retries >= warn_retry:
            warnings.append({
                "severity": "WARN", "provider": name, "type": "RETRY_PRESSURE",
                "message": f"{name}: retries={retries}",
            })

    total_network = sum(row["network_calls"] for row in provider_rows)
    previous_total_network = sum(_int(row or {}, "network_calls") for row in providers_prev.values())
    total_ratio = _ratio(total_network, previous_total_network, min_baseline)

    if total_ratio is not None and total_ratio >= total_strong_ratio:
        warnings.append({
            "severity": "STRONG_WARN", "provider": "ALL", "type": "TOTAL_NETWORK_CALL_SPIKE",
            "message": f"total network calls {total_network} vs previous {previous_total_network} ({total_ratio:.2f}x)",
        })
    elif total_ratio is not None and total_ratio >= total_warn_ratio:
        warnings.append({
            "severity": "WARN", "provider": "ALL", "type": "TOTAL_NETWORK_CALL_SPIKE",
            "message": f"total network calls {total_network} vs previous {previous_total_network} ({total_ratio:.2f}x)",
        })

    total_429 = sum(row["http_429"] for row in provider_rows)
    total_timeouts = sum(row["timeouts"] for row in provider_rows)
    total_retries = sum(row["retries"] for row in provider_rows)

    strong_count = sum(w["severity"] == "STRONG_WARN" for w in warnings)
    warn_count = len(warnings) - strong_count
    status = "STRONG_WARN" if strong_count else ("WARN" if warn_count else "OK")

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "blocking": False,
        "policy_note": "Monitoring only. This guard never changes LIVE/CACHE/LKG selection and never blocks the engine.",
        "current_health_generated_at": current.get("generated_at"),
        "previous_health_generated_at": previous.get("generated_at"),
        "summary": {
            "total_network_calls": total_network,
            "previous_total_network_calls": previous_total_network,
            "total_network_call_ratio_vs_previous": total_ratio,
            "total_cache_hits": sum(row["cache_hits"] for row in provider_rows),
            "total_http_429": total_429,
            "total_retries": total_retries,
            "total_timeouts": total_timeouts,
            "warning_count": warn_count,
            "strong_warning_count": strong_count,
        },
        "providers": provider_rows,
        "warnings": warnings,
    }


def _write_step_summary(report: dict[str, Any]) -> None:
    import os
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    summary = report["summary"]
    lines = [
        "## API Usage Guard",
        "",
        f"**Status:** `{report['status']}`  ",
        f"**Total network calls:** `{summary['total_network_calls']}`"
        f" (previous `{summary['previous_total_network_calls']}`"
        f", ratio `{summary['total_network_call_ratio_vs_previous']}`)  ",
        f"**Cache hits:** `{summary['total_cache_hits']}` · "
        f"**429:** `{summary['total_http_429']}` · "
        f"**Retries:** `{summary['total_retries']}` · "
        f"**Timeouts:** `{summary['total_timeouts']}`",
        "",
        "| Provider | Status | Network | Prev | Ratio | Cache | 429 | Retry | Timeout |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["providers"]:
        lines.append(
            f"| {row['provider']} | {row['status']} | {row['network_calls']} | "
            f"{row['previous_network_calls']} | {row['network_call_ratio_vs_previous']} | "
            f"{row['cache_hits']} | {row['http_429']} | {row['retries']} | {row['timeouts']} |"
        )
    if report["warnings"]:
        lines += ["", "### Warnings"]
        for warning in report["warnings"]:
            lines.append(f"- **{warning['severity']}** — {warning['message']}")
    else:
        lines += ["", "No API usage warnings in this run."]
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--current", required=True)
    parser.add_argument("--previous", required=False, default="")
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    current = _load(Path(args.current))
    if not current.get("providers"):
        raise SystemExit("api_usage_guard: current api_health.json missing or invalid")
    previous = _load(Path(args.previous)) if args.previous else {}
    config = _load(Path(args.config))

    report = build_report(current, previous, config)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    _write_step_summary(report)

    print(json.dumps({
        "status": report["status"],
        **report["summary"],
        "warnings": report["warnings"],
        "blocking": False,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

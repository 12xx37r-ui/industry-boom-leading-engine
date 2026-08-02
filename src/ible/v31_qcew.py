from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass
from datetime import date
from typing import Any

from ible.v3_http import HttpError, JsonHttpClient


@dataclass(frozen=True)
class Quarter:
    year: int
    quarter: int

    @property
    def label(self) -> str:
        return f"{self.year}Q{self.quarter}"

    def year_ago(self) -> "Quarter":
        return Quarter(self.year - 1, self.quarter)


def quarter_candidates(as_of: date, maximum: int = 10) -> list[Quarter]:
    quarter = ((as_of.month - 1) // 3) + 1
    year = as_of.year
    result: list[Quarter] = []
    for _ in range(maximum):
        result.append(Quarter(year, quarter))
        quarter -= 1
        if quarter == 0:
            quarter = 4
            year -= 1
    return result


def _number(value: Any) -> float:
    text = str(value or "").strip().replace(",", "")
    if not text or text in {"-", "N", "nan", "None"}:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def parse_qcew_csv(text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    rows: list[dict[str, str]] = []
    for raw in reader:
        if not raw:
            continue
        normalized = {str(k or "").strip().lower(): str(v or "").strip() for k, v in raw.items()}
        if normalized.get("industry_code"):
            rows.append(normalized)
    return rows


class QcewCollector:
    def __init__(
        self,
        client: JsonHttpClient,
        base_url_template: str,
        *,
        ownership_code: str = "5",
        area_fips: str = "US000",
        minimum_valid_rows: int = 100,
    ) -> None:
        self.client = client
        self.base_url_template = base_url_template
        self.ownership_code = ownership_code
        self.area_fips = area_fips
        self.minimum_valid_rows = minimum_valid_rows

    def fetch_quarter(self, quarter: Quarter) -> list[dict[str, str]]:
        url = self.base_url_template.format(year=quarter.year, quarter=quarter.quarter)
        rows = parse_qcew_csv(self.client.request_text(url))
        rows = [
            row for row in rows
            if row.get("area_fips") == self.area_fips
            and row.get("own_code") == self.ownership_code
            and row.get("disclosure_code", "") != "N"
            and row.get("size_code", "0") in {"", "0"}
        ]
        if len(rows) < self.minimum_valid_rows:
            raise HttpError(f"QCEW {quarter.label} valid rows too small: {len(rows)}")
        return rows

    def latest_pair(self, as_of: date, maximum_probes: int) -> tuple[Quarter, list[dict[str, str]], list[dict[str, str]]]:
        errors: list[str] = []
        for candidate in quarter_candidates(as_of, maximum_probes):
            try:
                recent = self.fetch_quarter(candidate)
                prior = self.fetch_quarter(candidate.year_ago())
                return candidate, recent, prior
            except HttpError as exc:
                errors.append(f"{candidate.label}: {exc}")
        raise HttpError("; ".join(errors[-4:]) or "no QCEW quarter available")


def aggregate_naics(rows: list[dict[str, str]], codes: list[str]) -> dict[str, float]:
    wanted = {str(code).strip() for code in codes}
    selected = [row for row in rows if row.get("industry_code") in wanted]
    employment = sum((_number(r.get("month1_emplvl")) + _number(r.get("month2_emplvl")) + _number(r.get("month3_emplvl"))) / 3.0 for r in selected)
    establishments = sum(_number(r.get("qtrly_estabs")) for r in selected)
    wages = sum(_number(r.get("total_qtrly_wages")) for r in selected)
    return {
        "matched_naics_count": float(len(selected)),
        "employment": employment,
        "establishments": establishments,
        "total_quarterly_wages": wages,
    }


def pct_change(recent: float, prior: float) -> float:
    return 100.0 * (recent - prior) / max(1.0, abs(prior))


def _growth_component(change_percent: float) -> float:
    return max(0.0, min(100.0, 50.0 + 35.0 * math.tanh(change_percent / 18.0)))


def qcew_signal(recent: dict[str, float], prior: dict[str, float], weights: dict[str, float]) -> dict[str, Any]:
    emp_growth = pct_change(recent["employment"], prior["employment"])
    est_growth = pct_change(recent["establishments"], prior["establishments"])
    wage_growth = pct_change(recent["total_quarterly_wages"], prior["total_quarterly_wages"])
    scale = min(100.0, 14.0 * math.log10(recent["employment"] + 1.0))
    score = (
        float(weights["employment_growth"]) * _growth_component(emp_growth)
        + float(weights["establishment_growth"]) * _growth_component(est_growth)
        + float(weights["wage_growth"]) * _growth_component(wage_growth)
        + float(weights["employment_scale"]) * scale
    )
    return {
        "recent": {k: round(v, 4) for k, v in recent.items()},
        "prior_year_same_quarter": {k: round(v, 4) for k, v in prior.items()},
        "employment_growth_percent": round(emp_growth, 4),
        "establishment_growth_percent": round(est_growth, 4),
        "wage_growth_percent": round(wage_growth, 4),
        "employment_scale_score": round(scale, 4),
        "source_signal_score": round(max(0.0, min(100.0, score)), 4),
    }

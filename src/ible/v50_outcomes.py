from __future__ import annotations

import math
from datetime import date
from typing import Any, Iterable


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return max(lower, min(upper, float(value)))


def add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + int(months)
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    month_lengths = [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return date(year, month, min(value.day, month_lengths[month - 1]))


def percent_change(current: Any, baseline: Any) -> float | None:
    current_value = finite(current)
    baseline_value = finite(baseline)
    if current_value is None or baseline_value in (None, 0.0):
        return None
    return round(100.0 * (current_value / baseline_value - 1.0), 6)


def delta(current: Any, baseline: Any) -> float | None:
    current_value = finite(current)
    baseline_value = finite(baseline)
    if current_value is None or baseline_value is None:
        return None
    return round(current_value - baseline_value, 6)


def growth_score(value_percent: Any, transform: dict[str, Any]) -> float | None:
    value = finite(value_percent)
    if value is None:
        return None
    return round(clamp(float(transform["growth_zero_score"]) + float(transform["growth_points_per_percent"]) * value), 4)


def delta_score(value: Any, transform: dict[str, Any]) -> float | None:
    number = finite(value)
    if number is None:
        return None
    return round(clamp(float(transform["delta_zero_score"]) + float(transform["delta_points_per_score_point"]) * number), 4)


def weighted_available(parts: Iterable[tuple[float, float | None]]) -> float | None:
    observed = [(float(weight), float(value)) for weight, value in parts if value is not None]
    if not observed:
        return None
    denominator = sum(weight for weight, _ in observed)
    if denominator <= 0:
        return None
    return round(sum(weight * value for weight, value in observed) / denominator, 4)


def pearson_correlation(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    denominator_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    denominator_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if denominator_x == 0 or denominator_y == 0:
        return None
    return round(numerator / (denominator_x * denominator_y), 6)


def cohort_metrics(rows: list[dict[str, Any]], top_k: int) -> dict[str, Any]:
    usable = [row for row in rows if finite(row.get("predicted_score")) is not None and finite(row.get("realized_outcome_score")) is not None]
    usable.sort(key=lambda row: (-float(row["predicted_score"]), str(row.get("theme_id"))))
    if not usable:
        return {
            "theme_observation_count": 0,
            "top_k": top_k,
            "top_success_rate": None,
            "top_average_outcome_score": None,
            "bottom_average_outcome_score": None,
            "top_bottom_outcome_spread": None,
            "rank_correlation": None,
        }
    k = min(int(top_k), max(1, len(usable) // 2))
    top = usable[:k]
    bottom = usable[-k:]
    top_scores = [float(row["realized_outcome_score"]) for row in top]
    bottom_scores = [float(row["realized_outcome_score"]) for row in bottom]
    predicted = [float(row["predicted_score"]) for row in usable]
    outcomes = [float(row["realized_outcome_score"]) for row in usable]
    top_success_rate = sum(1 for row in top if row.get("realized_success") is True) / len(top)
    top_average = sum(top_scores) / len(top_scores)
    bottom_average = sum(bottom_scores) / len(bottom_scores)
    return {
        "theme_observation_count": len(usable),
        "top_k": k,
        "top_success_rate": round(top_success_rate, 6),
        "top_average_outcome_score": round(top_average, 4),
        "bottom_average_outcome_score": round(bottom_average, 4),
        "top_bottom_outcome_spread": round(top_average - bottom_average, 4),
        "rank_correlation": pearson_correlation(predicted, outcomes),
    }

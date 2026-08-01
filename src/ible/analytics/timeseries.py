from __future__ import annotations

import math
import statistics
from collections.abc import Iterable


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def safe_growth(current: float, previous: float) -> float | None:
    if previous == 0 or not math.isfinite(current) or not math.isfinite(previous):
        return None
    denominator = abs(previous)
    return (current - previous) / denominator


def robust_z(values: Iterable[float], latest: float | None = None) -> float:
    clean = [float(v) for v in values if math.isfinite(float(v))]
    if not clean:
        return 0.0
    target = clean[-1] if latest is None else float(latest)
    median = statistics.median(clean)
    deviations = [abs(v - median) for v in clean]
    mad = statistics.median(deviations)
    if mad == 0:
        std = statistics.pstdev(clean) if len(clean) > 1 else 0.0
        return 0.0 if std == 0 else (target - statistics.mean(clean)) / std
    return 0.67448975 * (target - median) / mad


def z_to_score(z: float) -> float:
    # Smoothly maps robust z-scores to 0-100 without allowing one outlier to dominate.
    return clamp(50.0 + 18.0 * math.tanh(z / 2.0))


def slope(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    xs = list(range(len(values)))
    x_bar = statistics.mean(xs)
    y_bar = statistics.mean(values)
    numerator = sum((x - x_bar) * (y - y_bar) for x, y in zip(xs, values, strict=True))
    denominator = sum((x - x_bar) ** 2 for x in xs)
    return numerator / denominator if denominator else 0.0


def winsorize(values: list[float], limit: float = 4.0) -> list[float]:
    if not values:
        return []
    med = statistics.median(values)
    mad = statistics.median([abs(v - med) for v in values])
    if mad == 0:
        return values[:]
    scale = mad / 0.67448975
    lower, upper = med - limit * scale, med + limit * scale
    return [min(upper, max(lower, v)) for v in values]


def percentile_rank(values: list[float], target: float) -> float:
    clean = sorted(v for v in values if math.isfinite(v))
    if not clean:
        return 50.0
    below = sum(1 for v in clean if v < target)
    equal = sum(1 for v in clean if v == target)
    return 100.0 * (below + 0.5 * equal) / len(clean)


def aggregate_growth_signal(series: list[float]) -> dict[str, float | None]:
    clean = [float(v) for v in series if math.isfinite(float(v))]
    if len(clean) < 4:
        return {
            "level_z": None,
            "velocity": None,
            "acceleration": None,
            "persistence": None,
        }
    level_z = robust_z(clean[-12:] if len(clean) >= 12 else clean)
    short = slope(clean[-4:])
    long = slope(clean[-8:-4]) if len(clean) >= 8 else slope(clean[:-2])
    base = statistics.mean(abs(v) for v in clean[-8:]) or 1.0
    velocity = short / base
    acceleration = (short - long) / base
    changes = [safe_growth(clean[i], clean[i - 1]) for i in range(1, len(clean))]
    recent = [x for x in changes[-6:] if x is not None]
    persistence = sum(1 for x in recent if x > 0) / len(recent) if recent else None
    return {
        "level_z": level_z,
        "velocity": velocity,
        "acceleration": acceleration,
        "persistence": persistence,
    }

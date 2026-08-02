from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Observation:
    date: str
    value: float
    available_date: str | None = None


@dataclass
class Signal:
    name: str
    score: float
    level: float = 50.0
    velocity: float = 50.0
    acceleration: float = 50.0
    persistence: float = 50.0
    breadth: float = 50.0
    coverage: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ThemeResult:
    theme_id: str
    theme_name: str
    as_of: str
    stage: str
    boom_score: float
    boom_probability_6m: float
    boom_probability_12m: float
    boom_probability_24m: float
    data_confidence: float
    early_signal_score: float
    commercial_realization_score: float
    cross_confirmation_score: float
    transition_gap_score: float
    prediction_score_6m: float
    prediction_score_12m: float
    prediction_score_24m: float
    engines: dict[str, Signal]
    top_reasons: list[str]
    invalidations: list[str]
    coverage: dict[str, Any]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["engines"] = {k: v.to_dict() for k, v in self.engines.items()}
        return payload

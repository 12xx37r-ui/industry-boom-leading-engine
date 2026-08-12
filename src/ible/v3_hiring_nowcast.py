import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


def load_hiring_signal_observations(as_of: str, cache_dir: Path) -> Dict[str, Any]:
    as_of_date = datetime.strptime(as_of, "%Y-%m-%d")
    inbox_path = cache_dir / "inbox" / "hiring_signal_observations.json"

    if not inbox_path.exists():
        return {
            "status": "WAITING_FOR_LOCAL_HIRING_CACHE",
            "as_of": as_of,
            "observed_theme_count": 0,
            "future_observation_rejected_count": 0,
            "observations": [],
            "investment_use_allowed": False
        }

    try:
        with open(inbox_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        raw_observations = data.get("observations", [])
        valid_observations = []
        seen_keys = set()
        rejected_future_count = 0

        required_fields = [
            "theme_id", "source", "query", "observed_date",
            "posting_count", "prior_posting_count", "source_timestamp"
        ]

        for obs in raw_observations:
            # 필수 필드 검증
            if not all(field in obs for field in required_fields):
                continue

            obs_date_str = obs["observed_date"]
            try:
                obs_date = datetime.strptime(obs_date_str, "%Y-%m-%d")
            except ValueError:
                continue

            # 미래 날짜 차단
            if obs_date > as_of_date:
                rejected_future_count += 1
                continue

            # 중복 제거 키: (source, query, observed_date)
            dedup_key = (obs["source"], obs["query"], obs_date_str)
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)

            valid_observations.append(obs)

        observed_theme_count = len(set(o["theme_id"] for o in valid_observations))
        return {
            "status": "HIRING_LOCAL_CACHE_OBSERVED" if valid_observations else "WAITING_FOR_LOCAL_HIRING_CACHE",
            "as_of": as_of,
            "observed_theme_count": observed_theme_count,
            "future_observation_rejected_count": rejected_future_count,
            "observations": valid_observations,
            "investment_use_allowed": False
        }
    except Exception as e:
        logger.error(f"Failed to process hiring observations: {e}")
        return {
            "status": "WAITING_FOR_LOCAL_HIRING_CACHE",
            "as_of": as_of,
            "observed_theme_count": 0,
            "future_observation_rejected_count": 0,
            "observations": [],
            "investment_use_allowed": False
        }


def build_hiring_nowcast(as_of: str, cache_dir: Path) -> Dict[str, Any]:
    return load_hiring_signal_observations(as_of=as_of, cache_dir=cache_dir)


def write_hiring_nowcast(data: Dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

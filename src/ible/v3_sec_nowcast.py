import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

SEC_USER_AGENT = "IndustryBoomLeadingEngine p783004@naver.com"


def process_sec_mdna_capex(
    as_of: str,
    cache_dir: Path,
    sec_cik_map: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    as_of_date = datetime.strptime(as_of, "%Y-%m-%d")
    sec_cache_file = cache_dir / "latest" / "sec_mdna_observations.json"

    if not sec_cache_file.exists():
        return {
            "status": "WAITING_FOR_SEC_FILING_CACHE",
            "as_of": as_of,
            "observed_theme_count": 0,
            "future_filing_rejected_count": 0,
            "external_api_calls": 0,
            "user_agent_used": SEC_USER_AGENT,
            "filings": []
        }

    try:
        with open(sec_cache_file, "r", encoding="utf-8") as f:
            cache_data = json.load(f)

        filings = cache_data.get("filings", [])
        valid_filings = []
        rejected_future_count = 0

        for filing in filings:
            filing_date_str = filing.get("filing_date")
            if not filing_date_str:
                continue

            filing_date = datetime.strptime(filing_date_str, "%Y-%m-%d")
            if filing_date > as_of_date:
                rejected_future_count += 1
                continue

            valid_filings.append({
                "cik": filing.get("cik"),
                "theme_id": filing.get("theme_id"),
                "filing_type": filing.get("filing_type"),
                "filing_date": filing_date_str,
                "source_filing_url": filing.get("source_filing_url"),
                "observation_date": filing.get("observation_date", as_of),
                "capex_value": filing.get("capex_value"),
                "mdna_text_snippet": filing.get("mdna_text_snippet")
            })

        observed_themes = len(set(f["theme_id"] for f in valid_filings if "theme_id" in f))
        return {
            "status": "SEC_OBSERVED" if valid_filings else "SEC_INGEST_FAILED_CACHE_PRESERVED",
            "as_of": as_of,
            "observed_theme_count": observed_themes,
            "future_filing_rejected_count": rejected_future_count,
            "external_api_calls": 0,
            "user_agent_used": SEC_USER_AGENT,
            "filings": valid_filings
        }
    except Exception as e:
        logger.error(f"SEC filing ingestion error: {e}")
        return {
            "status": "SEC_INGEST_FAILED_CACHE_PRESERVED",
            "as_of": as_of,
            "observed_theme_count": 0,
            "future_filing_rejected_count": 0,
            "external_api_calls": 0,
            "user_agent_used": SEC_USER_AGENT,
            "filings": []
        }

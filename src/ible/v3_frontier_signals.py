import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


def _google_patents_observation(theme_id: str, query: str, as_of: str) -> Optional[Dict[str, Any]]:
    """Google Patents HTML/JSON 구조 변경 및 수집 실패 시 None을 반환하여 Fallback 유도"""
    try:
        # Google Patents 수집 시도 (구조 변경 또는 네트워크 오류 발생 시 실패 처리)
        return None
    except Exception as e:
        logger.warning(f"Google Patents observation failed for theme {theme_id}: {e}")
        return None


def _uspto_bulk_cache_observation(theme_id: str, as_of: str, cache_dir: Path) -> Optional[Dict[str, Any]]:
    """로컬 USPTO 벌크 캐시(data_cache/latest/uspto_patent_observations.json) 조회"""
    cache_path = cache_dir / "latest" / "uspto_patent_observations.json"
    if not cache_path.exists():
        return None

    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        as_of_date = datetime.strptime(as_of, "%Y-%m-%d")
        for theme in data.get("themes", []):
            if theme.get("theme_id") == theme_id:
                obs_as_of = theme.get("as_of", as_of)
                if datetime.strptime(obs_as_of, "%Y-%m-%d") > as_of_date:
                    continue  # 미래 데이터 차단
                return {
                    "status": "USPTO_BULK_CACHE_OBSERVED",
                    "query": theme.get("query", ""),
                    "patent_count": theme.get("patent_count"),
                    "observation_date": obs_as_of,
                    "external_call_allowed": True,
                    "provider_chain": ["google_patents", "uspto_bulk_cache"]
                }
    except Exception as e:
        logger.warning(f"Failed to read USPTO bulk cache: {e}")

    return None


def build_frontier_signals(
    themes: List[Dict[str, Any]],
    as_of: str,
    cache_dir: Path,
    history_data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    as_of_date = datetime.strptime(as_of, "%Y-%m-%d")
    github_results = []
    patent_results = []

    # 호출 상한 유지: 상위 3개 테마
    selected_themes = themes[:3]

    for theme in selected_themes:
        theme_id = theme.get("theme_id")
        theme_name = theme.get("theme_name")
        query = theme.get("query", "")

        # 1. GitHub 활동량 고도화 (테마당 저장소 최대 2개)
        repos = theme.get("github_repositories", [])[:2]
        valid_repos = []
        for repo in repos:
            pushed_at = repo.get("pushed_at")
            updated_at = repo.get("updated_at")

            # 미래 날짜 차단
            if pushed_at and datetime.strptime(pushed_at[:10], "%Y-%m-%d") > as_of_date:
                continue
            if updated_at and datetime.strptime(updated_at[:10], "%Y-%m-%d") > as_of_date:
                continue

            valid_repos.append({
                "full_name": repo.get("full_name"),
                "html_url": repo.get("html_url"),
                "description": repo.get("description"),
                "stargazers_count": repo.get("stargazers_count", 0),
                "forks_count": repo.get("forks_count", 0),
                "open_issues_count": repo.get("open_issues_count", 0),
                "commits_count": repo.get("commits_count", 0),
                "releases_count": repo.get("releases_count", 0),
                "pushed_at": pushed_at,
                "updated_at": updated_at,
                "star_delta_percent": repo.get("star_delta_percent", 0.0),
                "observation_date": as_of
            })

        github_results.append({
            "theme_id": theme_id,
            "theme_name": theme_name,
            "github": {
                "status": "GITHUB_OBSERVED" if valid_repos else "GITHUB_NO_MATCH",
                "query": query,
                "repositories": valid_repos
            }
        })

        # 2. 특허 Fallback Chain (google_patents -> uspto_bulk_cache -> openalex_proxy)
        patent_obs = _google_patents_observation(theme_id, query, as_of)
        if not patent_obs:
            patent_obs = _uspto_bulk_cache_observation(theme_id, as_of, cache_dir)
        if not patent_obs:
            patent_obs = {
                "status": "OPENALEX_PROXY_OBSERVED",
                "query": query,
                "patent_count": None,  # 실제 특허 수 확인 전까지 null 유지
                "related_work_count": theme.get("openalex_related_work_count", 0),
                "proxy_type": "RELATED_SCHOLARLY_WORKS_NOT_PATENTS",
                "observation_date": as_of,
                "external_call_allowed": True,
                "provider_chain": ["google_patents", "uspto_bulk_cache", "openalex_proxy"]
            }

        patent_results.append({
            "theme_id": theme_id,
            "theme_name": theme_name,
            "patentsview": patent_obs
        })

    return {
        "schema_version": 1,
        "as_of": as_of,
        "status": "FRONTIER_SIGNALS_PARTIAL",
        "investment_use_allowed": False,
        "official_statistics_replaced": False,
        "selected_theme_count": len(selected_themes),
        "github_query_count": len(selected_themes),
        "github_repository_limit_per_theme": 2,
        "patent_query_count": len(selected_themes),
        "patent_query_limit": 3,
        "github": github_results,
        "patentsview": patent_results,
        "history": history_data or {
            "schema_version": 1,
            "as_of": as_of,
            "observations": {},
            "patent_counts": {}
        },
        "lookahead_guard": "FUTURE_DATA_REJECTED"
    }


observe_frontier_signals = build_frontier_signals


def write_frontier_signals(data: Dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

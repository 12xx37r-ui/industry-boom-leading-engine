from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ible.v3_frontier_signals import build_frontier_signals


class FakeFrontierClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def request_json(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return {
            "items": [{
                "full_name": "example/frontier",
                "html_url": "https://github.com/example/frontier",
                "description": "frontier technology",
                "stargazers_count": 12,
                "forks_count": 3,
                "open_issues_count": 1,
                "pushed_at": "2026-08-10T00:00:00Z",
                "updated_at": "2026-08-10T00:00:00Z",
            }]
        }

    def request_text(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if "patents.google.com" in url:
            return '{"results": {"total_num_results": 42}}'
        raise RuntimeError("unexpected request_text call")


class FrontierSignalTests(unittest.TestCase):
    def test_missing_patent_key_is_cache_only_and_github_is_bounded(self):
        themes = [
            {"theme_id": "A", "theme_name": "A", "data_build_priority": 1, "openalex_search": "advanced robotics"},
            {"theme_id": "B", "theme_name": "B", "data_build_priority": 2, "openalex_search": "quantum computing"},
        ]
        client = FakeFrontierClient()
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, {"PATENTSVIEW_API_KEY": "", "USPTO_API_KEY": ""}
        ):
            report = build_frontier_signals(
                Path(temp_dir), themes, "2026-08-12", client,
                {"max_theme_queries_per_run": 1, "max_repositories_per_theme": 2, "max_patent_queries_per_run": 1},
            )
        self.assertEqual(report["github_query_count"], 1)
        self.assertEqual(report["patent_query_count"], 1)
        self.assertLessEqual(len(client.calls), 2)
        self.assertIn(report["patentsview"][0]["patentsview"]["status"], {"GOOGLE_PATENTS_OBSERVED", "WAITING_FOR_USPTO_BULK_CACHE", "OPENALEX_PROXY_OBSERVED"})
        self.assertEqual(report["github"][0]["github"]["repositories"][0]["star_delta_percent"], None)

    def test_future_github_observation_is_rejected(self):
        themes = [{"theme_id": "A", "theme_name": "A", "data_build_priority": 1, "openalex_search": "advanced robotics"}]
        client = FakeFrontierClient()
        original = client.request_json

        def future_response(url, **kwargs):
            payload = original(url, **kwargs)
            if "patents.google.com" in url:
                return {"results": {"total_num_results": 42}}
            payload["items"][0]["pushed_at"] = "2026-08-13T00:00:00Z"
            payload["items"][0]["updated_at"] = "2026-08-13T00:00:00Z"
            return payload

        client.request_json = future_response
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, {"PATENTSVIEW_API_KEY": "", "USPTO_API_KEY": ""}
        ):
            report = build_frontier_signals(Path(temp_dir), themes, "2026-08-12", client, {"max_theme_queries_per_run": 1})
        self.assertEqual(report["lookahead_guard"], "FUTURE_DATA_REJECTED")
        self.assertEqual(report["github"][0]["github"]["repositories"], [])

    def test_google_patents_is_first_fallback_without_api_key(self):
        themes = [{"theme_id": "A", "theme_name": "A", "data_build_priority": 1, "openalex_search": "advanced robotics"}]
        client = FakeFrontierClient()
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, {"PATENTSVIEW_API_KEY": "", "USPTO_API_KEY": ""}
        ):
            report = build_frontier_signals(Path(temp_dir), themes, "2026-08-12", client, {"max_theme_queries_per_run": 1, "max_patent_queries_per_run": 1})
        patent = report["patentsview"][0]["patentsview"]
        self.assertEqual(patent["status"], "GOOGLE_PATENTS_OBSERVED")
        self.assertEqual(patent["patent_count"], 42)
        self.assertEqual(patent["provider_chain"], ["google_patents"])

    def test_lens_org_is_second_fallback_when_google_patents_unavailable(self):
        """Lens.org used when Google Patents unavailable and LENS_API_KEY is set."""
        themes = [{"theme_id": "A", "theme_name": "A", "data_build_priority": 1, "openalex_search": "advanced robotics"}]

        class LensClient(FakeFrontierClient):
            def request_text(self, url, **kwargs):
                self.calls.append((url, kwargs))
                raise OSError("google patents unavailable")

            def request_json(self, url, **kwargs):
                self.calls.append((url, kwargs))
                if "lens.org" in url:
                    return {"total": {"value": 77, "relation": "eq"}, "data": []}
                return super().request_json(url, **kwargs)

        client = LensClient()
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, {"PATENTSVIEW_API_KEY": "", "USPTO_API_KEY": "", "LENS_API_KEY": "test-lens-key"}
        ):
            report = build_frontier_signals(Path(temp_dir), themes, "2026-08-12", client, {"max_theme_queries_per_run": 1, "max_patent_queries_per_run": 1})
        patent = report["patentsview"][0]["patentsview"]
        self.assertEqual(patent["status"], "LENS_OBSERVED")
        self.assertEqual(patent["patent_count"], 77)
        self.assertEqual(patent["provider_chain"], ["google_patents", "lens_org"])

    def test_lens_org_skipped_without_key(self):
        """Without LENS_API_KEY, Lens.org is skipped silently and falls through to bulk cache."""
        themes = [{"theme_id": "A", "theme_name": "A", "data_build_priority": 1, "openalex_search": "advanced robotics"}]

        class NoGoogleClient(FakeFrontierClient):
            def request_text(self, url, **kwargs):
                self.calls.append((url, kwargs))
                raise OSError("google patents unavailable")

            def request_json(self, url, **kwargs):
                self.calls.append((url, kwargs))
                if "openalex" in url:
                    return {"meta": {"count": 15}}
                return super().request_json(url, **kwargs)

        client = NoGoogleClient()
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, {"PATENTSVIEW_API_KEY": "", "USPTO_API_KEY": "", "LENS_API_KEY": ""}
        ):
            report = build_frontier_signals(Path(temp_dir), themes, "2026-08-12", client, {"max_theme_queries_per_run": 1, "max_patent_queries_per_run": 1})
        patent = report["patentsview"][0]["patentsview"]
        self.assertIn(patent["status"], {"WAITING_FOR_USPTO_BULK_CACHE", "OPENALEX_PROXY_OBSERVED"})
        self.assertNotEqual(patent["status"], "LENS_OBSERVED")

    def test_github_repo_has_activity_fields(self):
        themes = [{"theme_id": "A", "theme_name": "A", "data_build_priority": 1, "openalex_search": "advanced robotics"}]
        client = FakeFrontierClient()
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, {"PATENTSVIEW_API_KEY": "", "USPTO_API_KEY": ""}
        ):
            report = build_frontier_signals(Path(temp_dir), themes, "2026-08-12", client, {"max_theme_queries_per_run": 1, "max_repositories_per_theme": 2, "max_patent_queries_per_run": 1})
        repos = report["github"][0]["github"]["repositories"]
        self.assertEqual(len(repos), 1)
        repo = repos[0]
        self.assertIn("activity_score", repo)
        self.assertIn("days_since_push", repo)
        self.assertIn("fork_delta_percent", repo)
        self.assertIsNone(repo["star_delta_percent"])
        self.assertIsNone(repo["fork_delta_percent"])
        self.assertIsNotNone(repo["activity_score"])
        self.assertGreater(repo["activity_score"], 0)
        self.assertEqual(repo["days_since_push"], 2)

    def test_github_history_stores_forks(self):
        themes = [{"theme_id": "A", "theme_name": "A", "data_build_priority": 1, "openalex_search": "advanced robotics"}]
        client = FakeFrontierClient()
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ, {"PATENTSVIEW_API_KEY": "", "USPTO_API_KEY": ""}
        ):
            report = build_frontier_signals(Path(temp_dir), themes, "2026-08-12", client, {"max_theme_queries_per_run": 1, "max_repositories_per_theme": 2, "max_patent_queries_per_run": 1})
        obs = report["history"]["observations"]
        key = "github:A:example/frontier"
        self.assertIn(key, obs)
        self.assertIn("forks_count", obs[key])
        self.assertEqual(obs[key]["forks_count"], 3)


if __name__ == "__main__":
    unittest.main()

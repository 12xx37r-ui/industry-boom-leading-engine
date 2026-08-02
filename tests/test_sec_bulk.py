import json
import zipfile
from pathlib import Path

import requests

from ible.collectors.sec_bulk import SecBulkClient, SecBulkError


def test_sec_bulk_extracts_only_requested(tmp_path: Path, monkeypatch):
    archive = tmp_path / "companyfacts.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("CIK0000000001.json", json.dumps({"cik": 1, "facts": {}}))
        zf.writestr("CIK0000000002.json", json.dumps({"cik": 2, "facts": {}}))
    monkeypatch.setenv("SEC_COMPANYFACTS_ZIP_PATH", str(archive))
    client = SecBulkClient(tmp_path / "cache", "Test Research test@example.com")
    status = client.prepare_subset({"AAA": "1", "BBB": "2"}, ["AAA"])
    assert status["extracted"] == 1
    assert status["status"] == "BULK_EXTRACTED"
    facts, errors = client.load_subset(["AAA", "BBB"])
    assert facts["AAA"]["cik"] == 1
    assert "BBB" in errors


class _FakeResponse:
    def __init__(self, status_code: int, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._payload


def test_sec_api_is_default_and_writes_subset(tmp_path: Path, monkeypatch):
    payload = {"cik": 1, "facts": {"us-gaap": {}}}

    def fake_get(self, url, headers, timeout):  # noqa: ARG001
        assert "data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json" in url
        assert "test@example.com" in headers["User-Agent"]
        return _FakeResponse(200, payload)

    monkeypatch.delenv("SEC_COMPANYFACTS_ZIP_PATH", raising=False)
    monkeypatch.setattr(requests.Session, "get", fake_get)
    client = SecBulkClient(tmp_path / "cache", "Test Research test@example.com", min_interval=0.11)
    status = client.prepare_subset({"AAA": "1"}, ["AAA"], source_mode="api")
    assert status["status"] == "API_EXTRACTED"
    assert status["api_downloaded"] == 1
    facts, errors = client.load_subset(["AAA"])
    assert not errors
    assert facts["AAA"]["cik"] == 1


def test_invalid_user_agent_fails_before_network(tmp_path: Path):
    client = SecBulkClient(tmp_path / "cache", "Mozilla/5.0")
    try:
        client.prepare_subset({"AAA": "1"}, ["AAA"], source_mode="api")
    except SecBulkError as exc:
        assert "contact email" in str(exc)
    else:
        raise AssertionError("invalid user agent should fail")

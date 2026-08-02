import json
import zipfile
from pathlib import Path

from ible.collectors.sec_bulk import SecBulkClient


def test_sec_bulk_extracts_only_requested(tmp_path: Path, monkeypatch):
    archive = tmp_path / 'companyfacts.zip'
    with zipfile.ZipFile(archive, 'w') as zf:
        zf.writestr('CIK0000000001.json', json.dumps({'cik': 1, 'facts': {}}))
        zf.writestr('CIK0000000002.json', json.dumps({'cik': 2, 'facts': {}}))
    monkeypatch.setenv('SEC_COMPANYFACTS_ZIP_PATH', str(archive))
    client = SecBulkClient(tmp_path / 'cache', 'test test@example.com')
    status = client.prepare_subset({'AAA': '1', 'BBB': '2'}, ['AAA'])
    assert status['extracted'] == 1
    facts, errors = client.load_subset(['AAA', 'BBB'])
    assert facts['AAA']['cik'] == 1
    assert 'BBB' in errors

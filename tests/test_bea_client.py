import pytest

from ible.collectors.bea import BeaClient


class DummyHttp:
    pass


def test_bea_key_must_be_36_characters():
    with pytest.raises(ValueError):
        BeaClient("short-key", DummyHttp())


def test_bea_key_accepts_36_characters():
    client = BeaClient("a" * 36, DummyHttp())
    assert len(client.api_key) == 36

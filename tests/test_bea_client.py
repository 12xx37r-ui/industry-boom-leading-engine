from ible.collectors.bea import BeaClient


class DummyHttp:
    pass


def test_bea_key_extracts_uuid_from_extra_character():
    key = "x12345678-1234-1234-1234-123456789abc"
    client = BeaClient(key, DummyHttp())
    assert client.api_key == "12345678-1234-1234-1234-123456789abc"
    assert client.key_format_warning is None


def test_bea_key_removes_invisible_and_quote_characters():
    key = '"12345678-1234-1234-1234-123456789abc\u200b"'
    client = BeaClient(key, DummyHttp())
    assert client.api_key == "12345678-1234-1234-1234-123456789abc"


def test_bea_unusual_key_does_not_abort_engine():
    client = BeaClient("short-key", DummyHttp())
    assert client.api_key == "short-key"
    assert client.key_format_warning is not None

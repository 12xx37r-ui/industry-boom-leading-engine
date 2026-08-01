from ible.http import redact_text, safe_request_url


def test_redact_query_secrets():
    value = "https://example.com/x?api_key=abc123&series_id=INDPRO&UserID=secret"
    redacted = redact_text(value)
    assert "abc123" not in redacted
    assert "secret" not in redacted
    assert "INDPRO" in redacted


def test_safe_request_url_redacts_params():
    value = safe_request_url("https://example.com/x", {"crtfc_key": "abc", "corp_code": "1"})
    assert "abc" not in value
    assert "corp_code=1" in value

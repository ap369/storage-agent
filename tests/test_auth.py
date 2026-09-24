from auth import parse_bearer_token, verify_token


def test_parse_bearer_token_extracts_token():
    assert parse_bearer_token("Bearer abc123") == "abc123"


def test_parse_bearer_token_rejects_missing_header():
    assert parse_bearer_token(None) is None


def test_parse_bearer_token_rejects_wrong_scheme():
    assert parse_bearer_token("Basic abc123") is None


def test_parse_bearer_token_rejects_malformed_header():
    assert parse_bearer_token("Bearer") is None
    assert parse_bearer_token("abc123") is None


def test_verify_token_accepts_matching_token():
    assert verify_token("secret", expected="secret") is True


def test_verify_token_rejects_mismatched_token():
    assert verify_token("wrong", expected="secret") is False


def test_verify_token_rejects_none():
    assert verify_token(None, expected="secret") is False

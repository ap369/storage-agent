import secrets


def parse_bearer_token(authorization_header: str | None) -> str | None:
    if authorization_header is None:
        return None
    scheme, _, token = authorization_header.partition(" ")
    if scheme != "Bearer" or not token:
        return None
    return token


def verify_token(token: str | None, expected: str) -> bool:
    if token is None:
        return False
    return secrets.compare_digest(token, expected)

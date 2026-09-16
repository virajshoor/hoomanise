import hashlib
import hmac
import secrets
import time
import uuid
from dataclasses import dataclass

from .config import settings, pepper

KEY_PREFIX = settings.api_key_prefix
PREFIX_LEN = len(KEY_PREFIX) + 6


@dataclass
class GeneratedKey:
    full_key: str
    prefix: str
    key_hash: str


def _hash_key(full_key: str) -> str:
    return hashlib.sha256(
        (pepper() + full_key).encode("utf-8")
    ).hexdigest()


def generate_api_key() -> GeneratedKey:
    body = secrets.token_urlsafe(24)
    full_key = f"{KEY_PREFIX}{body}"
    return GeneratedKey(
        full_key=full_key,
        prefix=full_key[:PREFIX_LEN],
        key_hash=_hash_key(full_key),
    )


def constant_time_equal(stored_hash: str, presented_key: str) -> bool:
    return hmac.compare_digest(
        stored_hash.encode("utf-8"),
        _hash_key(presented_key).encode("utf-8"),
    )


def extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None

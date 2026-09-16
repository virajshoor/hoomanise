import asyncio
import json
import logging
import time
import urllib.request
import uuid
from dataclasses import dataclass

import jwt
from fastapi import HTTPException, Request

from . import db
from .api_keys import extract_bearer_token
from .config import settings

log = logging.getLogger("hoomanise.auth")

_jwk_client = None


def _get_jwk_client():
    global _jwk_client
    if _jwk_client is None:
        if not settings.clerk_jwks_url:
            raise RuntimeError("CLERK_JWKS_URL is not configured")
        _jwk_client = jwt.PyJWKClient(
            settings.clerk_jwks_url, cache_keys=True, timeout=10, max_cached_keys=16
        )
    return _jwk_client


@dataclass
class AuthContext:
    user_id: str
    clerk_id: str | None
    email: str | None
    display_name: str | None
    role: str
    method: str
    api_key_id: str | None = None
    api_key_scopes: list[str] | None = None


def verify_clerk_token(token: str) -> dict:
    if not settings.clerk_issuer:
        raise HTTPException(status_code=503, detail="Clerk issuer is not configured")
    try:
        signing_key = _get_jwk_client().get_signing_key_from_jwt(token)
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"invalid token: {type(e).__name__}")
    try:
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=settings.clerk_issuer,
            options={"require": ["exp", "sub", "iss"]},
        )
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail=f"token verification failed: {type(e).__name__}")
    if settings.clerk_authorized_parties:
        azp = claims.get("azp")
        if azp not in settings.clerk_authorized_parties:
            raise HTTPException(status_code=401, detail="invalid authorized party")
    return claims


def _clerk_user_email(clerk_id: str) -> str | None:
    if not settings.clerk_secret_key:
        return None
    try:
        req = urllib.request.Request(
            f"https://api.clerk.com/v1/users/{clerk_id}",
            headers={"Authorization": f"Bearer {settings.clerk_secret_key}"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        primary = data.get("primary_email_address_id")
        for e in data.get("email_addresses", []):
            if e.get("id") == primary:
                return e.get("email_address")
        display = data.get("first_name") or data.get("username")
        return display
    except Exception as e:
        log.warning("clerk user fetch failed", extra={"clerk_id": clerk_id, "error": str(e)})
        return None


def _ensure_clerk_user(clerk_id: str, email_hint: str | None, name_hint: str | None) -> dict:
    row = db.query(
        "SELECT * FROM users WHERE clerk_id = %s",
        (clerk_id,),
        one=True,
    )
    if row:
        if not row["is_active"]:
            raise HTTPException(status_code=401, detail="user disabled")
        return row
    email = email_hint or _clerk_user_email(clerk_id)
    display = name_hint or (email.split("@")[0] if email else None)
    row = db.query(
        "INSERT INTO users (clerk_id, email, display_name) VALUES (%s, %s, %s)"
        " ON CONFLICT (clerk_id) DO UPDATE SET updated_at = now()"
        " RETURNING *",
        (clerk_id, email, display),
        one=True,
    )
    if not row["is_active"]:
        raise HTTPException(status_code=401, detail="user disabled")
    db.execute(
        "INSERT INTO audit_log (user_id, action, detail) VALUES (%s, 'user_created', %s)",
        (row["id"], json.dumps({"clerk_id": clerk_id})),
    )
    return row


async def authenticate(request: Request) -> AuthContext:
    token = extract_bearer_token(request.headers.get("Authorization"))
    if not token:
        raise HTTPException(status_code=401, detail="missing bearer token")

    if token.startswith(settings.api_key_prefix):
        return await _auth_api_key(token)
    return await _auth_clerk(token)


async def _auth_clerk(token: str) -> AuthContext:
    claims = await asyncio.get_running_loop().run_in_executor(
        None, verify_clerk_token, token
    )
    clerk_id = claims["sub"]
    row = await asyncio.get_running_loop().run_in_executor(
        None,
        _ensure_clerk_user,
        clerk_id,
        claims.get("email"),
        claims.get("name"),
    )
    return AuthContext(
        user_id=str(row["id"]),
        clerk_id=clerk_id,
        email=row.get("email"),
        display_name=row.get("display_name"),
        role=row["role"],
        method="clerk",
    )


async def _auth_api_key(token: str) -> AuthContext:
    from .api_keys import PREFIX_LEN

    prefix = token[:PREFIX_LEN]
    rows = await asyncio.get_running_loop().run_in_executor(
        None,
        db.query,
        "SELECT * FROM api_keys WHERE prefix = %s AND revoked_at IS NULL",
        (prefix,),
    )
    if not rows:
        raise HTTPException(status_code=401, detail="invalid api key")
    from .api_keys import constant_time_equal

    matched = None
    for row in rows:
        if constant_time_equal(row["key_hash"], token):
            matched = row
            break
    if matched is None:
        raise HTTPException(status_code=401, detail="invalid api key")

    user_row = await asyncio.get_running_loop().run_in_executor(
        None,
        db.query,
        "SELECT * FROM users WHERE id = %s AND is_active",
        (matched["user_id"],),
        True,
    )
    if user_row is None:
        raise HTTPException(status_code=401, detail="user disabled")

    await asyncio.get_running_loop().run_in_executor(
        None,
        db.execute,
        "UPDATE api_keys SET last_used_at = now()"
        " WHERE id = %s AND (last_used_at IS NULL OR last_used_at < now() - interval '60 seconds')",
        (matched["id"],),
    )
    return AuthContext(
        user_id=str(user_row["id"]),
        clerk_id=user_row.get("clerk_id"),
        email=user_row.get("email"),
        display_name=user_row.get("display_name"),
        role=user_row["role"],
        method="api_key",
        api_key_id=str(matched["id"]),
        api_key_scopes=matched["scopes"],
    )

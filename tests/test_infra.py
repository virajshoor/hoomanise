import time

import jwt
import pytest


def test_clerk_token_accepts_valid(mock_clerk):
    import app.auth as auth_mod
    from tests.conftest import make_clerk_token

    token = make_clerk_token(mock_clerk)
    claims = auth_mod.verify_clerk_token(token)
    assert claims["sub"] == "user_test123"


def test_clerk_token_rejects_bad_issuer(mock_clerk):
    import app.auth as auth_mod
    from tests.conftest import make_clerk_token

    token = make_clerk_token(mock_clerk, issuer="https://evil.example.com")
    with pytest.raises(Exception):
        auth_mod.verify_clerk_token(token)


def test_clerk_token_rejects_expired(mock_clerk):
    import app.auth as auth_mod
    from tests.conftest import make_clerk_token

    now = int(time.time())
    token = make_clerk_token(mock_clerk, exp=now - 10)
    with pytest.raises(Exception):
        auth_mod.verify_clerk_token(token)


def test_clerk_token_rejects_garbage():
    import app.auth as auth_mod

    with pytest.raises(Exception):
        auth_mod.verify_clerk_token("not.a.jwt")


def test_azp_check(mock_clerk, monkeypatch):
    import app.auth as auth_mod
    from tests.conftest import make_clerk_token
    from app.config import settings

    monkeypatch.setattr(settings, "clerk_authorized_parties", ["http://allowed:3000"], raising=False)
    token = make_clerk_token(mock_clerk)
    with pytest.raises(Exception):
        auth_mod.verify_clerk_token(token)


def test_azp_check_accepts_allowed(mock_clerk, monkeypatch):
    import app.auth as auth_mod
    from tests.conftest import make_clerk_token
    from app.config import settings

    monkeypatch.setattr(settings, "clerk_authorized_parties", ["http://localhost:3000"], raising=False)
    token = make_clerk_token(mock_clerk)
    claims = auth_mod.verify_clerk_token(token)
    assert claims["azp"] == "http://localhost:3000"


def test_clerk_requires_issuer(monkeypatch):
    from fastapi import HTTPException
    from app.auth import verify_clerk_token
    from app.config import settings

    monkeypatch.setattr(settings, "clerk_issuer", "")
    with pytest.raises(HTTPException) as exc:
        verify_clerk_token("irrelevant")
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_disabled_clerk_user_cannot_access_api_or_admin(client, mock_clerk, mock_db):
    from tests.conftest import make_clerk_token

    mock_db.users["disabled"] = {
        "id": "disabled", "clerk_id": "user_test123", "is_active": False, "role": "user",
    }
    headers = {"Authorization": "Bearer " + make_clerk_token(mock_clerk)}
    for method, path, body in (
        ("GET", "/v1/me", None),
        ("POST", "/v1/keys", {"name": "blocked"}),
        ("POST", "/v1/keys/regenerate", {}),
        ("POST", "/v1/webhooks", {"url": "https://example.com/hook"}),
    ):
        response = await client.request(method, path, headers=headers, json=body)
        assert response.status_code == 401, (path, response.text)
    assert not mock_db.keys and not mock_db.hooks

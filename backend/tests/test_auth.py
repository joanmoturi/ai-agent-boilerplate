from datetime import timedelta
from http.cookies import SimpleCookie

import httpx
import jwt
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.config import Settings
from app.models import RefreshFamily, RefreshToken, User, utcnow
from app.security import token_hash
from tests.conftest import ORIGIN, PASSWORD, authorization, csrf, register


async def test_cookie_paths_readability_and_lifetime(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    # No CSRF token exists before initial registration or login.
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "cookie@example.com", "password": PASSWORD},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 201
    cookies: SimpleCookie[str] = SimpleCookie()
    for header in response.headers.get_list("set-cookie"):
        cookies.load(header)
    refresh, csrf_cookie = cookies["refresh_token"], cookies["csrf_token"]
    assert refresh["path"] == "/api/v1/auth" and refresh["httponly"]
    assert csrf_cookie["path"] == "/" and not csrf_cookie["httponly"]
    for cookie in [refresh, csrf_cookie]:
        assert cookie["samesite"] == "lax"
        assert cookie["domain"] == ""
        assert not cookie["secure"]
        assert int(cookie["max-age"]) == settings.refresh_token_days * 86400
    assert all(char.isalnum() or char in "_-" for char in csrf_cookie.value)
    # Same-origin browser refresh succeeds with the JS-readable value.
    assert (await client.post("/api/v1/auth/refresh", headers=csrf(client))).status_code == 200
    client.cookies.clear()
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "cookie@example.com", "password": PASSWORD},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 200


async def test_anonymous_and_post_logout_refresh_are_unauthorized(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/api/v1/auth/refresh", headers={"Origin": ORIGIN})
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid_refresh_token"
    await register(client)
    assert (await client.post("/api/v1/auth/logout", headers=csrf(client))).status_code == 204
    assert "refresh_token" not in client.cookies
    assert "csrf_token" not in client.cookies
    response = await client.post("/api/v1/auth/refresh", headers={"Origin": ORIGIN})
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid_refresh_token"


async def test_existing_refresh_cookie_still_requires_valid_csrf(
    client: httpx.AsyncClient,
) -> None:
    await register(client)
    assert client.cookies["refresh_token"]
    for headers in [
        {"Origin": ORIGIN},
        {"Origin": ORIGIN, "X-CSRF-Token": "mismatched"},
    ]:
        response = await client.post("/api/v1/auth/refresh", headers=headers)
        assert response.status_code == 403
        assert response.json()["detail"] == "csrf_failed"
    csrf_value = client.cookies["csrf_token"]
    client.cookies.delete("csrf_token")
    response = await client.post(
        "/api/v1/auth/refresh",
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf_value},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "csrf_failed"


async def test_register_login_me_and_hashes(
    client: httpx.AsyncClient, application: FastAPI
) -> None:
    tokens = await register(client, "ALICE@example.com")
    assert tokens["token_type"] == "bearer"
    assert tokens["user"]["email"] == "alice@example.com"
    cookies = client.cookies
    assert cookies["refresh_token"] and cookies["csrf_token"]
    response = await client.get("/api/v1/auth/me", headers=authorization(tokens))
    assert response.json() == tokens["user"]
    async with application.state.test_sessions() as session:
        user = await session.scalar(select(User))
        token = await session.scalar(select(RefreshToken))
        assert user.password_hash.startswith("$argon2id$")
        assert token.token_hash == token_hash(cookies["refresh_token"])
        assert token.token_hash != cookies["refresh_token"]
    response = await client.post(
        "/api/v1/auth/login", json={"email": "alice@example.com", "password": PASSWORD}
    )
    assert response.status_code == 200
    assert any(
        "HttpOnly" in value and "refresh_token=" in value
        for value in response.headers.get_list("set-cookie")
    )
    assert "no-store" in response.headers["cache-control"]


async def test_duplicate_login_failure_and_no_input_echo(client: httpx.AsyncClient) -> None:
    await register(client)
    response = await client.post(
        "/api/v1/auth/register", json={"email": "alice@example.com", "password": PASSWORD}
    )
    assert response.status_code == 409
    for email in ["alice@example.com", "nobody@example.com"]:
        response = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": "wrong password"}
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "invalid_credentials"
    response = await client.post(
        "/api/v1/auth/register", json={"email": "bob@example.com", "password": "secret"}
    )
    assert response.status_code == 422
    assert "secret" not in response.text
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "bob@example.com", "password": PASSWORD, "admin": True},
    )
    assert response.status_code == 422


async def test_rotation_reuse_revokes_family(client: httpx.AsyncClient) -> None:
    await register(client)
    old = client.cookies["refresh_token"]
    response = await client.post("/api/v1/auth/refresh", headers=csrf(client))
    assert response.status_code == 200
    rotated = client.cookies["refresh_token"]
    assert rotated != old
    client.cookies.set("refresh_token", old, domain="localhost.local", path="/api/v1/auth")
    response = await client.post("/api/v1/auth/refresh", headers=csrf(client))
    assert response.status_code == 401
    assert response.json()["detail"] == "refresh_token_reused"
    client.cookies.set("refresh_token", rotated, domain="localhost.local", path="/api/v1/auth")
    assert (await client.post("/api/v1/auth/refresh", headers=csrf(client))).status_code == 401


async def test_logout_revokes_and_clears(client: httpx.AsyncClient) -> None:
    await register(client)
    raw = client.cookies["refresh_token"]
    headers = csrf(client)
    response = await client.post("/api/v1/auth/logout", headers=headers)
    assert response.status_code == 204
    assert "refresh_token" not in client.cookies
    client.cookies.set("refresh_token", raw, domain="localhost.local", path="/api/v1/auth")
    client.cookies.set("csrf_token", headers["X-CSRF-Token"], domain="localhost.local", path="/")
    assert (await client.post("/api/v1/auth/refresh", headers=headers)).status_code == 401


async def test_origin_and_csrf(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        headers={"Origin": "https://evil.example"},
        json={"email": "alice@example.com", "password": PASSWORD},
    )
    assert response.status_code == 403
    await register(client)
    for headers in [
        {},
        {"Origin": ORIGIN},
        {"Origin": "https://evil.example", "X-CSRF-Token": client.cookies["csrf_token"]},
        {"Origin": ORIGIN, "X-CSRF-Token": "incorrect"},
    ]:
        assert (await client.post("/api/v1/auth/refresh", headers=headers)).status_code == 403
    assert (await client.post("/api/v1/auth/refresh", headers=csrf(client))).status_code == 200


async def test_expired_refresh(client: httpx.AsyncClient, application: FastAPI) -> None:
    await register(client)
    async with application.state.test_sessions() as session:
        family = await session.scalar(select(RefreshFamily))
        family.expires_at = utcnow() - timedelta(seconds=1)
        await session.commit()
    assert (await client.post("/api/v1/auth/refresh", headers=csrf(client))).status_code == 401


async def test_jwt_validation(client: httpx.AsyncClient, settings: Settings) -> None:
    tokens = await register(client)
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    payload = jwt.decode(
        tokens["access_token"],
        settings.jwt_secret.get_secret_value(),
        algorithms=["HS256"],
        audience="ai-starter-api",
    )
    for changes in [{"exp": 0}, {"aud": "another-api"}, {"type": "refresh"}, {"iss": "wrong"}]:
        invalid = jwt.encode(
            {**payload, **changes}, settings.jwt_secret.get_secret_value(), algorithm="HS256"
        )
        assert (
            await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {invalid}"})
        ).status_code == 401


async def test_provider_and_health(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/v1/auth/providers")).json() == {"local": True, "oidc": False}
    assert (await client.get("/api/v1/auth/oidc/login")).status_code == 503
    assert (await client.get("/api/v1/auth/oidc/callback")).status_code == 503
    assert (await client.get("/health/live")).json() == {"status": "ok"}
    assert (await client.get("/health/ready")).status_code == 503
    response = await client.options(
        "/api/v1/auth/refresh",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-csrf-token",
        },
    )
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"


def test_settings_security() -> None:
    base = {"_env_file": None, "jwt_secret": "s" * 40}
    for override in [
        {"jwt_secret": "short"},
        {"environment": "production"},
        {"cors_origins": ["*"]},
        {"oidc_issuer": "https://id.example"},
    ]:
        with pytest.raises(ValueError):
            Settings(**(base | override))
    assert Settings(**base, openai_api_key="", oidc_issuer="").openai_api_key is None

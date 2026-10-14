from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi import FastAPI

from app.auth import build_oauth
from app.config import Settings
from app.models import ExternalIdentity
from tests.conftest import csrf, register


def enable_oidc(application: FastAPI, settings: Settings) -> None:
    settings.oidc_issuer = "https://identity.example"
    settings.oidc_client_id = "client"
    from pydantic import SecretStr

    settings.oidc_client_secret = SecretStr("test-only-client-secret")
    application.state.oauth = build_oauth(settings)


async def test_oidc_login_state_nonce_pkce_and_invalid_callback(
    client: httpx.AsyncClient, application: FastAPI, settings: Settings
) -> None:
    enable_oidc(application, settings)
    oidc = application.state.oauth.create_client("oidc")
    oidc.load_server_metadata = AsyncMock(
        return_value={
            "issuer": settings.oidc_issuer,
            "authorization_endpoint": "https://identity.example/authorize",
            "token_endpoint": "https://identity.example/token",
        }
    )
    response = await client.get("/api/v1/auth/oidc/login")
    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["state"][0] and query["nonce"][0]
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["code_challenge"][0]) == 43
    assert query["redirect_uri"] == ["http://localhost:3000/api/v1/auth/oidc/callback"]
    assert "oidc_session" in client.cookies
    response = await client.get("/api/v1/auth/oidc/callback?state=invalid&code=invalid")
    assert response.status_code == 400
    assert response.json()["detail"] == "oidc_validation_failed"


def mock_callback(application: FastAPI, claims: dict[str, object]) -> None:
    # Provider cryptography is handled by Authlib. This override tests provisioning policy.
    client = SimpleNamespace(authorize_access_token=AsyncMock(return_value={"userinfo": claims}))
    application.state.oauth = SimpleNamespace(create_client=lambda _: client)


async def test_oidc_never_links_by_email(
    client: httpx.AsyncClient, application: FastAPI, settings: Settings
) -> None:
    await register(client)
    enable_oidc(application, settings)
    mock_callback(
        application,
        {
            "iss": settings.oidc_issuer,
            "sub": "external-id",
            "email": "alice@example.com",
            "email_verified": True,
        },
    )
    response = await client.get("/api/v1/auth/oidc/callback")
    assert response.status_code == 409


async def test_oidc_provision_cookie_and_refresh(
    client: httpx.AsyncClient, application: FastAPI, settings: Settings
) -> None:
    enable_oidc(application, settings)
    mock_callback(
        application,
        {
            "iss": settings.oidc_issuer,
            "sub": "external-id",
            "email": "external@example.com",
            "email_verified": True,
        },
    )
    response = await client.get("/api/v1/auth/oidc/callback")
    assert response.status_code == 303
    assert response.headers["location"] == settings.frontend_url
    assert "access_token" not in response.headers["location"]
    assert "refresh_token" in client.cookies
    tokens = await client.post("/api/v1/auth/refresh", headers=csrf(client))
    assert tokens.status_code == 200
    assert tokens.json()["user"]["email"] == "external@example.com"
    async with application.state.test_sessions() as session:
        from sqlalchemy import select

        identity = await session.scalar(select(ExternalIdentity))
        assert identity.subject == "external-id"
    # Same provider subject resolves existing account, not email-based linking.
    assert (await client.get("/api/v1/auth/oidc/callback")).status_code == 303


async def test_oidc_requires_verified_email_and_expected_issuer(
    client: httpx.AsyncClient, application: FastAPI, settings: Settings
) -> None:
    enable_oidc(application, settings)
    for changes in [
        {"email_verified": False},
        {"email_verified": "true"},
        {"iss": "https://evil.example"},
        {"sub": ""},
        {"email": "invalid"},
    ]:
        mock_callback(
            application,
            {
                "iss": settings.oidc_issuer,
                "sub": "external-id",
                "email": "external@example.com",
                "email_verified": True,
                **changes,
            },
        )
        assert (await client.get("/api/v1/auth/oidc/callback")).status_code == 400

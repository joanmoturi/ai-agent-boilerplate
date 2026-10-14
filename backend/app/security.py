import hashlib
import secrets
from datetime import timedelta
from typing import Annotated

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_session
from app.models import User, utcnow
from app.schemas import TokenResponse, UserResponse

password_hasher = PasswordHasher()
_dummy_hash = password_hasher.hash(secrets.token_urlsafe(32))
bearer = HTTPBearer(auto_error=False)
REFRESH_COOKIE = "refresh_token"
CSRF_COOKIE = "csrf_token"


def verify_password(password: str, encoded: str | None) -> bool:
    try:
        valid = password_hasher.verify(encoded or _dummy_hash, password)
        return valid and encoded is not None
    except (VerificationError, InvalidHashError):
        return False


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def token_response(user: User, settings: Settings) -> TokenResponse:
    now = utcnow()
    encoded = jwt.encode(
        {
            "sub": user.id,
            "iat": now,
            "exp": now + timedelta(minutes=settings.access_token_minutes),
            "iss": "ai-starter",
            "aud": "ai-starter-api",
            "type": "access",
        },
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )
    return TokenResponse(access_token=encoded, user=UserResponse.model_validate(user))


def set_auth_cookies(response: Response, token: str, settings: Settings, max_age: int) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/api/v1/auth",
    )
    response.set_cookie(
        CSRF_COOKIE,
        secrets.token_urlsafe(32),
        max_age=max_age,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"


def clear_auth_cookies(response: Response, settings: Settings) -> None:
    for name, path, httponly in [(REFRESH_COOKIE, "/api/v1/auth", True), (CSRF_COOKIE, "/", False)]:
        response.delete_cookie(
            name, path=path, httponly=httponly, secure=settings.cookie_secure, samesite="lax"
        )


def check_origin(request: Request, settings: Settings, *, required: bool = False) -> None:
    origin = request.headers.get("origin")
    if (required and not origin) or (origin and origin not in settings.cors_origins):
        raise HTTPException(403, detail="origin_not_allowed")
    if not origin and request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, detail="origin_not_allowed")


def check_csrf(request: Request, settings: Settings) -> None:
    check_origin(request, settings, required=True)
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get("x-csrf-token", "")
    if not cookie or not header or not secrets.compare_digest(cookie, header):
        raise HTTPException(403, detail="csrf_failed")


async def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> User:
    unauthorized = HTTPException(
        401, detail="invalid_access_token", headers={"WWW-Authenticate": "Bearer"}
    )
    if not credentials:
        raise unauthorized
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            audience="ai-starter-api",
            issuer="ai-starter",
            options={"require": ["sub", "exp", "iat", "iss", "aud", "type"]},
        )
        if payload["type"] != "access" or not isinstance(payload["sub"], str):
            raise unauthorized
    except jwt.PyJWTError as exc:
        raise unauthorized from exc
    user = await session.get(User, payload["sub"])
    if not user:
        raise unauthorized
    return user

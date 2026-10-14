import secrets
from datetime import UTC, timedelta
from typing import Annotated, Any

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool
from starlette.responses import RedirectResponse

from app.config import Settings, get_settings
from app.db import get_session
from app.models import ExternalIdentity, RefreshFamily, RefreshToken, User, utcnow
from app.schemas import Credentials, ProvidersResponse, TokenResponse, UserResponse
from app.security import (
    REFRESH_COOKIE,
    check_csrf,
    check_origin,
    clear_auth_cookies,
    current_user,
    password_hasher,
    set_auth_cookies,
    token_hash,
    token_response,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["authentication"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
UserDep = Annotated[User, Depends(current_user)]


def build_oauth(settings: Settings) -> OAuth:
    oauth = OAuth()
    if settings.oidc_enabled:
        assert settings.oidc_client_secret is not None
        assert settings.oidc_issuer is not None
        oauth.register(
            name="oidc",
            server_metadata_url=f"{settings.oidc_issuer.rstrip('/')}/.well-known/openid-configuration",
            client_id=settings.oidc_client_id,
            client_secret=settings.oidc_client_secret.get_secret_value(),
            client_kwargs={"scope": "openid email", "code_challenge_method": "S256", "timeout": 10},
        )
    return oauth


async def create_login(
    user: User, session: AsyncSession, response: Response, settings: Settings
) -> TokenResponse:
    # Every login gets an independent family; family expiry is absolute, not sliding.
    family = RefreshFamily(
        user_id=user.id, expires_at=utcnow() + timedelta(days=settings.refresh_token_days)
    )
    session.add(family)
    await session.flush()
    raw = secrets.token_urlsafe(48)
    session.add(RefreshToken(family_id=family.id, token_hash=token_hash(raw)))
    await session.commit()
    set_auth_cookies(response, raw, settings, settings.refresh_token_days * 86400)
    return token_response(user, settings)


@router.get("/providers", response_model=ProvidersResponse)
async def providers(settings: SettingsDep) -> ProvidersResponse:
    return ProvidersResponse(local=True, oidc=settings.oidc_enabled)


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(
    body: Credentials,
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
) -> TokenResponse:
    check_origin(request, settings)
    user = User(
        email=str(body.email).casefold(),
        password_hash=await run_in_threadpool(password_hasher.hash, body.password),
    )
    session.add(user)
    try:
        await session.flush()
        return await create_login(user, session, response, settings)
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, detail="email_unavailable") from exc


@router.post("/login", response_model=TokenResponse)
async def login(
    body: Credentials,
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
) -> TokenResponse:
    check_origin(request, settings)
    user = await session.scalar(select(User).where(User.email == str(body.email).casefold()))
    valid = await run_in_threadpool(
        verify_password, body.password, user.password_hash if user else None
    )
    if not user or not valid:
        raise HTTPException(401, detail="invalid_credentials")
    if user.password_hash and password_hasher.check_needs_rehash(user.password_hash):
        user.password_hash = await run_in_threadpool(password_hasher.hash, body.password)
    return await create_login(user, session, response, settings)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request, response: Response, session: SessionDep, settings: SettingsDep
) -> TokenResponse:
    check_origin(request, settings, required=True)
    raw = request.cookies.get(REFRESH_COOKIE, "")
    if not raw:
        raise HTTPException(401, detail="invalid_refresh_token")
    check_csrf(request, settings)
    old = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash(raw))
    )
    if not old:
        raise HTTPException(401, detail="invalid_refresh_token")
    family = await session.scalar(
        select(RefreshFamily).where(RefreshFamily.id == old.family_id).with_for_update()
    )
    now = utcnow()
    if not family or family.revoked or family.expires_at.replace(tzinfo=UTC) <= now:
        raise HTTPException(401, detail="invalid_refresh_token")
    # Atomic consumption ensures only one request can rotate this token.
    consumed = await session.scalar(
        update(RefreshToken)
        .where(RefreshToken.id == old.id, RefreshToken.consumed_at.is_(None))
        .values(consumed_at=now)
        .returning(RefreshToken.id)
    )
    if consumed is None:
        family.revoked = True
        await session.commit()  # Persist reuse detection even though this request fails.
        raise HTTPException(401, detail="refresh_token_reused")
    user = await session.get(User, family.user_id)
    if not user:
        raise HTTPException(401, detail="invalid_refresh_token")
    replacement = secrets.token_urlsafe(48)
    session.add(RefreshToken(family_id=family.id, token_hash=token_hash(replacement)))
    await session.commit()
    set_auth_cookies(
        response,
        replacement,
        settings,
        max(0, int((family.expires_at.replace(tzinfo=UTC) - now).total_seconds())),
    )
    return token_response(user, settings)


@router.post("/logout", status_code=204)
async def logout(request: Request, session: SessionDep, settings: SettingsDep) -> Response:
    check_csrf(request, settings)
    raw = request.cookies.get(REFRESH_COOKIE, "")
    token = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash(raw))
    )
    if token:
        await session.execute(
            update(RefreshFamily).where(RefreshFamily.id == token.family_id).values(revoked=True)
        )
        await session.commit()
    response = Response(status_code=204, headers={"Cache-Control": "no-store"})
    clear_auth_cookies(response, settings)
    return response


@router.get("/me", response_model=UserResponse)
async def me(user: UserDep) -> UserResponse:
    return UserResponse.model_validate(user)


@router.get("/oidc/login")
async def oidc_login(request: Request, settings: SettingsDep) -> Response:
    if not settings.oidc_enabled:
        raise HTTPException(503, detail="oidc_not_configured")
    client = request.app.state.oauth.create_client("oidc")
    # Authlib stores nonce, state and PKCE verifier in the signed session and validates them.
    result: Response = await client.authorize_redirect(request, settings.oidc_redirect_uri)
    return result


@router.get("/oidc/callback")
async def oidc_callback(request: Request, session: SessionDep, settings: SettingsDep) -> Response:
    if not settings.oidc_enabled:
        raise HTTPException(503, detail="oidc_not_configured")
    client = request.app.state.oauth.create_client("oidc")
    try:
        token = await client.authorize_access_token(request)
        claims: dict[str, Any] = token.get("userinfo", {})
        if claims.get("iss") != settings.oidc_issuer:
            raise ValueError("issuer mismatch")
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject or len(subject) > 512:
            raise ValueError("invalid subject")
        if claims.get("email_verified") is not True:
            raise ValueError("verified email required")
        email = str(TypeAdapter(EmailStr).validate_python(claims.get("email"))).casefold()
    except Exception as exc:
        request.session.clear()
        raise HTTPException(400, detail="oidc_validation_failed") from exc
    request.session.clear()
    identity = await session.scalar(
        select(ExternalIdentity).where(
            ExternalIdentity.issuer == settings.oidc_issuer, ExternalIdentity.subject == subject
        )
    )
    if identity:
        user = await session.get(User, identity.user_id)
        if not user:
            raise HTTPException(401, detail="invalid_identity")
    else:
        # Never link to a local account on email equality, even if the IdP verified it.
        if await session.scalar(select(User.id).where(User.email == email)):
            raise HTTPException(409, detail="email_already_registered_use_original_provider")
        try:
            user = User(email=email, password_hash=None)
            session.add(user)
            await session.flush()
            session.add(
                ExternalIdentity(
                    user_id=user.id, issuer=settings.oidc_issuer or "", subject=subject
                )
            )
            await session.flush()
        except (IntegrityError, ValidationError) as exc:
            await session.rollback()
            raise HTTPException(409, detail="identity_registration_conflict") from exc
    response = RedirectResponse(settings.frontend_url, status_code=303)
    await create_login(user, session, response, settings)
    return response

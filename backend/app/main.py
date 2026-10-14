import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import cast

import structlog
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.middleware.sessions import SessionMiddleware

from app import agent, auth, documents
from app.config import Settings, get_settings
from app.db import create_session_factory


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or get_settings()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # HTTP client info logs can include URLs; avoid token-bearing callback queries.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
    sessions = create_session_factory(config.database_url)
    llm = agent.OpenAIDecisionClient(config) if config.openai_api_key else None

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if llm:
            await llm.client.close()
        await cast(AsyncEngine, sessions.kw["bind"]).dispose()

    app = FastAPI(title="AI Starter API", version="1.0.0", lifespan=lifespan)
    app.state.sessions = sessions
    app.state.decision_client = llm
    app.state.oauth = auth.build_oauth(config)
    app.state.agent_capacity = agent.Capacity(
        config.agent_concurrency, config.agent_queue_timeout_seconds
    )
    app.dependency_overrides[get_settings] = lambda: config
    app.add_middleware(
        SessionMiddleware,
        secret_key=config.jwt_secret.get_secret_value(),
        session_cookie="oidc_session",
        max_age=600,
        same_site="lax",
        https_only=config.cookie_secure,
        path="/api/v1/auth/oidc",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-CSRF-Token"],
        expose_headers=["X-Request-ID"],
    )

    @app.middleware("http")
    async def request_logging(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = str(uuid.uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.monotonic()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path.startswith("/api/v1/auth"):
            response.headers["Cache-Control"] = "no-store"
        structlog.get_logger().info(
            "http_request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            elapsed_ms=round((time.monotonic() - started) * 1000, 2),
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Pydantic's default includes submitted input, which may include passwords.
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {"loc": list(error["loc"]), "type": error["type"], "msg": error["msg"]}
                    for error in exc.errors()
                ]
            },
        )

    @app.exception_handler(Exception)
    async def unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        structlog.get_logger().error("unhandled_error", error_type=type(exc).__name__)
        return JSONResponse(status_code=500, content={"detail": "internal_server_error"})

    @app.get("/health/live", tags=["health"])
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    async def ready() -> dict[str, str]:
        try:
            async with sessions() as session:
                if not (
                    config.environment == "development"
                    and session.get_bind().dialect.name == "sqlite"
                ):
                    # Only the explicit local SQLite launcher bypasses Alembic.
                    revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
                    if revision != "0001":
                        raise RuntimeError("database schema revision mismatch")
                await session.execute(text("SELECT 1 FROM users LIMIT 1"))
        except Exception as exc:
            raise HTTPException(503, detail="database_not_ready") from exc
        return {"status": "ready"}

    for router in [auth.router, agent.router, documents.router]:
        app.include_router(router, prefix="/api/v1")
    return app

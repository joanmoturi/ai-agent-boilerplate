"""Loopback-only SQLite development server; never used by the production image."""

import asyncio
import secrets
from pathlib import Path

import uvicorn
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import create_async_engine

import app.models  # noqa: F401
from app.config import Settings
from app.db import Base
from app.main import create_app

BACKEND_DIRECTORY = Path(__file__).resolve().parents[1]
PROJECT_DIRECTORY = BACKEND_DIRECTORY.parent


def local_secret(directory: Path) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "jwt-secret"
    if not path.exists():
        with path.open("x", encoding="ascii") as file:
            file.write(secrets.token_urlsafe(48))
    secret = path.read_text(encoding="ascii").strip()
    if len(secret) < 32:
        raise ValueError(
            f"Invalid development JWT secret in {path}; at least 32 characters required"
        )
    return secret


async def initialize_database(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()


def main() -> None:
    directory = PROJECT_DIRECTORY / ".local"
    secret = local_secret(directory)
    database_url = f"sqlite+aiosqlite:///{(directory / 'development.sqlite').as_posix()}"
    settings = Settings(
        _env_file=(PROJECT_DIRECTORY / ".env", BACKEND_DIRECTORY / ".env"),
        database_url=database_url,
        jwt_secret=SecretStr(secret),
        environment="development",
        frontend_url="http://localhost:3000",
        cors_origins=["http://localhost:3000"],
        cookie_secure=False,
    )
    asyncio.run(initialize_database(database_url))
    print("SQLite development mode. Data persists in .local; pgvector endpoints are unavailable.")
    uvicorn.run(create_app(settings), host="127.0.0.1", port=8000, access_log=False)


if __name__ == "__main__":
    main()

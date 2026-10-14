from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.main import create_app
from scripts.dev_server import initialize_database, local_secret


def test_development_secret_persists(tmp_path: Path) -> None:
    first = local_secret(tmp_path / "local")
    assert len(first) >= 32
    assert local_secret(tmp_path / "local") == first


def test_invalid_development_secret_is_not_silently_replaced(tmp_path: Path) -> None:
    (tmp_path / "jwt-secret").write_text("invalid", encoding="ascii")
    with pytest.raises(ValueError, match="Invalid development JWT secret"):
        local_secret(tmp_path)


@pytest.mark.asyncio
async def test_development_readiness_requires_initialized_schema(tmp_path: Path) -> None:
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'development.sqlite').as_posix()}"
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        jwt_secret=SecretStr(local_secret(tmp_path)),
        environment="development",
    )
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://localhost:8000"
        ) as client:
            assert (await client.get("/health/ready")).status_code == 503
            await initialize_database(database_url)
            assert (await client.get("/health/ready")).status_code == 200


@pytest.mark.asyncio
async def test_test_mode_still_requires_migrations(tmp_path: Path) -> None:
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'test.sqlite').as_posix()}"
    await initialize_database(database_url)
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        jwt_secret=SecretStr(local_secret(tmp_path)),
        environment="test",
    )
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://localhost:8000"
        ) as client:
            assert (await client.get("/health/ready")).status_code == 503

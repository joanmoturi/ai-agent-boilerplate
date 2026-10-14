from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agent import get_decision_client
from app.config import Settings
from app.db import Base, get_session
from app.main import create_app
from app.schemas import Decision

ORIGIN = "http://localhost:3000"
PASSWORD = "correct horse battery staple"


class FakeLLM:
    def __init__(self) -> None:
        self.decisions: list[Any] = [
            Decision(action="final", answer="Hello from the test agent", tool=None)
        ]
        self.calls = 0
        self.delay = 0.0
        self.observed: list[list[dict[str, Any]]] = []

    async def decide(self, prompt: str, state: list[dict[str, Any]]) -> Decision:
        import asyncio

        self.calls += 1
        self.observed.append(list(state))
        if self.delay:
            await asyncio.sleep(self.delay)
        value = self.decisions[min(self.calls - 1, len(self.decisions) - 1)]
        if isinstance(value, Exception):
            raise value
        return value


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        jwt_secret="test-only-secret-that-is-at-least-32-characters",
        database_url="sqlite+aiosqlite://",
        environment="test",
        agent_timeout_seconds=0.2,
        agent_max_steps=3,
    )


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()


@pytest_asyncio.fixture
async def application(settings: Settings, llm: FakeLLM) -> AsyncIterator[FastAPI]:
    app = create_app(settings)
    engine = create_async_engine("sqlite+aiosqlite://")

    @event.listens_for(engine.sync_engine, "connect")
    def foreign_keys(connection: Any, _: Any) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            try:
                yield session
            except BaseException:
                await session.rollback()
                raise

    app.state.test_sessions = factory
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_decision_client] = lambda: llm
    yield app
    await engine.dispose()
    await app.state.sessions.kw["bind"].dispose()


@pytest_asyncio.fixture
async def client(application: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://localhost:8000"
    ) as client:
        yield client


async def register(client: httpx.AsyncClient, email: str = "alice@example.com") -> dict[str, Any]:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 201, response.text
    return response.json()


def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    return {"Origin": ORIGIN, "X-CSRF-Token": client.cookies["csrf_token"]}


def authorization(tokens: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}

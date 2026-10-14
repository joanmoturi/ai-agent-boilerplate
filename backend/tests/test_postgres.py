import asyncio
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.main import create_app
from app.models import Document
from tests.conftest import ORIGIN, register

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not os.getenv("TEST_POSTGRES_URL"),
        reason="TEST_POSTGRES_URL not set; requires disposable PostgreSQL with pgvector",
    ),
]
ROOT = Path(__file__).resolve().parents[1]


async def test_migrations_vector_and_atomic_rotation() -> None:
    url = os.environ["TEST_POSTGRES_URL"]
    env = {**os.environ, "DATABASE_URL": url, "JWT_SECRET": "postgres-test-only-secret-" + "s" * 32}

    async def migrate(revision: str) -> None:
        result = await asyncio.to_thread(
            subprocess.run,
            [
                sys.executable,
                "-m",
                "alembic",
                "upgrade" if revision == "head" else "downgrade",
                revision,
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    await migrate("head")
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app(
        Settings(_env_file=None, database_url=url, jwt_secret=env["JWT_SECRET"], environment="test")
    )
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://localhost:8000"
        ) as client:
            tokens = await register(client)
            assert (await client.get("/health/ready")).status_code == 200
            user_id = tokens["user"]["id"]
            cookies = dict(client.cookies)
        async with factory() as session:
            assert (
                await session.scalar(
                    text("SELECT extname FROM pg_extension WHERE extname='vector'")
                )
                == "vector"
            )
            near = Document(
                user_id=user_id,
                content="near",
                embedding_model="text-embedding-3-small",
                embedding=[1.0] + [0.0] * 1535,
            )
            far = Document(
                user_id=user_id,
                content="far",
                embedding_model="text-embedding-3-small",
                embedding=[-1.0] + [0.0] * 1535,
            )
            session.add_all([near, far])
            await session.commit()
            documents = (
                await session.scalars(
                    select(Document)
                    .where(Document.user_id == user_id)
                    .order_by(Document.embedding.cosine_distance([1.0] + [0.0] * 1535))
                )
            ).all()
            assert [document.content for document in documents] == ["near", "far"]

        async def rotate() -> httpx.Response:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://localhost:8000", cookies=cookies
            ) as client:
                return await client.post(
                    "/api/v1/auth/refresh",
                    headers={
                        "Origin": ORIGIN,
                        "X-CSRF-Token": cookies["csrf_token"],
                    },
                )

        responses = await asyncio.gather(rotate(), rotate())
        assert sorted(response.status_code for response in responses) == [200, 401]
        winner = next(response for response in responses if response.status_code == 200)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://localhost:8000", cookies=winner.cookies
        ) as client:
            assert (
                await client.post(
                    "/api/v1/auth/refresh",
                    headers={
                        "Origin": ORIGIN,
                        "X-CSRF-Token": winner.cookies["csrf_token"],
                    },
                )
            ).status_code == 401
    finally:
        await engine.dispose()
        await app.state.sessions.kw["bind"].dispose()
        await migrate("base")

import os
import subprocess
import sys
from pathlib import Path


def test_postgres_migration_renders_extension_and_vector() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=root,
        env={
            **os.environ,
            "DATABASE_URL": "postgresql+asyncpg://postgres:postgres@localhost:5432/app",
            "JWT_SECRET": "migration-render-test-only-secret-" + "s" * 32,
            "ENVIRONMENT": "test",
            "OIDC_ISSUER": "",
            "OIDC_CLIENT_ID": "",
            "OIDC_CLIENT_SECRET": "",
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CREATE EXTENSION IF NOT EXISTS vector" in result.stdout
    assert "VECTOR(1536)" in result.stdout.upper()
    assert "USING hnsw" in result.stdout
    assert "refresh_tokens" in result.stdout
    assert "agent_runs" in result.stdout

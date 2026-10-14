from decimal import Decimal

import httpx
import openai
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.agent import Capacity, arithmetic, get_decision_client
from app.models import AgentRun
from app.schemas import Arithmetic, Decision
from tests.conftest import FakeLLM, authorization, register


async def test_multistep_persisted_and_repeat(
    client: httpx.AsyncClient, application: FastAPI, llm: FakeLLM
) -> None:
    tokens = await register(client)
    llm.decisions = [
        Decision(
            action="tool", answer=None, tool=Arithmetic(operation="add", left="0.1", right="0.2")
        ),
        Decision(action="final", answer="0.3", tool=None),
    ]
    first = await client.post(
        "/api/v1/agent/runs", json={"prompt": "Add 0.1 + 0.2"}, headers=authorization(tokens)
    )
    assert first.status_code == 201, first.text
    run = first.json()
    assert run["status"] == "completed" and run["answer"] == "0.3" and run["steps"] == 2
    assert llm.observed[1][0]["observation"] == "0.3"
    response = await client.get(f"/api/v1/agent/runs/{run['id']}", headers=authorization(tokens))
    assert response.json() == run
    async with application.state.test_sessions() as session:
        saved = await session.get(AgentRun, run["id"])
        assert saved.prompt == "Add 0.1 + 0.2"
        assert len(saved.state) == 2
    second = await client.post(
        "/api/v1/agent/runs", json={"prompt": "Add 0.1 + 0.2"}, headers=authorization(tokens)
    )
    assert second.json()["id"] != run["id"]


@pytest.mark.parametrize(
    "decision,error",
    [
        (
            {"action": "final", "answer": "x", "tool": None, "unexpected": True},
            "invalid_agent_output",
        ),
        (Decision(action="tool", tool=None, answer=None), "invalid_agent_output"),
        (
            Decision(
                action="final", tool=Arithmetic(operation="add", left="1", right="1"), answer="x"
            ),
            "invalid_agent_output",
        ),
        (
            Decision(
                action="tool", tool=Arithmetic(operation="divide", left="1", right="0"), answer=None
            ),
            "invalid_agent_output",
        ),
    ],
)
async def test_invalid_decisions(
    client: httpx.AsyncClient, llm: FakeLLM, decision: object, error: str
) -> None:
    tokens = await register(client)
    llm.decisions = [decision]
    response = await client.post(
        "/api/v1/agent/runs", json={"prompt": "test"}, headers=authorization(tokens)
    )
    assert response.status_code == 201
    assert response.json()["status"] == "failed"
    assert response.json()["error"] == error
    assert response.json()["steps"] == 1


async def test_timeout(client: httpx.AsyncClient, llm: FakeLLM) -> None:
    tokens = await register(client)
    llm.delay = 1
    response = await client.post(
        "/api/v1/agent/runs", json={"prompt": "test"}, headers=authorization(tokens)
    )
    assert response.json()["error"] == "agent_timeout"
    assert response.json()["status"] == "failed"
    fetched = await client.get(
        f"/api/v1/agent/runs/{response.json()['id']}", headers=authorization(tokens)
    )
    assert fetched.json() == response.json()


async def test_max_steps(client: httpx.AsyncClient, llm: FakeLLM) -> None:
    tokens = await register(client)
    llm.decisions = [
        Decision(
            action="tool", answer=None, tool=Arithmetic(operation="multiply", left="2", right="3")
        )
    ]
    response = await client.post(
        "/api/v1/agent/runs", json={"prompt": "test"}, headers=authorization(tokens)
    )
    assert response.json()["error"] == "max_steps_exceeded"
    assert response.json()["steps"] == llm.calls == 3


async def test_llm_error(client: httpx.AsyncClient, llm: FakeLLM) -> None:
    tokens = await register(client)
    llm.decisions = [
        openai.APIConnectionError(request=httpx.Request("POST", "https://example.com"))
    ]
    response = await client.post(
        "/api/v1/agent/runs", json={"prompt": "test"}, headers=authorization(tokens)
    )
    assert response.json()["error"] == "llm_service_error"


async def test_ownership_auth_and_limits(client: httpx.AsyncClient) -> None:
    assert (await client.post("/api/v1/agent/runs", json={"prompt": "test"})).status_code == 401
    alice = await register(client)
    run = (
        await client.post(
            "/api/v1/agent/runs", json={"prompt": "test"}, headers=authorization(alice)
        )
    ).json()
    bob = await register(client, "bob@example.com")
    assert (
        await client.get(f"/api/v1/agent/runs/{run['id']}", headers=authorization(bob))
    ).status_code == 404
    for prompt in ["", "  ", "x" * 4001, 123]:
        assert (
            await client.post(
                "/api/v1/agent/runs", json={"prompt": prompt}, headers=authorization(bob)
            )
        ).status_code == 422


async def test_unconfigured_llm(client: httpx.AsyncClient, application: FastAPI) -> None:
    tokens = await register(client)
    del application.dependency_overrides[get_decision_client]
    response = await client.post(
        "/api/v1/agent/runs", json={"prompt": "test"}, headers=authorization(tokens)
    )
    assert response.status_code == 503
    async with application.state.test_sessions() as session:
        assert await session.scalar(select(AgentRun.id)) is None


async def test_capacity(client: httpx.AsyncClient, application: FastAPI) -> None:
    tokens = await register(client)
    capacity = Capacity(1, 0.01)
    application.state.agent_capacity = capacity
    async with capacity.slot():
        response = await client.post(
            "/api/v1/agent/runs", json={"prompt": "test"}, headers=authorization(tokens)
        )
    assert response.status_code == 429
    assert response.headers["retry-after"] == "2"


@pytest.mark.parametrize(
    "operation,left,right,expected",
    [
        ("add", "0.1", "0.2", "0.3"),
        ("subtract", "1", "2", "-1"),
        ("multiply", "3", "4", "12"),
        ("divide", "1", "2", "0.5"),
    ],
)
def test_arithmetic(operation: str, left: str, right: str, expected: str) -> None:
    value = arithmetic(
        Arithmetic.model_validate({"operation": operation, "left": left, "right": right})
    )
    assert Decimal(value) == Decimal(expected)


@pytest.mark.parametrize("operand", ["NaN", "Infinity", "1e999", "1; os.system('bad')", "9" * 13])
def test_arithmetic_rejects_code_and_unbounded(operand: str) -> None:
    with pytest.raises(ValueError):
        Arithmetic(operation="add", left=operand, right="1")

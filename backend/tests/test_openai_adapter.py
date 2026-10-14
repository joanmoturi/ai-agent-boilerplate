import json

import httpx
import openai
import pytest
from pydantic import SecretStr

from app.agent import OpenAIDecisionClient
from app.config import Settings


@pytest.mark.parametrize(
    "content,valid",
    [
        ({"action": "final", "answer": "structured answer", "tool": None}, True),
        (
            {
                "action": "tool",
                "answer": None,
                "tool": {"operation": "add", "left": "2.5", "right": "1.5"},
            },
            True,
        ),
        ({"action": "execute_code", "answer": None, "tool": None}, False),
        ({"action": "final", "answer": "x", "tool": None, "extra": "not allowed"}, False),
    ],
)
async def test_sdk_parses_actual_structured_json(
    settings: Settings, content: dict[str, object], valid: bool
) -> None:
    settings.openai_api_key = SecretStr("test-only-key")
    adapter = OpenAIDecisionClient(settings)
    await adapter.client.close()
    seen: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1,
                "model": "gpt-4o-mini",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(content),
                            "refusal": None,
                        },
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            },
        )

    adapter.client = openai.AsyncOpenAI(
        api_key="test-only-key",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        if valid:
            result = await adapter.decide("bounded input", [])
            assert result.action == content["action"]
        else:
            with pytest.raises(ValueError):
                await adapter.decide("bounded input", [])
        assert seen[0]["response_format"]["type"] == "json_schema"
        assert seen[0]["response_format"]["json_schema"]["strict"] is True
        assert seen[0]["model"] == "gpt-4o-mini"
    finally:
        await adapter.client.close()

import json

import httpx
import pytest

from ai_poster.ai import QualityError, Rewriter


def completion(content, reason="stop", refusal=None):
    return {
        "choices": [
            {
                "finish_reason": reason,
                "message": {
                    "content": json.dumps(content),
                    "refusal": refusal,
                },
            }
        ]
    }


async def rewrite_with_responses(settings, *responses):
    remaining = list(responses)
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=remaining.pop(0))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await Rewriter(settings, client).rewrite(
            "Profit was $10m, possibly more.", "https://t.me/source/11"
        )
    return result, requests


REWRITE = {"text": "Прибыль составила $10 млн, возможно больше.", "standalone": True, "reason": ""}
REVIEW = {
    "faithful": True,
    "missing_facts": [],
    "added_claims": [],
    "changed_facts": [],
    "reason": "Смысл сохранён",
    "confidence": 0.98,
}


async def test_rewrite_and_separate_review_add_source(settings):
    result, requests = await rewrite_with_responses(
        settings, completion(REWRITE), completion(REVIEW)
    )
    assert result.endswith("Источник: https://t.me/source/11")
    assert len(requests) == 2
    assert requests[0]["response_format"]["json_schema"]["strict"] is True
    assert json.loads(requests[1]["messages"][1]["content"])["candidate"] == REWRITE["text"]


@pytest.mark.parametrize(
    "changes",
    [
        {"faithful": False},
        {"confidence": 0.5},
        {"missing_facts": ["possibly"]},
        {"added_claims": ["record profit"]},
        {"changed_facts": ["10m became 20m"]},
    ],
)
async def test_review_rejects_changed_meaning(settings, changes):
    with pytest.raises(QualityError):
        await rewrite_with_responses(settings, completion(REWRITE), completion(REVIEW | changes))


@pytest.mark.parametrize(
    "response",
    [
        completion(REWRITE, reason="length"),
        completion(REWRITE, refusal="No"),
        completion(REWRITE | {"standalone": False}),
        completion(REWRITE | {"text": "😀" * 2100}),
    ],
)
async def test_fail_closed_on_incomplete_or_oversized_output(settings, response):
    with pytest.raises(QualityError):
        await rewrite_with_responses(settings, response)


def claude_completion(content, reason="end_turn"):
    return {"stop_reason": reason, "content": [{"type": "text", "text": json.dumps(content)}]}


@pytest.fixture
def claude_settings(settings):
    return type(settings)(
        _env_file=None,
        telegram_bot_token="fake-token",
        owner_id=42,
        ai_provider="anthropic",
        anthropic_api_key="fake-claude-key",
    )


async def test_claude_rewrite_and_review_use_messages_api(claude_settings):
    responses = [claude_completion(REWRITE), claude_completion(REVIEW)]
    requests = []

    def handler(request):
        assert str(request.url) == "https://api.anthropic.com/v1/messages"
        assert request.headers["x-api-key"] == "fake-claude-key"
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert "authorization" not in request.headers
        body = json.loads(request.content)
        requests.append(body)
        assert body["model"] == "claude-sonnet-4-6"
        assert body["system"]
        assert body["messages"][0]["role"] == "user"
        assert body["output_config"]["format"]["type"] == "json_schema"
        return httpx.Response(200, json=responses.pop(0))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await Rewriter(claude_settings, client).rewrite(
            "Profit was $10m, possibly more.", "https://t.me/source/11"
        )
    assert result.endswith("Источник: https://t.me/source/11")
    assert len(requests) == 2
    confidence = requests[1]["output_config"]["format"]["schema"]["properties"]["confidence"]
    assert "minimum" not in confidence and "maximum" not in confidence
    assert "minimum=0" in confidence["description"]
    assert json.loads(requests[1]["messages"][0]["content"])["candidate"] == REWRITE["text"]


@pytest.mark.parametrize("reason", ["refusal", "max_tokens", "tool_use", "pause_turn"])
async def test_claude_incomplete_response_blocks_post(claude_settings, reason):
    with pytest.raises(QualityError):
        await rewrite_with_responses(claude_settings, claude_completion(REWRITE, reason))


@pytest.mark.parametrize(
    "changes",
    [
        {"faithful": False},
        {"confidence": 0.5},
        {"missing_facts": ["possibly"]},
        {"added_claims": ["record profit"]},
        {"changed_facts": ["10m became 20m"]},
    ],
)
async def test_claude_review_blocks_changed_meaning(claude_settings, changes):
    with pytest.raises(QualityError):
        await rewrite_with_responses(
            claude_settings, claude_completion(REWRITE), claude_completion(REVIEW | changes)
        )


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
async def test_claude_numeric_bounds_are_validated_locally(claude_settings, confidence):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        await rewrite_with_responses(
            claude_settings,
            claude_completion(REWRITE),
            claude_completion(REVIEW | {"confidence": confidence}),
        )


@pytest.mark.parametrize("content", [[], [{"type": "tool_use", "name": "unexpected"}]])
async def test_claude_missing_or_unexpected_content_blocks(claude_settings, content):
    with pytest.raises(QualityError):
        await rewrite_with_responses(
            claude_settings, {"stop_reason": "end_turn", "content": content}
        )


async def test_block_reason_reports_concrete_difference_despite_positive_summary(settings):
    with pytest.raises(QualityError, match="Добавлено от себя: рублей"):
        await rewrite_with_responses(
            settings,
            completion(REWRITE),
            completion(REVIEW | {"added_claims": ["рублей"], "reason": "В целом всё верно"}),
        )

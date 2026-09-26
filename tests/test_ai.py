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

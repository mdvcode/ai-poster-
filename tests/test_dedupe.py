import json

import httpx
import pytest
from test_ai import completion

from ai_poster.ai import Rewriter
from ai_poster.db import post_version
from ai_poster.dedupe import content_key, near_identical
from ai_poster.sources import Item


def test_normalization_catches_case_punctuation_and_different_source_footers():
    assert content_key("News: the launch is LIVE!") == content_key("NEWS — the launch is live.")
    assert near_identical(
        "The launch is live.\n\nSource: https://t.me/a/1",
        "The launch is live!\n\nSource: https://t.me/b/2",
    )
    assert content_key("Study: https://one.test") != content_key("Study: https://two.test")


def test_similar_news_with_new_numbers_is_not_identical():
    a = (
        "Company published a detailed report on its results this morning with extensive "
        "information about customers and sales which reached 100 million dollars."
    )
    assert not near_identical(a, a.replace("100", "200"))
    assert near_identical(a, a.replace("detailed", "comprehensive"))


async def test_cross_source_deleted_post_stays_suppressed(worker, store, telegram):
    store.ingest(1, [Item("9", "Company earned $10 million.", "url")], "10", "-100123")
    store.delete_post(1, post_version(store.post(1)))
    store.add_source("telegram", "other", "public:other", "0")
    store.ingest(2, [Item("20", "COMPANY EARNED $10 MILLION!", "other-url")], "20", "-100123")
    worker.sources["telegram"].fetch.return_value = ([], "20")
    await worker.cycle()
    assert store.post(2)["state"] == "duplicate"
    assert store.post(2)["duplicate_of"] == 1
    worker.rewriter.rewrite.assert_not_called()
    telegram.send.assert_not_called()


async def test_semantic_candidate_duplicate_does_not_generate(worker, store):
    store.ingest(
        1,
        [
            Item(
                "9",
                "DrivingBench tested models steering Corolla and Astra completed the course.",
                "url",
            )
        ],
        "10",
        "-100123",
    )
    store.update_post(1, state="published")
    store.ingest(
        1,
        [
            Item(
                "10",
                "Astra completed the course while DrivingBench tested Corolla steering models.",
                "url2",
            )
        ],
        "10",
        "-100123",
    )
    worker.sources["telegram"].fetch.return_value = ([], "10")
    worker.rewriter.find_duplicate.return_value = 1
    await worker.cycle()
    worker.rewriter.find_duplicate.assert_awaited_once()
    worker.rewriter.rewrite.assert_not_called()
    assert store.post(2)["state"] == "duplicate"


async def test_publish_guard_catches_identical_edited_drafts(worker, store, telegram):
    await worker.cycle()
    store.update_post(1, state="published")
    store.ingest(
        1, [Item("12", "A different original with different facts.", "url2")], "12", "-100123"
    )
    store.update_post(2, state="ready", draft=store.post(1)["draft"])
    telegram.send.reset_mock()
    result = await worker.publish(2, manual=True)
    assert "Повторная публикация остановлена" in result
    assert store.post(2)["state"] == "duplicate"
    telegram.send.assert_not_called()


@pytest.mark.parametrize(
    "duplicate_id,confidence,expected",
    [(1, 0.99, 1), (999, 0.99, None), (1, 0.8, None), (None, 0.99, None)],
)
async def test_ai_dedupe_validates_candidate_id_and_confidence(
    settings, duplicate_id, confidence, expected
):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json=completion(
                {"duplicate_of": duplicate_id, "confidence": confidence, "reason": "test"}
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await Rewriter(settings, client).find_duplicate(
            "incoming", [{"id": 1, "original": "earlier"}]
        )
    assert result == expected
    assert len(requests) == 1

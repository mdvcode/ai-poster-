from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from telethon.tl.types import Channel

from ai_poster.sources import TelegramSource, XSource, normalize_handle


@pytest.mark.parametrize(
    "kind,value,expected",
    [
        ("telegram", "https://t.me/Example/", "example"),
        ("telegram", "@Example", "example"),
        ("x", "https://x.com/OpenAI", "openai"),
        ("x", "https://twitter.com/OpenAI", "openai"),
    ],
)
def test_normalize(kind, value, expected):
    assert normalize_handle(kind, value) == expected


@pytest.mark.parametrize("value", ["https://evil.test/source", "source/123", "../token", "+invite"])
def test_disallow_arbitrary_urls(value):
    with pytest.raises(ValueError):
        normalize_handle("telegram", value)


async def test_x_pagination_extended_text_and_exclude_quotes():
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        if len(seen) == 1:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "14",
                            "text": "truncated",
                            "note_tweet": {"text": "Complete long post"},
                        },
                        {
                            "id": "13",
                            "text": "Quote",
                            "referenced_tweets": [{"id": "1", "type": "quoted"}],
                        },
                    ],
                    "meta": {"next_token": "page2"},
                },
            )
        return httpx.Response(
            200, json={"data": [{"id": "12", "text": "Earlier post"}], "meta": {}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = XSource("token", client)
        items, cursor = await source.fetch({"external_id": "123", "cursor": "10"})
    assert cursor == "14"
    assert [i.id for i in items] == ["12", "14"]
    assert items[-1].text == "Complete long post"
    assert seen[1]["since_id"] == "10"
    assert seen[1]["pagination_token"] == "page2"
    assert seen[0]["exclude"] == "retweets,replies"


async def test_x_failed_second_page_does_not_return_partial_batch():
    def handler(request):
        if request.url.params.get("pagination_token"):
            return httpx.Response(429)
        return httpx.Response(
            200, json={"data": [{"id": "14", "text": "new"}], "meta": {"next_token": "next"}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await XSource("token", client).fetch({"external_id": "123", "cursor": "10"})


async def test_x_baseline_prevents_importing_history():
    def handler(request):
        if "/by/username/" in request.url.path:
            return httpx.Response(200, json={"data": {"id": "123"}})
        return httpx.Response(
            200, json={"data": [{"id": "50", "text": "old"}, {"id": "49", "text": "older"}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await XSource("token", client).resolve("source") == ("123", "50")


async def test_telegram_cursor_advances_over_media_only_and_reads_oldest_first():
    client = AsyncMock()
    channel = Channel(id=456, title="Source", photo=None, date=None, broadcast=True)
    client.get_entity.return_value = channel

    async def messages(*args, **kwargs):
        assert kwargs == {"min_id": 10, "reverse": True, "limit": 100}
        yield SimpleNamespace(id=11, raw_text="New post")
        yield SimpleNamespace(id=12, raw_text="")

    client.iter_messages = Mock(side_effect=messages)
    items, cursor = await TelegramSource(client).fetch(
        {
            "handle": "source",
            "external_id": "-1000000000456",
            "cursor": "10",
        }
    )
    assert cursor == "12"
    assert len(items) == 1
    assert items[0].url == "https://t.me/source/11"


async def test_telegram_detects_changed_username_owner():
    client = AsyncMock()
    client.get_entity.return_value = Channel(
        id=999, title="Other", photo=None, date=None, broadcast=True
    )
    with pytest.raises(ValueError, match="владельца"):
        await TelegramSource(client).fetch({"handle": "source", "external_id": "-100456"})

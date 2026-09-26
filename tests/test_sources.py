import httpx
import pytest

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
        source = XSource("token", client, lambda *args: True)
        items, cursor = await source.fetch(
            {"handle": "chosen", "external_id": "123", "cursor": "10"}
        )
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
            await XSource("token", client, lambda *args: True).fetch(
                {"handle": "chosen", "external_id": "123", "cursor": "10"}
            )


async def test_x_baseline_prevents_importing_history():
    def handler(request):
        if "/by/username/" in request.url.path:
            return httpx.Response(200, json={"data": {"id": "123"}})
        return httpx.Response(
            200, json={"data": [{"id": "50", "text": "old"}, {"id": "49", "text": "older"}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await XSource("token", client, lambda *args: True).resolve("source") == ("123", "50")


def preview(posts, handle="chosen", older=False):
    cards = "".join(
        f'<div class="tgme_widget_message" data-post="{handle}/{post_id}">'
        f'<div class="tgme_widget_message_text">{body}</div></div>'
        for post_id, body in posts
    )
    more = (
        '<a class="tme_messages_more" data-before="11" href="https://evil.test/">more</a>'
        if older
        else ""
    )
    return (
        '<a class="tgme_channel_info_header_username">@'
        + handle
        + "</a>"
        + '<section class="tgme_channel_history">'
        + cards
        + "</section>"
        + more
    )


def html_response(text):
    return httpx.Response(200, text=text, headers={"content-type": "text/html; charset=utf-8"})


async def test_public_telegram_catchup_preserves_text_and_only_reads_chosen_channel():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.host == "t.me"
        assert request.url.path == "/s/chosen"
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        if request.url.params.get("before"):
            assert request.url.params["before"] == "12"
            return html_response(preview([(10, "old"), (11, "Earlier")]))
        return html_response(
            preview(
                [(12, 'New<br>line &amp; <a href="https://example.com">link</a>'), (13, "")],
                older=True,
            )
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "must-not-leak"},
        cookies={"private": "must-not-leak"},
    ) as client:
        source = TelegramSource(client, lambda kind, handle, ext: handle == "chosen")
        items, cursor = await source.fetch(
            {"handle": "chosen", "external_id": "public:chosen", "cursor": "10"}
        )
    assert [i.id for i in items] == ["11", "12", "13"]
    assert items[1].text == "New\nline & link (https://example.com)"
    assert cursor == "13"
    assert len(requests) == 2


@pytest.mark.parametrize(
    "external_id,allowed",
    [
        ("public:chosen", None),
        ("public:chosen", lambda *args: False),
        ("-100123", lambda *args: True),
        ("public:other", lambda *args: True),
    ],
)
async def test_telegram_denies_non_whitelisted_or_legacy_source_before_network(
    external_id, allowed
):
    def handler(request):
        pytest.fail("No network requests are allowed")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PermissionError):
            await TelegramSource(client, allowed).fetch(
                {"handle": "chosen", "external_id": external_id, "cursor": "0"}
            )


async def test_public_telegram_baseline_does_not_import_archive():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: html_response(preview([(10, "old"), (11, "latest")]))
        )
    ) as client:
        assert await TelegramSource(client).resolve("chosen") == ("public:chosen", "11")


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(302, headers={"location": "https://evil.test/"}),
        html_response("<html>Login required</html>"),
        html_response(preview([(1, "wrong channel")], handle="other")),
        html_response(preview([(1, "test")]).replace('data-post="chosen/', 'data-post="other/')),
    ],
)
async def test_unavailable_or_redirected_telegram_page_never_follows_or_parses_other_channel(
    response,
):
    calls = []

    def handler(request):
        calls.append(request)
        return response

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as client:
        with pytest.raises((ValueError, httpx.HTTPStatusError)):
            await TelegramSource(client).resolve("chosen")
    assert len(calls) == 1


async def test_disabling_telegram_during_pagination_stops_reads():
    calls = []

    def handler(request):
        calls.append(request)
        return html_response(preview([(12, "new")], older=True))

    checks = iter([True, True, False])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PermissionError):
            await TelegramSource(client, lambda *args: next(checks)).fetch(
                {"handle": "chosen", "external_id": "public:chosen", "cursor": "10"}
            )
    assert len(calls) == 1


async def test_telegram_failed_second_page_returns_no_partial_batch():
    def handler(request):
        if request.url.params.get("before"):
            return httpx.Response(503)
        return html_response(preview([(12, "new")], older=True))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await TelegramSource(client, lambda *args: True).fetch(
                {"handle": "chosen", "external_id": "public:chosen", "cursor": "10"}
            )


async def test_x_rejects_non_whitelisted_source_without_network():
    def handler(request):
        pytest.fail("No network requests are allowed")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PermissionError):
            await XSource("token", client).fetch(
                {"handle": "chosen", "external_id": "123", "cursor": "10"}
            )


async def test_real_store_enforces_whitelist_and_removal(store):
    store.add_source("telegram", "chosen", "public:chosen", "10")
    requests = []

    def handler(request):
        requests.append(request)
        return html_response(preview([(11, "new")]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = TelegramSource(client, store.source_is_active)
        selected = {"handle": "chosen", "external_id": "public:chosen", "cursor": "10"}
        items, cursor = await source.fetch(selected)
        assert len(items) == 1 and cursor == "11"
        with pytest.raises(PermissionError):
            await source.fetch({"handle": "other", "external_id": "public:other", "cursor": "10"})
        store.remove_source(2)
        with pytest.raises(PermissionError):
            await source.fetch(selected)
    assert len(requests) == 1


async def test_disabled_x_blocks_next_page():
    calls = []
    checks = iter([True, True, False])

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200, json={"data": [{"id": "12", "text": "chosen"}], "meta": {"next_token": "next"}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PermissionError):
            await XSource("token", client, lambda *args: next(checks)).fetch(
                {"handle": "chosen", "external_id": "123", "cursor": "10"}
            )
    assert len(calls) == 1

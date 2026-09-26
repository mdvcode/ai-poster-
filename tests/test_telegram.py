import json

import httpx
import pytest

from ai_poster.telegram import Telegram, TelegramError


async def test_send_plain_text_without_format_interpretation():
    def handler(request):
        payload = json.loads(request.content)
        assert payload["text"] == "<b>not markup</b>"
        assert "parse_mode" not in payload
        assert payload["link_preview_options"]["is_disabled"]
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await Telegram("secret-token", client).send(-100123, "<b>not markup</b>") == {
            "message_id": 7,
        }


async def test_error_redacts_token_and_retains_retry_after():
    def handler(request):
        return httpx.Response(
            429,
            json={
                "ok": False,
                "error_code": 429,
                "description": "secret-token",
                "parameters": {"retry_after": 30},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TelegramError) as error:
            await Telegram("secret-token", client).send(42, "Hello")
    assert "secret-token" not in str(error.value)
    assert error.value.retry_after == 30


@pytest.mark.parametrize(
    "owner_status,can_post,allowed",
    [
        ("creator", True, True),
        ("administrator", True, True),
        ("member", True, False),
        ("creator", False, False),
    ],
)
async def test_connect_requires_owner_and_bot_permissions(owner_status, can_post, allowed):
    def handler(request):
        method = request.url.path.rsplit("/", 1)[1]
        payload = json.loads(request.content)
        result = {"getChat": {"type": "channel", "id": -100123}, "getMe": {"id": 99}}
        if method == "getChatMember":
            data = (
                {"status": owner_status}
                if payload["user_id"] == 42
                else {"status": "administrator", "can_post_messages": can_post}
            )
        else:
            data = result[method]
        return httpx.Response(200, json={"ok": True, "result": data})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        telegram = Telegram("token", client)
        if allowed:
            assert await telegram.validate_channel("@target", 42) == "-100123"
        else:
            with pytest.raises(ValueError):
                await telegram.validate_channel("@target", 42)

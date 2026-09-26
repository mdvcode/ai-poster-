import json
from unittest.mock import AsyncMock

import pytest

from ai_poster.bot import Bot
from ai_poster.sources import Item
from ai_poster.telegram import TelegramError


@pytest.fixture
def bot(store, worker, telegram, settings):
    worker.sources["telegram"].resolve.return_value = ("-100888", "100")
    return Bot(store, worker, telegram, settings)


async def press(bot, action, sender=42, chat_id=42, chat_type="private"):
    await bot.handle(
        {
            "callback_query": {
                "id": "callback",
                "from": {"id": sender},
                "data": f"admin:{action}",
                "message": {"message_id": 88, "chat": {"id": chat_id, "type": chat_type}},
            }
        }
    )


async def message(bot, text="", **fields):
    await bot.handle(
        {
            "message": {
                "from": {"id": 42},
                "chat": {"id": 42, "type": "private"},
                "text": text,
                **fields,
            }
        }
    )


def last_panel(telegram):
    edits = [c.kwargs for c in telegram.call.call_args_list if c.args[0] == "editMessageText"]
    return edits[-1]


async def test_start_opens_button_admin(bot, telegram, store):
    await message(bot, "/start")
    assert "Админка" in telegram.send.call_args.args[1]
    buttons = telegram.send.call_args.kwargs["reply_markup"]["inline_keyboard"]
    actions = {b["callback_data"] for row in buttons for b in row}
    assert {"admin:sources:0", "admin:channel", "admin:queue:0"} <= actions
    assert store.get("mode") == "manual"


async def test_add_telegram_and_remove_with_buttons(bot, telegram, store):
    await press(bot, "add:telegram")
    assert json.loads(store.get("admin_input"))["kind"] == "telegram"
    await message(bot, "https://t.me/other_channel")
    assert store.sources()[-1]["handle"] == "other_channel"
    assert not store.get("admin_input")
    assert "добавлен" in telegram.send.call_args.args[1]
    await press(bot, "remove:2")
    assert len(store.sources()) == 2
    assert "Удалить источник" in last_panel(telegram)["text"]
    await press(bot, "remove_confirm:2")
    assert len(store.sources()) == 1


async def test_add_x_requires_configured_access(bot, telegram, store):
    await press(bot, "add:x")
    assert "X ещё не подключено" in last_panel(telegram)["text"]
    assert not store.get("admin_input")
    assert len(store.sources()) == 1


async def test_add_x_when_configured(bot, store):
    from pydantic import SecretStr

    bot.settings.x_bearer_token = SecretStr("fake-x-key")
    bot.worker.sources["x"] = AsyncMock()
    bot.worker.sources["x"].resolve.return_value = ("777", "100")
    await press(bot, "add:x")
    await message(bot, "https://x.com/OpenAI")
    assert store.sources()[-1]["kind"] == "x"
    assert store.sources()[-1]["handle"] == "openai"


async def test_invalid_input_retains_prompt_then_corrects(bot, store, telegram):
    await press(bot, "add:telegram")
    await message(bot, "https://evil.example/redirect")
    assert len(store.sources()) == 1
    assert json.loads(store.get("admin_input"))["kind"] == "telegram"
    assert "корректную ссылку" in telegram.send.call_args.args[1]
    await message(bot, "@valid_channel")
    assert len(store.sources()) == 2
    assert not store.get("admin_input")


async def test_cancel_clears_input_and_plain_text_does_not_add(bot, store):
    await press(bot, "add:telegram")
    await press(bot, "home")
    await message(bot, "@unexpected_source")
    assert len(store.sources()) == 1
    assert not store.get("admin_input")


async def test_command_interrupts_form(bot, store):
    await press(bot, "add:telegram")
    await message(bot, "/pause")
    assert store.get("paused") == "1"
    assert not store.get("admin_input")


async def test_form_survives_bot_recreation_and_expires(bot, store, telegram):
    await press(bot, "add:telegram")
    replacement = Bot(store, bot.worker, telegram, bot.settings)
    await message(replacement, "@after_restart")
    assert store.sources()[-1]["handle"] == "after_restart"
    store.set("admin_input", json.dumps({"kind": "telegram", "created": 0}))
    await message(replacement, "@too_late")
    assert "Время ввода истекло" in telegram.send.call_args.args[1]
    assert len(store.sources()) == 2


async def test_connect_private_channel_by_forward(bot, store, telegram):
    telegram.validate_channel.return_value = "-100789"
    await press(bot, "channel")
    await message(bot, forward_origin={"type": "channel", "chat": {"id": -100789}})
    telegram.validate_channel.assert_awaited_once_with("-100789", 42)
    assert store.get("target") == "-100789"
    assert store.get("target_label") == "-100789"


async def test_channel_link_normalized_and_permission_error_keeps_form(bot, store, telegram):
    telegram.validate_channel.side_effect = TelegramError(403)
    await press(bot, "channel")
    await message(bot, "https://t.me/my_channel")
    telegram.validate_channel.assert_awaited_once_with("@my_channel", 42)
    assert store.get("target") == "-100123"
    assert store.get("admin_input")
    assert "код 403" in telegram.send.call_args.args[1]


@pytest.mark.parametrize(
    "sender,chat_id,chat_type", [(9, 42, "private"), (42, 9, "private"), (42, 42, "group")]
)
async def test_admin_is_owner_private_only(bot, store, telegram, sender, chat_id, chat_type):
    await press(bot, "auto_confirm", sender, chat_id, chat_type)
    await press(bot, "add:telegram", sender, chat_id, chat_type)
    assert store.get("mode") == "manual"
    assert not store.get("admin_input")
    telegram.send.assert_not_called()
    telegram.call.assert_not_called()


async def test_auto_requires_separate_explicit_confirmation(bot, store):
    await press(bot, "mode")
    await press(bot, "auto")
    assert store.get("mode") == "manual"
    await press(bot, "auto_confirm")
    assert store.get("mode") == "auto"
    await press(bot, "manual")
    assert store.get("mode") == "manual"


async def test_pagination_reaches_all_sources_and_older_queue(bot, store, telegram):
    for i in range(2, 10):
        store.add_source("telegram", f"source{i}", str(i), "0")
    await press(bot, "sources:1")
    panel = last_panel(telegram)
    assert "@source9" in panel["text"]
    assert "@source2" not in panel["text"]
    store.ingest(
        1,
        [Item(str(i), f"Unique text {i}", "https://t.me/source/1") for i in range(30)],
        "29",
        "-100123",
    )
    await press(bot, "queue:4")
    assert "#1 ·" in last_panel(telegram)["text"]
    assert len(store.queue(limit=6, offset=24)) == 6


async def test_resume_requires_source_and_channel(bot, store, telegram):
    store.set("paused", "1")
    store.set("target", "")
    await press(bot, "resume")
    assert store.get("paused") == "1"
    assert "Мой канал" in last_panel(telegram)["text"]
    store.set("target", "-100123")
    store.remove_source(1)
    await press(bot, "resume")
    assert store.get("paused") == "1"
    assert "источник" in last_panel(telegram)["text"]


async def test_old_panel_edit_falls_back_to_new_message(bot, telegram):
    async def call(method, **kwargs):
        if method == "editMessageText":
            raise TelegramError(400)

    telegram.call.side_effect = call
    await press(bot, "home")
    assert "Админка" in telegram.send.call_args.args[1]


async def test_remove_cancels_failed_sends(bot, store):
    store.ingest(1, [Item("11", "Text", "url")], "11", "-100123")
    store.update_post(1, state="send_failed")
    await press(bot, "remove_confirm:1")
    assert store.post(1)["state"] == "skipped"


async def test_destination_public_username_cannot_be_source(bot, telegram, store):
    telegram.call.return_value = {"username": "destination"}
    await press(bot, "add:telegram")
    await message(bot, "@destination")
    bot.worker.sources["telegram"].resolve.assert_not_awaited()
    assert len(store.sources()) == 1


async def test_source_cannot_become_destination_by_numeric_id(bot, telegram, store):
    telegram.validate_channel.return_value = "-100999"
    telegram.call.return_value = {"username": "source"}
    await press(bot, "channel")
    await message(bot, "-100999")
    assert store.get("target") == "-100123"

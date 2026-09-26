import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from ai_poster.ai import QualityError
from ai_poster.bot import Bot
from ai_poster.db import Store
from ai_poster.sources import Item
from ai_poster.telegram import TelegramError


async def test_manual_end_to_end_only_owner_confirmation_publishes(
    worker, store, telegram, settings
):
    bot = Bot(store, worker, telegram, settings)
    await worker.cycle()
    assert store.post(1)["state"] == "ready"
    assert [call.args[0] for call in telegram.send.call_args_list] == [42]
    callback_data = telegram.send.call_args.kwargs["reply_markup"]["inline_keyboard"][0][0][
        "callback_data"
    ]
    callback = {
        "callback_query": {
            "id": "cb",
            "from": {"id": 42},
            "data": callback_data,
            "message": {"chat": {"id": 42, "type": "private"}},
        }
    }
    await bot.handle(callback)
    await bot.handle(callback)
    assert store.post(1)["state"] == "published"
    assert sum(c.args[0] == "-100123" for c in telegram.send.call_args_list) == 1
    await worker.cycle()
    assert store.counts() == {"published": 1}


async def test_unauthorized_user_and_group_cannot_mutate(worker, store, telegram, settings):
    bot = Bot(store, worker, telegram, settings)
    await bot.handle(
        {"message": {"from": {"id": 9}, "chat": {"id": 9, "type": "private"}, "text": "/mode auto"}}
    )
    await bot.handle(
        {"message": {"from": {"id": 42}, "chat": {"id": 5, "type": "group"}, "text": "/mode auto"}}
    )
    assert store.get("mode") == "manual"
    telegram.send.assert_not_called()


async def test_quality_failure_never_publishes_even_in_auto(worker, store, telegram):
    store.set("mode", "auto")
    worker.rewriter.rewrite.side_effect = QualityError("Число изменено")
    await worker.cycle()
    assert store.post(1)["state"] == "blocked"
    assert "проверку" in await worker.publish(1, manual=True)
    assert all(c.args[0] == 42 for c in telegram.send.call_args_list)


async def test_auto_mode_and_dedupe(worker, store, telegram):
    store.set("mode", "auto")
    await worker.cycle()
    await worker.cycle()
    assert store.counts() == {"published": 1}
    assert len(telegram.send.call_args_list) == 1
    store.add_source("x", "other", "987", "0")
    store.ingest(
        2,
        [Item("999", "Company  earned $10 million. ", "https://x.com/i/web/status/999")],
        "999",
        "-100123",
    )
    assert store.counts() == {"published": 1}
    assert store.sources()[1]["cursor"] == "999"


async def test_source_failure_does_not_advance_cursor(worker, store):
    worker.sources["telegram"].fetch.side_effect = httpx.ReadTimeout("timeout")
    await worker.cycle()
    assert store.sources()[0]["cursor"] == "10"
    assert store.sources()[0]["error"] == "ReadTimeout"
    assert not store.queue()


async def test_ambiguous_send_never_retried(worker, store, telegram):
    await worker.cycle()
    telegram.send.reset_mock()
    telegram.send.side_effect = httpx.ReadTimeout("lost response")
    result = await worker.publish(1, manual=True)
    assert "uncertain" in result
    assert store.post(1)["state"] == "uncertain"
    await worker.cycle()
    await worker.publish(1, manual=True)
    assert sum(c.args[0] == "-100123" for c in telegram.send.call_args_list) == 1


@pytest.mark.parametrize("code,state", [(403, "send_failed"), (500, "uncertain"), (429, "ready")])
async def test_telegram_send_errors(worker, store, telegram, code, state):
    await worker.cycle()
    telegram.send.side_effect = TelegramError(code, retry_after=30)
    await worker.publish(1, manual=True)
    assert store.post(1)["state"] == state
    if code == 429:
        assert store.post(1)["next_attempt"] > 0


async def test_no_publish_after_pause_or_target_change(worker, store, telegram):
    await worker.cycle()
    telegram.send.reset_mock()
    store.set("paused", "1")
    await worker.publish(1, manual=True)
    store.set("paused", "0")
    store.set("target", "-100999")
    await worker.publish(1, manual=True)
    telegram.send.assert_not_called()
    assert store.post(1)["state"] == "ready"


async def test_pause_while_ai_runs(worker, store, telegram):
    async def rewrite(*args):
        store.set("paused", "1")
        return "Draft"

    store.set("mode", "auto")
    worker.rewriter.rewrite.side_effect = rewrite
    await worker.cycle()
    telegram.send.assert_not_called()


async def test_retries_are_bounded(worker, store):
    worker.rewriter.rewrite.side_effect = httpx.ReadTimeout("timeout")
    for _ in range(3):
        if store.post(1):
            store.update_post(1, next_attempt=0)
        await worker.cycle()
    assert store.post(1)["state"] == "failed"
    assert store.post(1)["attempts"] == 3
    await worker.cycle()
    assert worker.rewriter.rewrite.await_count == 3


async def test_cycles_serialize(worker, store):
    await asyncio.gather(worker.cycle(), worker.cycle())
    assert store.counts() == {"ready": 1}
    worker.rewriter.rewrite.assert_awaited_once()


async def test_removing_source_cancels_queue(worker, store):
    await worker.cycle()
    store.remove_source(1)
    assert store.post(1)["state"] == "skipped"
    assert not store.sources()


def test_restart_preserves_settings_and_quarantines_inflight(tmp_path):
    path = str(tmp_path / "restart.sqlite3")
    db = Store(path)
    db.set("mode", "auto")
    db.add_source("telegram", "source", "123", "0")
    db.ingest(1, [Item("1", "text", "https://t.me/source/1")], "1", "target")
    db.update_post(1, state="sending")
    db.close()
    db = Store(path)
    assert db.get("mode") == "auto"
    assert db.post(1)["state"] == "uncertain"
    assert db.sources()[0]["cursor"] == "1"
    db.close()


async def test_connect_and_add_reject_feedback_loop(worker, store, telegram, settings):
    bot = Bot(store, worker, telegram, settings)
    telegram.validate_channel.return_value = "-100456"
    assert "источником" in await bot.command("/channel", ["@source"])
    worker.sources["telegram"].resolve = AsyncMock(return_value=("-100123", "10"))
    assert "источник" in await bot.command("/add", ["telegram", "@target"])
    assert len(store.sources()) == 1


async def test_stale_draft_button_cannot_publish_new_revision(worker, store, telegram, settings):
    await worker.cycle()
    callback_data = telegram.send.call_args.kwargs["reply_markup"]["inline_keyboard"][0][0][
        "callback_data"
    ]
    store.update_post(1, draft="A different draft requiring a new review")
    bot = Bot(store, worker, telegram, settings)
    await bot.handle(
        {
            "callback_query": {
                "id": "old",
                "from": {"id": 42},
                "data": callback_data,
                "message": {"chat": {"id": 42, "type": "private"}},
            }
        }
    )
    assert store.post(1)["state"] == "ready"
    assert all(c.args[0] == 42 for c in telegram.send.call_args_list)


async def test_malformed_send_result_is_uncertain(worker, store, telegram):
    await worker.cycle()
    telegram.send.return_value = {}
    await worker.publish(1, manual=True)
    assert store.post(1)["state"] == "uncertain"


async def test_publication_interval_applies_to_manual_confirmation(worker, store, telegram):
    await worker.cycle()
    store.set("next_send", "9999999999")
    telegram.send.reset_mock()
    assert "интервал" in await worker.publish(1, manual=True)
    telegram.send.assert_not_called()


def test_ingest_rolls_back_cursor_and_items_together(store):
    class BrokenItem:
        @property
        def text(self):
            raise RuntimeError("broken input")

    with pytest.raises(RuntimeError):
        store.ingest(1, [Item("11", "text", "url"), BrokenItem()], "12", "-100123")
    assert store.counts() == {}
    assert store.sources()[0]["cursor"] == "10"


async def test_unapproved_old_drafts_do_not_starve_new_notifications(worker, store, telegram):
    worker.sources["telegram"].fetch.return_value = (
        [
            Item(str(i), f"Unique source text {i}", f"https://t.me/source/{i}")
            for i in range(11, 23)
        ],
        "22",
    )
    for _ in range(3):
        await worker.cycle()
    assert store.counts() == {"ready": 12}
    assert len(telegram.send.call_args_list) == 12
    assert not store.work("ready", 50, unnotified_only=True)

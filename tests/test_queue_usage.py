import asyncio
import json
import time

import httpx
import pytest
import test_web
from test_ai import claude_completion, completion
from test_editorial import ingest, review, setup_selection

from ai_poster.ai import Rewrite, Rewriter
from ai_poster.db import Store, post_version
from ai_poster.editorial import ContentRules
from ai_poster.sources import Item, XSource
from ai_poster.usage import UsageMeter, token_cost, usage_scope

web = test_web.web
client = test_web.client


async def test_busy_source_does_not_starve_quiet_source(worker, store):
    setup_selection(store, worker, 2)
    ingest(store, 120)
    store.add_source("telegram", "quiet", "public:quiet", "0")
    store.ingest(
        2, [Item("1", "Quiet channel's distinct news.", "https://t.me/quiet/1")], "1", "-100123"
    )
    worker.rewriter.screen.side_effect = lambda posts, *args: [review(p["id"]) for p in posts]
    worker.rewriter.rewrite.side_effect = lambda text, url: "Draft: " + text
    await worker.cycle()
    assert store.post(121)["state"] == "ready"
    assert worker.rewriter.rewrite.await_count == 2
    assert worker.rewriter.screen.await_count == 5
    assert (
        store.db.execute("SELECT count(*) FROM posts WHERE editorial_revision IS NULL").fetchone()[
            0
        ]
        == 71
    )


async def test_screening_snapshot_does_not_expand_on_new_arrivals(worker, store):
    setup_selection(store, worker, 1)
    ingest(store, 20)

    async def screen(posts, *args):
        store.ingest(
            1,
            [Item(str(100 + p["id"]), f"Arrival {p['id']}", "url") for p in posts],
            "200",
            "-100123",
        )
        return [review(p["id"]) for p in posts]

    worker.rewriter.screen.side_effect = screen
    await worker.cycle()
    assert worker.rewriter.screen.await_count == 2
    assert worker.rewriter.rewrite.await_count == 1
    assert len(store.screening_work(store.content_rules().revision, 100)) == 20


def test_screen_rotation_includes_sources_beyond_first_window(store):
    for i in range(2, 62):
        store.add_source("telegram", f"source{i}", f"public:source{i}", "0")
        store.ingest(i, [Item("1", f"News from source {i}", "url")], "1", "-100123")
    rules = ContentRules()
    first = store.screening_work(rules.revision, 50)
    store.set("screen_source_cursor", str(first[-1]["source_id"]))
    second = store.screening_work(rules.revision, 50)
    assert second[0]["source_id"] == 52
    assert set(range(2, 62)) <= {p["source_id"] for p in first + second}


async def test_source_age_cutoff_and_manual_override(worker, store):
    setup_selection(store, worker, 5)
    now = time.time()
    store.ingest(
        1,
        [
            Item("1", "Old story", "url", now - 73 * 3600),
            Item("2", "Recent story", "url", now - 2 * 3600),
        ],
        "2",
        "-100123",
    )
    worker.rewriter.screen.side_effect = lambda posts, *args: [review(p["id"]) for p in posts]
    await worker.cycle()
    assert store.post(1)["state"] == "filtered"
    assert store.post(2)["state"] == "ready"
    assert store.post(1)["published_at"] < store.post(1)["created"] - 72 * 3600
    assert [p["id"] for p in worker.rewriter.screen.call_args.args[0]] == [2]
    store.choose_post(1, post_version(store.post(1)))
    await worker.cycle()
    assert store.post(1)["state"] == "ready"


async def test_age_limit_disabled_preserves_old_material(worker, store):
    setup_selection(store, worker)
    store.save_content_rules(ContentRules(max_age_hours=0))
    store.ingest(
        1, [Item("1", "Evergreen story", "url", time.time() - 100 * 86400)], "1", "-100123"
    )
    worker.rewriter.screen.return_value = [review(1)]
    await worker.cycle()
    assert store.post(1)["state"] == "ready"


async def test_auto_publication_rechecks_age_but_manual_can_approve(
    worker, store, telegram, monkeypatch
):
    now = time.time()
    monkeypatch.setattr("ai_poster.db.time.time", lambda: now)
    setup_selection(store, worker)
    store.ingest(1, [Item("1", "Recent story", "url", now - 71 * 3600)], "1", "-100123")
    worker.rewriter.screen.return_value = [review(1)]
    await worker.cycle()
    now += 2 * 3600
    store.set("mode", "auto")
    assert "устарела" in await worker.publish(1, manual=False)
    telegram.send.reset_mock()
    assert store.post(1)["state"] == "blocked"
    store.update_post(1, state="ready")
    assert "опубликован" in await worker.publish(1, manual=True)


def test_unknown_age_and_replay_backfill(store):
    store.ingest(1, [Item("1", "Text", "url")], "1", "-100123")
    assert store.post(1)["published_at"] is None
    store.ingest(1, [Item("1", "Text", "url", 1700000000)], "1", "-100123")
    assert store.post(1)["published_at"] == 1700000000
    assert store.post(1)["created"] != 1700000000
    assert store.db.execute("SELECT count(*) FROM posts").fetchone()[0] == 1


async def test_x_preserves_publication_time_even_after_history_import():
    result = {"data": [{"id": "2", "text": "News", "created_at": "2026-09-28T10:00:00Z"}]}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=result))
    ) as http:
        source = XSource("fake", http, lambda *args: True)
        items, _ = await source.fetch({"handle": "example", "external_id": "123", "cursor": "1"})
    assert items[0].published_at == 1790589600


@pytest.mark.parametrize(
    "provider,model,usage,expected",
    [
        (
            "openai",
            "gpt-4.1-mini",
            {
                "prompt_tokens": 1000,
                "completion_tokens": 100,
                "prompt_tokens_details": {"cached_tokens": 200},
            },
            0.00050,
        ),
        (
            "anthropic",
            "claude-sonnet-4-6",
            {
                "input_tokens": 1000,
                "output_tokens": 100,
                "cache_read_input_tokens": 200,
                "cache_creation_input_tokens": 100,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": 60,
                    "ephemeral_1h_input_tokens": 40,
                },
            },
            0.005025,
        ),
        ("openai", "unknown-model", {"prompt_tokens": 1000, "completion_tokens": 100}, None),
        ("openai", "gpt-4.1-mini", {}, None),
        ("anthropic", "claude-sonnet-4-6", {"input_tokens": -1, "output_tokens": 5}, None),
    ],
)
def test_token_estimates(provider, model, usage, expected):
    _, cost = token_cost(provider, model, usage)
    assert cost == pytest.approx(expected) if expected is not None else cost is None


async def test_usage_is_recorded_before_output_validation_and_survives_restart(
    settings, store, tmp_path
):
    ingest(store, 1)
    result = completion({"text": "Draft", "standalone": True, "reason": "ok"})
    result["usage"] = {"prompt_tokens": 1000, "completion_tokens": 100}
    result["choices"][0]["message"]["content"] = '{"wrong":true}'
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=result))
    ) as http:
        rewriter = Rewriter(settings, http, store)
        with usage_scope(1), pytest.raises(ValueError):
            await rewriter.structured(
                settings.rewrite_model, Rewrite, "instruction", {"text": "secret prompt"}
            )
    summary = UsageMeter(store).post_summary(1)
    assert summary["usd"] == pytest.approx(0.00056)
    row = store.db.execute("SELECT * FROM ai_calls").fetchone()
    assert row["status"] == "error"
    assert "secret prompt" not in json.dumps(dict(row))
    reopened = Store(str(tmp_path / "test.sqlite3"))
    assert UsageMeter(reopened).summary()["today"]["usd"] == pytest.approx(0.00056)
    reopened.close()


async def test_claude_usage_and_concurrent_post_scopes(settings, store):
    ingest(store, 2)
    settings.ai_provider = "anthropic"
    result = claude_completion({"text": "Draft", "standalone": True, "reason": "ok"})
    result["usage"] = {"input_tokens": 1000, "output_tokens": 100}

    async def respond(request):
        await asyncio.sleep(0)
        return httpx.Response(200, json=result)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        rw = Rewriter(settings, http, store)

        async def call(pid):
            with usage_scope(pid):
                await rw.structured("claude-sonnet-4-6", Rewrite, "instruction", {})

        await asyncio.gather(call(1), call(2))
    meter = UsageMeter(store)
    assert meter.post_summary(1)["calls"] == meter.post_summary(2)["calls"] == 1
    assert meter.summary()["today"]["usd"] == pytest.approx(0.009)


async def test_timeout_is_unknown_and_batch_cost_not_double_counted(settings, store):
    ingest(store, 2)
    meter = UsageMeter(store)
    with usage_scope(1, 2), meter.call("openai", "gpt-4.1-mini", "screening"):
        meter.response(
            "openai", "gpt-4.1-mini", {"usage": {"prompt_tokens": 1000, "completion_tokens": 100}}
        )

    def respond(request):
        raise httpx.ReadTimeout("secret-api-key")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        with usage_scope(1), pytest.raises(httpx.ReadTimeout):
            await Rewriter(settings, http, store).structured(
                settings.rewrite_model, Rewrite, "instruction", {}
            )
    assert meter.summary()["today"]["usd"] == pytest.approx(0.00056)
    assert meter.summary()["today"]["unknown"] == 1
    assert meter.post_summary(1)["usd"] == pytest.approx(0.00028)
    assert meter.post_summary(2)["usd"] == pytest.approx(0.00028)
    assert "secret-api-key" not in json.dumps(meter.summary())


async def test_usage_endpoint_and_age_rule_editor(client, store, worker):
    await worker.cycle()
    summary = (await client.get("/api/usage")).json()
    assert summary["today"]["calls"] == 0
    assert summary["tracking_since"] > 0
    old = (await client.get("/api/rules")).json()
    response = await client.put(
        "/api/rules",
        json={"rules": old["rules"] | {"max_age_hours": 48}, "revision": old["revision"]},
    )
    assert response.status_code == 200
    assert store.content_rules().max_age_hours == 48
    post = (await client.get("/api/posts/1")).json()
    assert post["published_at"] is None
    assert post["usage"] == {"calls": 0, "usd": 0, "unknown": 0}
    await client.post("/api/logout", json={})
    assert (await client.get("/api/usage")).status_code == 401


def test_nullable_cache_details_still_preserve_usage():
    assert token_cost(
        "anthropic",
        "claude-sonnet-4-6",
        {
            "input_tokens": 1000,
            "output_tokens": 100,
            "cache_creation": None,
        },
    )[1] == pytest.approx(0.0045)
    assert token_cost(
        "openai",
        "gpt-4.1-mini",
        {
            "prompt_tokens": 1000,
            "completion_tokens": 100,
            "prompt_tokens_details": None,
        },
    )[1] == pytest.approx(0.00056)


async def test_post_that_expires_during_writing_is_not_saved(worker, store, monkeypatch):
    now = time.time()
    monkeypatch.setattr("ai_poster.db.time.time", lambda: now)
    setup_selection(store, worker)
    store.ingest(1, [Item("1", "Expiring story", "url", now - 72 * 3600 + 1)], "1", "-100123")
    worker.rewriter.screen.return_value = [review(1)]

    async def rewrite(*args):
        nonlocal now
        now += 2
        return "Too late"

    worker.rewriter.rewrite.side_effect = rewrite
    await worker.cycle()
    assert store.post(1)["draft"] is None
    await worker.cycle()
    assert store.post(1)["state"] == "filtered"


async def test_image_calls_are_linked_and_billed_as_estimates(settings, store):
    import base64
    import io

    from PIL import Image
    from pydantic import SecretStr

    from ai_poster.images import ImageGenerator

    ingest(store, 1)
    output = io.BytesIO()
    Image.new("RGB", (32, 24), "green").save(output, "JPEG")
    result = {
        "has_nsfw_concepts": [False],
        "images": [
            {"url": "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode()}
        ],
    }
    settings.fal_key = SecretStr("test")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=result))
    ) as http:
        with usage_scope(1):
            await ImageGenerator(settings, http, store).generate("Test prompt")
    meter = UsageMeter(store)
    assert meter.post_summary(1)["usd"] == 0.003
    assert meter.summary()["recent"][0]["stage"] == "image"


def test_usage_day_month_boundaries_use_editorial_timezone(store, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    clock = datetime(2026, 10, 1, 0, 10, tzinfo=ZoneInfo("Europe/Berlin")).timestamp()
    monkeypatch.setattr("ai_poster.usage.time.time", lambda: clock)
    meter = UsageMeter(store)
    with meter.call("fal", "fal-ai/flux/schnell", "image"):
        meter.response("fal", "fal-ai/flux/schnell", {})
    clock -= 1200
    with meter.call("fal", "fal-ai/flux/schnell", "image"):
        meter.response("fal", "fal-ai/flux/schnell", {})
    clock += 1200
    summary = meter.summary()
    assert summary["today"]["calls"] == summary["month"]["calls"] == 1
    assert summary["month"]["usd"] == 0.003


async def test_single_slot_rotates_even_when_last_source_has_highest_score(worker, store, settings):
    setup_selection(store, worker, 5)
    settings.max_posts_per_cycle = 1
    ingest(store, 3)
    store.add_source("telegram", "highscore", "public:highscore", "0")
    store.ingest(
        2, [Item(str(i), f"Higher score story {i}", "url") for i in range(3)], "3", "-100123"
    )
    worker.rewriter.screen.side_effect = lambda posts, *args: [
        review(p["id"], 96 if p["id"] > 3 else 80) for p in posts
    ]
    worker.rewriter.rewrite.side_effect = lambda text, url: "Draft: " + text
    await worker.cycle()
    assert store.get("draft_source_cursor") == "2"
    await worker.cycle()
    assert store.get("draft_source_cursor") == "1"
    assert (
        store.db.execute(
            "SELECT count(*) FROM posts WHERE source_id=1 AND state='ready'"
        ).fetchone()[0]
        == 1
    )


async def test_partial_photo_delivery_can_finish_after_age_limit(worker, store, telegram):
    await worker.cycle()
    with store.db:
        store.db.execute("UPDATE posts SET published_at=? WHERE id=1", (time.time() - 80 * 3600,))
    store.update_post(1, image_message_id=99)
    store.set("mode", "auto")
    assert "опубликован" in await worker.publish(1, manual=False)
    assert store.post(1)["state"] == "published"
    telegram.send_photo.assert_not_awaited()

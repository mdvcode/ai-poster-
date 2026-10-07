import asyncio
import base64
import io
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from PIL import Image
from pydantic import SecretStr

from ai_poster.bot import Bot
from ai_poster.db import post_version
from ai_poster.images import ImageError, ImageGenerator
from ai_poster.telegram import Telegram, TelegramError
from ai_poster.web import WebAdmin, issue_login_code
from ai_poster.worker import draft_revision


@pytest.fixture
def jpeg():
    output = io.BytesIO()
    Image.new("RGB", (32, 24), "green").save(output, "JPEG")
    return output.getvalue()


@pytest.fixture
def image_settings(settings):
    settings.fal_key = SecretStr("private-fal-key")
    return settings


async def test_fal_request_is_one_safe_small_image(image_settings, jpeg):
    calls = []

    def respond(request):
        calls.append(request)
        payload = json.loads(request.content)
        assert request.url == "https://fal.run/fal-ai/flux/schnell"
        assert request.headers["Authorization"] == "Key private-fal-key"
        assert request.headers["X-Fal-No-Retry"] == "1"
        assert payload["num_images"] == 1
        assert payload["image_size"] == {"width": 1024, "height": 768}
        assert payload["enable_safety_checker"] and payload["sync_mode"]
        return httpx.Response(
            200,
            json={
                "has_nsfw_concepts": [False],
                "images": [{"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        assert (
            await ImageGenerator(image_settings, client).generate("An editorial illustration")
            == jpeg
        )
    assert len(calls) == 1


@pytest.mark.parametrize("status", [401, 402, 429, 500])
async def test_fal_errors_redacted_no_retry(image_settings, status):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(status, text="private-fal-key secret response")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ImageError) as exc:
            await ImageGenerator(image_settings, client).generate("prompt")
        assert "private-fal-key" not in str(exc.value)
        assert "secret response" not in str(exc.value)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "data",
    [
        {"has_nsfw_concepts": [True], "images": [{"url": "https://evil.test/image.jpg"}]},
        {"has_nsfw_concepts": [False], "images": [{"url": "https://evil.test/image.jpg"}]},
        {"has_nsfw_concepts": [False], "images": [{"url": "data:image/jpeg;base64,aGVsbG8="}]},
        {"images": []},
    ],
)
async def test_fal_rejects_unsafe_urls_and_non_images(image_settings, data):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json=data)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ImageError):
            await ImageGenerator(image_settings, client).generate("prompt")
    assert len(calls) == 1  # No arbitrary URL download.


@pytest.fixture
async def image_web(worker, store, telegram, image_settings, jpeg):
    web = WebAdmin(Bot(store, worker, telegram, image_settings))
    web.images.generate = AsyncMock(return_value=jpeg)
    worker.rewriter.image_prompt.return_value = "A green editorial illustration of automation"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=web.app),
        base_url="http://127.0.0.1:8765",
        headers={"X-Requested-With": "Tweebit"},
    ) as client:
        await client.post("/api/login", json={"code": issue_login_code(store)})
        await worker.cycle()
        yield web, client


async def test_generate_review_select_remove_and_stale_approval(image_web, store, jpeg):
    web, client = image_web
    version = post_version(store.post(1))
    response = await client.post("/api/posts/1/generate-image", json={"version": version})
    assert response.status_code == 200
    post = response.json()["post"]
    assert post["image_id"] is None
    assert post["image_candidate"]
    image_id = post["image_candidate"]
    assert (await client.get("/api/images/" + image_id)).content == jpeg
    selected = await client.post(
        "/api/posts/1/select-image",
        json={
            "version": version,
            "image_id": image_id,
        },
    )
    assert selected.status_code == 200
    assert selected.json()["post"]["version"] != version
    assert (await client.post("/api/posts/1/publish", json={"version": version})).status_code == 409
    removed = await client.post(
        "/api/posts/1/select-image",
        json={
            "version": selected.json()["post"]["version"],
            "image_id": None,
        },
    )
    assert removed.json()["post"]["image_id"] is None
    assert store.image_attempts_today() == 1
    await client.post("/api/logout", json={})
    assert (await client.get("/api/images/" + image_id)).status_code == 401
    web.images.generate.assert_awaited_once()


async def test_missing_key_and_quota_do_not_call_provider(image_web, store, image_settings):
    web, client = image_web
    version = post_version(store.post(1))
    image_settings.fal_key = SecretStr("")
    assert (
        await client.post("/api/posts/1/generate-image", json={"version": version})
    ).status_code == 503
    image_settings.fal_key = SecretStr("key")
    image_settings.image_daily_limit = 1
    web.images.generate.side_effect = ImageError("Unavailable")
    assert (
        await client.post("/api/posts/1/generate-image", json={"version": version})
    ).status_code == 502
    assert (
        await client.post("/api/posts/1/generate-image", json={"version": version})
    ).status_code == 409
    web.images.generate.assert_awaited_once()
    assert not store.image_busy(1)


async def test_concurrent_generation_and_edit_discard_stale_result(image_web, store, worker, jpeg):
    web, client = image_web
    started, release = asyncio.Event(), asyncio.Event()

    async def generate(prompt):
        started.set()
        await release.wait()
        return jpeg

    web.images.generate.side_effect = generate
    version = post_version(store.post(1))
    task = asyncio.create_task(
        client.post("/api/posts/1/generate-image", json={"version": version})
    )
    await started.wait()
    assert (
        await client.post("/api/posts/1/generate-image", json={"version": version})
    ).status_code == 409
    assert "Дождитесь" in await worker.publish(1, manual=True)
    edited = await client.patch("/api/posts/1", json={"version": version, "text": "Edited story."})
    assert edited.status_code == 200
    release.set()
    assert (await task).status_code == 409
    assert store.images_for(1) == []
    assert not store.image_busy(1)
    assert store.post(1)["draft"] == "Edited story."


async def attach(worker, store, jpeg, draft=None):
    await worker.cycle()
    if draft:
        store.update_post(1, draft=draft)
    image_id = store.save_image(1, jpeg, "test prompt")
    store.select_image(1, post_version(store.post(1)), image_id)
    return image_id


async def test_short_photo_caption_formatting(worker, store, telegram, jpeg):
    await attach(worker, store, jpeg, "🤖 **A headline**\n\nA brief story.")
    telegram.send.reset_mock()
    telegram.send_photo.return_value = {"message_id": 101}
    await worker.publish(1, manual=True)
    assert store.post(1)["state"] == "published"
    assert store.post(1)["message_id"] == 101
    call = telegram.send_photo.call_args
    assert call.args == ("-100123", jpeg)
    assert call.kwargs["caption"] == "🤖 A headline\n\nA brief story."
    assert call.kwargs["caption_entities"] == [{"type": "bold", "offset": 3, "length": 10}]
    telegram.send.assert_not_awaited()


async def test_long_post_rate_limit_retries_only_text(worker, store, telegram, jpeg):
    draft = "A detailed automation story. " * 50
    await attach(worker, store, jpeg, draft)
    telegram.send_photo.return_value = {"message_id": 101}
    telegram.send.side_effect = TelegramError(429, 1)
    await worker.publish(1, manual=True)
    assert store.post(1)["state"] == "ready"
    assert store.post(1)["image_message_id"] == 101
    with pytest.raises(ValueError):
        store.select_image(1, post_version(store.post(1)), None)
    with pytest.raises(ValueError):
        store.delete_post(1, post_version(store.post(1)))
    store.set("next_send", "0")
    store.update_post(1, next_attempt=0)
    telegram.send.side_effect = None
    await worker.publish(1, manual=True)
    assert store.post(1)["state"] == "published"
    assert telegram.send_photo.await_count == 1
    assert telegram.send.call_args.args[1] == draft


async def test_partial_send_error_requires_manual_resolution(worker, store, telegram, jpeg):
    await attach(worker, store, jpeg, "Long factual story. " * 80)
    telegram.send_photo.return_value = {"message_id": 101}
    # Failed text delivery, followed by successful owner notification.
    telegram.send.side_effect = [TelegramError(400), {"message_id": 102}]
    await worker.publish(1, manual=True)
    assert store.post(1)["state"] == "uncertain"
    await worker.publish(1, manual=True)
    assert telegram.send_photo.await_count == 1


async def test_auto_waits_for_image_choice_and_old_bot_approval_is_stale(
    worker,
    store,
    telegram,
    settings,
    jpeg,
):
    await worker.cycle()
    old_revision = draft_revision(store.post(1)["draft"])
    image_id = store.save_image(1, jpeg, "prompt")
    store.set("mode", "auto")
    assert "Выберите" in await worker.publish(1, manual=False)
    store.select_image(1, post_version(store.post(1)), image_id)
    bot = Bot(store, worker, telegram, settings)
    assert "изменился" in await bot.command("/post", ["1", old_revision])
    telegram.send_photo.assert_not_awaited()


async def test_send_photo_multipart_encoding(jpeg):
    def respond(request):
        assert request.url.path.endswith("/sendPhoto")
        assert "multipart/form-data" in request.headers["content-type"]
        assert b"image/jpeg" in request.content
        assert b"caption_entities" in request.content
        assert b'"offset": 3' in request.content
        assert b"disable_notification" in request.content
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 99}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await Telegram("fake", client).send_photo(
            123,
            jpeg,
            caption="test",
            caption_entities=[{"type": "bold", "offset": 3, "length": 4}],
            disable_notification=True,
        )
        assert result["message_id"] == 99


async def test_image_cannot_be_attached_to_another_post(worker, store, jpeg):
    from ai_poster.sources import Item

    await worker.cycle()
    store.ingest(1, [Item("12", "A different story.", "https://t.me/source/12")], "12", "-100123")
    store.save_draft(2, "A different draft.")
    image_id = store.save_image(1, jpeg, "prompt")
    with pytest.raises(ValueError, match="не относится"):
        store.select_image(2, post_version(store.post(2)), image_id)
    assert store.post(2)["image_id"] is None


async def test_interrupted_attempt_is_not_retried_on_restart(worker, store, tmp_path):
    from ai_poster.db import Store

    await worker.cycle()
    store.start_image(1, post_version(store.post(1)), 10)
    store.close()
    reopened = Store(str(tmp_path / "test.sqlite3"))
    try:
        assert not reopened.image_busy(1)
        assert reopened.image_attempts_today() == 1
        assert (
            reopened.db.execute("SELECT status FROM image_attempts").fetchone()[0] == "interrupted"
        )
        assert reopened.post(1)["state"] == "ready"
    finally:
        reopened.close()


async def test_photo_timeout_is_uncertain_and_not_retried(worker, store, telegram, jpeg):
    await attach(worker, store, jpeg)
    telegram.send_photo.side_effect = httpx.ReadTimeout("private details")
    await worker.publish(1, manual=True)
    assert store.post(1)["state"] == "uncertain"
    assert store.post(1)["reason"] == "ReadTimeout"
    await worker.publish(1, manual=True)
    assert telegram.send_photo.await_count == 1

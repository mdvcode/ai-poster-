import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from ai_poster.bot import Bot
from ai_poster.db import post_version
from ai_poster.web import WebAdmin, issue_login_code

BASE = "http://127.0.0.1:8765"
HEADERS = {"X-Requested-With": "Tweebit", "Origin": BASE}


@pytest.fixture
def web(worker, store, telegram, settings):
    return WebAdmin(Bot(store, worker, telegram, settings))


@pytest.fixture
async def client(web, store):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=web.app), base_url=BASE, headers=HEADERS
    ) as client:
        result = await client.post("/api/login", json={"code": issue_login_code(store)})
        assert result.status_code == 200
        yield client


async def test_requires_login_and_rejects_host_and_cross_origin(web):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web.app), base_url=BASE) as c:
        assert (await c.get("/")).status_code == 200
        assert (await c.get("/api/state")).status_code == 401
        assert (await c.get("/api/state", headers={"Host": "evil.test"})).status_code == 403
        assert (await c.post("/api/login", json={"code": "bad"})).status_code == 403
        assert (
            await c.post(
                "/api/login",
                json={"code": "bad"},
                headers=HEADERS | {"Origin": "https://evil.test"},
            )
        ).status_code == 403


async def test_login_code_single_use_session_logout_and_no_secrets(web, store):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=web.app), base_url=BASE, headers=HEADERS
    ) as c:
        code = issue_login_code(store)
        response = await c.post("/api/login", json={"code": code})
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=strict" in cookie
        assert (await c.post("/api/login", json={"code": code})).status_code == 401
        state = await c.get("/api/state")
        assert state.status_code == 200
        assert "fake-key" not in state.text and "fake-token" not in state.text
        assert (await c.post("/api/logout", json={})).status_code == 200
        assert (await c.get("/api/state")).status_code == 401


async def test_expired_code_and_rate_limit(web, store, monkeypatch):
    monkeypatch.setattr("ai_poster.web.time.time", lambda: 1000)
    code = issue_login_code(store)
    monkeypatch.setattr("ai_poster.web.time.time", lambda: 1601)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=web.app), base_url=BASE, headers=HEADERS
    ) as c:
        for _ in range(5):
            assert (await c.post("/api/login", json={"code": code})).status_code == 401
        assert (await c.post("/api/login", json={"code": code})).status_code == 429


async def test_edit_shorten_stale_revision_and_delete(client, worker, store):
    await worker.cycle()
    old = (await client.get("/api/posts/1")).json()
    response = await client.patch(
        "/api/posts/1", json={"text": "Short version.", "version": old["version"]}
    )
    assert response.status_code == 200
    assert response.json()["draft"] == "Short version."
    assert response.json()["edited_by_owner"] == 1
    assert (
        await client.patch("/api/posts/1", json={"text": "stale", "version": old["version"]})
    ).status_code == 409
    assert (await client.delete("/api/posts/1")).status_code == 415
    response = await client.request(
        "DELETE", "/api/posts/1", json={"version": response.json()["version"]}
    )
    assert response.status_code == 200
    assert store.post(1)["state"] == "deleted"
    assert (await client.get("/api/posts?filter=ready")).json()["total"] == 0
    assert (await client.get("/api/posts?filter=deleted")).json()["total"] == 1
    await worker.cycle()
    assert store.post(1)["state"] == "deleted"


@pytest.mark.parametrize("text", ["", "😀" * 2100])
async def test_empty_or_oversized_edits_blocked(client, worker, text):
    await worker.cycle()
    post = (await client.get("/api/posts/1")).json()
    result = await client.patch("/api/posts/1", json={"text": text, "version": post["version"]})
    assert result.status_code == 409


async def test_web_delete_during_ai_does_not_resurrect(client, worker, store, telegram):
    started, release = asyncio.Event(), asyncio.Event()

    async def rewrite(*args):
        started.set()
        await release.wait()
        return "Late draft"

    worker.rewriter.rewrite.side_effect = rewrite
    task = asyncio.create_task(worker.cycle())
    await started.wait()
    version = post_version(store.post(1))
    response = await asyncio.wait_for(
        client.request("DELETE", "/api/posts/1", json={"version": version}), 1
    )
    assert response.status_code == 200
    release.set()
    await task
    assert store.post(1)["state"] == "deleted"
    telegram.send.assert_not_called()


async def test_owner_only_web_code(worker, store, telegram, settings):
    bot = Bot(store, worker, telegram, settings)
    await bot.handle(
        {"message": {"from": {"id": 9}, "chat": {"id": 9, "type": "private"}, "text": "/web"}}
    )
    assert not store.get("web_login")
    await bot.handle(
        {"message": {"from": {"id": 42}, "chat": {"id": 42, "type": "private"}, "text": "/web"}}
    )
    assert store.get("web_login")
    assert "http://127.0.0.1:8765" in telegram.send.call_args.args[1]


async def test_publish_uses_saved_text_and_blocks_stale_version(client, worker, store, telegram):
    await worker.cycle()
    old = (await client.get("/api/posts/1")).json()
    updated = (
        await client.patch(
            "/api/posts/1", json={"text": "Edited English post.", "version": old["version"]}
        )
    ).json()
    assert (
        await client.post("/api/posts/1/publish", json={"version": old["version"]})
    ).status_code == 409
    response = await client.post("/api/posts/1/publish", json={"version": updated["version"]})
    assert response.json()["post"]["state"] == "published"
    sent = [c.args[1] for c in telegram.send.call_args_list if c.args[0] == "-100123"]
    assert sent == ["Edited English post."]
    response = await client.post("/api/posts/1/publish", json={"version": updated["version"]})
    assert response.status_code == 409
    assert (
        await client.request(
            "DELETE", "/api/posts/1", json={"version": post_version(store.post(1))}
        )
    ).status_code == 409


async def test_web_sources_use_existing_allowlist_controls(client, worker, store, telegram):
    worker.sources["telegram"].resolve = AsyncMock(return_value=("public:chosen", "20"))
    telegram.call.return_value = {"username": "mychannel"}
    assert (
        await client.post("/api/sources", json={"kind": "telegram", "handle": "@chosen"})
    ).status_code == 200
    assert store.source_is_active("telegram", "chosen", "public:chosen")
    assert (await client.request("DELETE", "/api/sources/2", json={})).status_code == 200
    assert not store.source_is_active("telegram", "chosen", "public:chosen")


async def test_security_headers_assets_and_unknown_filters(client):
    response = await client.get("/")
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"
    assert (await client.get("/assets/app.js")).status_code == 200
    assert (await client.get("/assets/.env")).status_code == 404
    assert (await client.get("/api/posts?filter=invalid")).status_code == 400


@pytest.mark.parametrize("mode", ["manual", "auto"])
async def test_manual_publish_while_collection_paused(client, worker, store, telegram, mode):
    await worker.cycle()
    store.set("mode", mode)
    await client.post("/api/control", json={"action": "pause"})
    telegram.send.reset_mock()
    worker.sources["telegram"].fetch.reset_mock()
    await worker.cycle()
    worker.sources["telegram"].fetch.assert_not_called()
    telegram.send.assert_not_called()
    post = (await client.get("/api/posts/1")).json()
    response = await client.post("/api/posts/1/publish", json={"version": post["version"]})
    assert response.status_code == 200
    assert response.json()["post"]["state"] == "published"
    telegram.send.assert_awaited_once_with("-100123", post["draft"])
    assert store.get("paused") == "1"

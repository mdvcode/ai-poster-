import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
import test_web
from test_ai import claude_completion, completion

from ai_poster.ai import QualityError, Rewriter
from ai_poster.db import Store, post_version
from ai_poster.editorial import ContentRules, Screening
from ai_poster.sources import Item

web = test_web.web
client = test_web.client


def review(post_id, score=80, suitable=True):
    q, r = divmod(score, 4)
    return Screening(
        id=post_id,
        suitable=suitable,
        relevance=q + r,
        usefulness=q,
        novelty=q,
        substance=q,
        reason="Конкретная новость об автоматизации.",
    )


def setup_selection(store, worker, limit=5):
    rules = ContentRules(daily_limit=limit)
    store.save_content_rules(rules)
    worker.sources["telegram"].fetch.return_value = ([], "30")
    worker.rewriter.find_duplicate.return_value = None
    return rules


def ingest(store, count=3):
    store.ingest(
        1,
        [
            Item(str(i), f"Distinct story number {i}.", f"https://t.me/source/{i}")
            for i in range(count)
        ],
        "30",
        "-100123",
    )


async def test_scores_before_rewriting_and_best_first(worker, store):
    rules = setup_selection(store, worker, limit=1)
    ingest(store)
    worker.rewriter.screen.return_value = [review(1, 72), review(2, 96), review(3, 90, False)]
    await worker.cycle()
    assert store.post(1)["state"] == "pending"
    assert store.post(2)["state"] == "ready"
    assert store.post(3)["state"] == "filtered"
    worker.rewriter.rewrite.assert_awaited_once_with(
        "Distinct story number 1.", "https://t.me/source/1"
    )
    assert store.drafts_today(rules) == 1
    await worker.cycle()
    assert worker.rewriter.rewrite.await_count == 1


async def test_daily_limit_survives_delete_restart_and_resets_local_midnight(
    worker, store, monkeypatch
):
    clock = datetime(2026, 9, 26, 23, 50, tzinfo=ZoneInfo("Europe/Berlin")).timestamp()
    monkeypatch.setattr("ai_poster.db.time.time", lambda: clock)
    rules = setup_selection(store, worker, 1)
    ingest(store, 2)
    worker.rewriter.screen.return_value = [review(1, 96), review(2)]
    await worker.cycle()
    store.delete_post(1, post_version(store.post(1)))
    path = store.db.execute("PRAGMA database_list").fetchone()[2]
    reopened = Store(path)
    assert reopened.drafts_today(rules) == 1
    reopened.close()
    await worker.cycle()
    assert store.post(2)["state"] == "pending"
    clock += 1200
    await worker.cycle()
    assert store.post(2)["state"] == "ready"
    assert store.drafts_today(rules) == 1


def test_day_bounds_handle_daylight_saving():
    rules = ContentRules()
    for date, hours in [("2026-03-29", 23), ("2026-10-25", 25)]:
        now = datetime.fromisoformat(date).replace(tzinfo=ZoneInfo(rules.timezone)).timestamp()
        start, end = rules.day_bounds(now)
        assert end - start == hours * 3600


async def test_better_newer_version_not_suppressed_by_lower_pending_id(worker, store):
    setup_selection(store, worker)
    ingest(store, 2)
    worker.rewriter.screen.return_value = [review(1, 76), review(2, 96)]
    worker.rewriter.find_duplicate.return_value = 2
    await worker.cycle()
    assert store.post(2)["state"] == "ready"
    assert store.post(1)["state"] == "duplicate"
    assert store.post(1)["duplicate_of"] == 2
    worker.rewriter.rewrite.assert_awaited_once()


async def test_filtered_choice_still_obeys_quality_and_quota(worker, store, client):
    rules = setup_selection(store, worker, 1)
    ingest(store, 2)
    worker.rewriter.screen.return_value = [review(1, 20), review(2, 20)]
    await worker.cycle()
    worker.rewriter.rewrite.assert_not_called()
    response = await client.post(
        "/api/posts/1/choose", json={"version": post_version(store.post(1))}
    )
    assert response.json()["post"]["editorial_override"] == 1
    worker.rewriter.rewrite.side_effect = QualityError("Изменён факт")
    await worker.cycle()
    assert store.post(1)["state"] == "blocked"
    assert store.drafts_today(rules) == 0
    store.save_draft(1, "Saved draft")
    await client.post("/api/posts/2/choose", json={"version": post_version(store.post(2))})
    await worker.cycle()
    assert store.post(2)["state"] == "pending"
    assert worker.rewriter.rewrite.await_count == 1


async def test_rules_update_rechecks_queue_preserves_drafts_and_pause(client, worker, store):
    old = (await client.get("/api/rules")).json()
    ingest(store)
    store.update_post(1, state="filtered")
    store.save_draft(2, "Keep my text")
    store.update_post(3, state="deleted")
    store.set("paused", "1")
    rules = old["rules"] | {"enabled": True, "topics": "Автоматизация, новости", "daily_limit": 3}
    response = await client.put("/api/rules", json={"rules": rules, "revision": old["revision"]})
    assert response.status_code == 200
    assert store.post(1)["state"] == "pending"
    assert store.post(2)["draft"] == "Keep my text"
    assert store.post(3)["state"] == "deleted"
    assert store.get("paused") == "1"
    assert (
        await client.put("/api/rules", json={"rules": rules, "revision": old["revision"]})
    ).status_code == 409


@pytest.mark.parametrize(
    "patch",
    [
        {"daily_limit": 0},
        {"min_score": 101},
        {"exclusions": ["unknown"]},
        {"timezone": "../../etc/passwd"},
        {"topics": " "},
        {"enabled": "yes"},
    ],
)
async def test_invalid_rules_do_not_save(client, store, patch):
    old = (await client.get("/api/rules")).json()
    response = await client.put(
        "/api/rules", json={"rules": old["rules"] | patch, "revision": old["revision"]}
    )
    assert response.status_code == 400
    assert store.content_rules().revision == old["revision"]


async def test_delete_feedback_is_used_by_next_screen(client, worker, store):
    setup_selection(store, worker)
    ingest(store, 2)
    response = await client.request(
        "DELETE", "/api/posts/1", json={"version": post_version(store.post(1)), "feedback": "ads"}
    )
    assert response.status_code == 200
    worker.rewriter.screen.return_value = [review(2)]
    await worker.cycle()
    assert worker.rewriter.screen.call_args.args[2] == [
        {"text": "Distinct story number 0.", "reason": "Реклама"}
    ]
    assert store.post(1)["state"] == "deleted"


async def test_rule_change_during_review_or_rewrite_discards_stale_result(worker, store):
    rules = setup_selection(store, worker)
    ingest(store, 1)

    async def screen(*args):
        store.save_content_rules(rules.model_copy(update={"topics": "Other topic"}))
        return [review(1)]

    worker.rewriter.screen.side_effect = screen
    await worker.cycle()
    assert store.post(1)["editorial_score"] is None
    worker.rewriter.rewrite.assert_not_called()
    worker.rewriter.screen.side_effect = None
    worker.rewriter.screen.return_value = [review(1)]

    async def rewrite(*args):
        store.save_content_rules(rules)
        return "Stale draft"

    worker.rewriter.rewrite.side_effect = rewrite
    await worker.cycle()
    assert store.post(1)["state"] == "pending"
    assert store.post(1)["draft"] is None
    assert store.drafts_today(rules) == 0


async def test_delete_or_choose_during_screen_not_overwritten(worker, store):
    setup_selection(store, worker)
    ingest(store, 2)

    async def screen(*args):
        store.delete_post(1, post_version(store.post(1)))
        store.choose_post(2, post_version(store.post(2)))
        return [review(1), review(2, 10)]

    worker.rewriter.screen.side_effect = screen
    await worker.cycle()
    assert store.post(1)["state"] == "deleted"
    assert store.post(2)["state"] == "ready"
    assert store.post(2)["editorial_override"] == 1


async def test_failed_screen_never_falls_back_to_unfiltered_generation(worker, store):
    setup_selection(store, worker)
    ingest(store, 1)
    worker.rewriter.screen.side_effect = ValueError("bad model output")
    for _ in range(3):
        store.update_post(1, next_attempt=0)
        await worker.cycle()
    assert store.post(1)["state"] == "failed"
    worker.rewriter.rewrite.assert_not_called()


@pytest.mark.parametrize("ids", [[1, 1], [2], [1, 2]])
async def test_ai_screen_validates_exact_response_ids(settings, ids):
    body = {"items": [review(i).model_dump() for i in ids]}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=completion(body)))
    ) as http:
        with pytest.raises(ValueError):
            await Rewriter(settings, http).screen(
                [{"id": 1, "text": "Untrusted text"}], ContentRules(), []
            )


async def test_claude_nested_screen_schema_and_local_bounds(settings):
    settings.ai_provider = "anthropic"
    body = {"items": [review(1).model_dump() | {"relevance": 26}]}

    def handler(request):
        payload = json.loads(request.content)
        schema = payload["output_config"]["format"]["schema"]
        assert "maximum" not in json.dumps(schema).replace("maximum=25", "")
        assert "Автоматизация" in payload["system"]
        return httpx.Response(200, json=claude_completion(body))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(ValueError):
            await Rewriter(settings, http).screen([{"id": 1, "text": "text"}], ContentRules(), [])


async def test_large_queue_creates_draft_without_waiting_for_every_post(worker, store):
    setup_selection(store, worker, 1)
    ingest(store, 60)

    async def screen(posts, *args):
        return [review(p["id"], 96 if p["id"] == 60 else 80) for p in posts]

    worker.rewriter.screen.side_effect = screen
    await worker.cycle()
    assert worker.rewriter.rewrite.await_count == 1
    assert worker.rewriter.screen.await_count == 5
    assert len(store.screening_work(store.content_rules().revision, 100)) == 10
    await worker.cycle()
    assert store.post(60)["state"] == "ready"
    assert worker.rewriter.rewrite.await_count == 1


async def test_owner_chosen_post_still_has_bounded_generation_retries(worker, store):
    setup_selection(store, worker)
    ingest(store, 1)
    store.choose_post(1, post_version(store.post(1)))
    worker.rewriter.rewrite.side_effect = httpx.ReadTimeout("timeout")
    for _ in range(3):
        store.update_post(1, next_attempt=0)
        await worker.cycle()
    assert store.post(1)["state"] == "failed"
    assert store.post(1)["attempts"] == 3
    worker.rewriter.screen.assert_not_called()

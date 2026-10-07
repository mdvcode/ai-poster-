import json

import httpx
import pytest
import test_web
from test_ai import REVIEW, REWRITE, completion

from ai_poster.ai import QualityError, Rewriter
from ai_poster.db import post_version
from ai_poster.formatting import post_kwargs, render_post
from ai_poster.telegram import Telegram

web = test_web.web
client = test_web.client


def test_bold_offsets_count_emoji_as_utf16_units():
    text, entities = render_post(
        "🤖 **Robot update**\n\n• **50 Hz** control.\n• Literal <b>tag</b>."
    )
    assert text == "🤖 Robot update\n\n• 50 Hz control.\n• Literal <b>tag</b>."
    assert entities == [
        {"type": "bold", "offset": 3, "length": 12},
        {"type": "bold", "offset": 19, "length": 5},
    ]
    text, entities = render_post("**🤖 Robot**")
    assert entities == [{"type": "bold", "offset": 0, "length": 8}]


@pytest.mark.parametrize(
    "body",
    ["plain <script>text</script>", "**unfinished", "***nested***", "** **", "**multi\nline**"],
)
def test_unrecognised_markup_is_preserved_as_plain_text(body):
    assert render_post(body) == (body, [])


async def test_actual_telegram_payload_uses_entities_not_parse_mode():
    def handler(request):
        payload = json.loads(request.content)
        assert payload["text"] == "📰 Report\n\nFacts <b>as text</b>."
        assert payload["entities"] == [{"type": "bold", "offset": 3, "length": 6}]
        assert "parse_mode" not in payload
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        text, kwargs = post_kwargs("📰 **Report**\n\nFacts <b>as text</b>.")
        await Telegram("fake", http).send(42, text, **kwargs)


async def test_preview_and_publication_match_and_old_confirmation_becomes_stale(
    worker, store, telegram
):
    await worker.cycle()
    old_version = post_version(store.post(1))
    body = "**A concrete result**\n\n• First fact.\n• Second fact."
    store.edit_post(1, body, old_version)
    assert post_version(store.post(1)) != old_version
    telegram.send.reset_mock()
    await worker.preview(store.post(1))
    preview = telegram.send.call_args
    await worker.publish(1, manual=True)
    published = telegram.send.call_args
    assert preview.args[1] == published.args[1] == render_post(body)[0]
    assert preview.kwargs["entities"] == published.kwargs["entities"]


async def test_new_generation_verifies_visible_text(settings):
    body = "**Company results**\n\nRevenue reached $10 million."
    outputs = [completion(REWRITE | {"text": body}), completion(REVIEW)]
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=outputs.pop(0))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await Rewriter(settings, http).rewrite("Company earned $10m.", "url")
    assert result == body
    assert json.loads(requests[1]["messages"][1]["content"])["candidate"] == render_post(body)[0]
    assert "**bold**" in requests[0]["messages"][0]["content"]


async def test_style_api_preserves_quota_and_ready_state(client, worker, store):
    await worker.cycle()
    old = store.post(1)
    quota = store.drafts_today(store.content_rules())
    worker.rewriter.restyle.return_value = "**Company results**\n\nThe company earned $10 million."
    result = await client.post("/api/posts/1/style", json={"version": post_version(old)})
    assert result.status_code == 200
    assert result.json()["post"]["state"] == "ready"
    assert "**Company results**" in result.json()["post"]["draft"]
    assert store.drafts_today(store.content_rules()) == quota
    assert (
        await client.post("/api/posts/1/style", json={"version": post_version(old)})
    ).status_code == 409
    worker.rewriter.restyle.assert_awaited_once()


async def test_style_quality_failure_keeps_existing_draft(client, worker, store):
    await worker.cycle()
    old = store.post(1)
    worker.rewriter.restyle.side_effect = QualityError("Изменено число")
    response = await client.post("/api/posts/1/style", json={"version": post_version(old)})
    assert response.status_code == 422
    assert store.post(1)["draft"] == old["draft"]


async def test_style_cannot_overwrite_concurrent_edit(client, worker, store):
    await worker.cycle()
    old = store.post(1)

    async def restyle(*args):
        store.edit_post(1, "Owner edited text.", post_version(old))
        return "**Late AI text**"

    worker.rewriter.restyle.side_effect = restyle
    response = await client.post("/api/posts/1/style", json={"version": post_version(old)})
    assert response.status_code == 409
    assert store.post(1)["draft"] == "Owner edited text."


async def test_restyle_rejects_semantic_changes(settings):
    review = {
        k: v
        for k, v in REVIEW.items()
        if k not in {"independent_presentation", "presentation_reason"}
    }
    outputs = [completion(REWRITE), completion(review | {"changed_facts": ["wrong date"]})]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=outputs.pop(0)))
    ) as http:
        with pytest.raises(QualityError, match="wrong date"):
            await Rewriter(settings, http).restyle("Initial post")


async def test_visible_length_excludes_formatting_markers(worker, store):
    await worker.cycle()
    store.edit_post(1, "**" + "a" * 4096 + "**", post_version(store.post(1)))
    with pytest.raises(ValueError):
        store.edit_post(1, "**" + "a" * 4097 + "**", post_version(store.post(1)))

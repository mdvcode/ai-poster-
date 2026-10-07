import asyncio
import hashlib
import logging
import time

from ai_poster.ai import QualityError
from ai_poster.formatting import post_kwargs
from ai_poster.telegram import TelegramError, utf16_len
from ai_poster.usage import usage_scope

log = logging.getLogger(__name__)


def draft_revision(text: str, image_id=None) -> str:
    body = text + ("\0" + image_id if image_id else "")
    return hashlib.sha256(body.encode()).hexdigest()[:12]


class Worker:
    def __init__(self, store, sources, rewriter, telegram, settings):
        self.store = store
        self.sources = sources
        self.rewriter = rewriter
        self.telegram = telegram
        self.settings = settings
        self.lock = asyncio.Lock()
        self.cycle_lock = asyncio.Lock()
        self.wakeup = asyncio.Event()
        self.processing_id = None

    async def notify(self, text: str, **kwargs):
        try:
            await self.telegram.send(self.settings.owner_id, text, **kwargs)
            return True
        except Exception as exc:
            log.warning("Owner notification failed: %s", type(exc).__name__)
            return False

    async def preview(self, post):
        revision = draft_revision(post["draft"], post["image_id"])
        text, formatting = post_kwargs(post["draft"])
        if post["image_id"]:
            image = self.store.image(post["image_id"])
            if image:
                await self.telegram.send_photo(
                    self.settings.owner_id,
                    image["content"],
                    caption="AI illustration · предпросмотр обложки",
                )
        await self.telegram.send(
            self.settings.owner_id,
            text,
            **formatting,
            reply_markup={
                "inline_keyboard": [
                    [
                        {
                            "text": f"Опубликовать #{post['id']}",
                            "callback_data": f"post:{post['id']}:{revision}",
                        },
                        {"text": "Пропустить", "callback_data": f"skip:{post['id']}"},
                    ],
                    [
                        {"text": "📄 Оригинал", "callback_data": f"admin:original:{post['id']}"},
                        {"text": "⚙️ Админка", "callback_data": "admin:home"},
                    ],
                ]
            },
        )

    async def deliver_preview(self, post):
        current = self.store.post(post["id"])
        if (
            not current
            or current["state"] != "ready"
            or current["draft"] != post["draft"]
            or current["image_id"] != post["image_id"]
        ):
            return
        try:
            await self.preview(post)
            current = self.store.post(post["id"])
            if (
                current
                and current["state"] == "ready"
                and current["draft"] == post["draft"]
                and current["image_id"] == post["image_id"]
            ):
                self.store.update_post(post["id"], notified=1)
        except Exception as exc:
            log.warning("Draft notification failed: %s", type(exc).__name__)

    async def cycle(self):
        async with self.cycle_lock:
            if self.store.get("paused") == "1" or not self.store.get("target"):
                return
            for source in self.store.sources():
                if self.store.get("paused") == "1":
                    break
                try:
                    target = self.store.get("target")
                    items, cursor = await self.sources[source["kind"]].fetch(source)
                    if target == self.store.get("target") and self.store.source_is_active(
                        source["kind"], source["handle"], source["external_id"]
                    ):
                        self.store.ingest(source["id"], items, cursor, target)
                except Exception as exc:
                    error = type(exc).__name__
                    log.warning("Source %s failed: %s", source["id"], error)
                    if source["error"] != error:
                        await self.notify(
                            f"Источник #{source['id']} недоступен: {error}. "
                            "Проверьте /sources и настройки доступа."
                        )
                    self.store.source_error(source["id"], error)
            rules = self.store.content_rules()
            self.store.expire_candidates(rules)
            if rules.enabled:
                if self.store.drafts_today(rules) < rules.daily_limit:
                    await self.screen_candidates(rules)
                candidates = self.store.editorial_work(rules, self.settings.max_posts_per_cycle)
            else:
                candidates = self.store.work("pending", self.settings.max_posts_per_cycle)
            for post in candidates:
                if self.store.content_rules().revision != rules.revision:
                    break
                if rules.enabled and self.store.drafts_today(rules) >= rules.daily_limit:
                    break
                if self.store.get("paused") == "1":
                    break
                if not self.still_pending(post, rules):
                    continue
                self.store.set("draft_source_cursor", str(post["source_id"]))
                self.processing_id = post["id"]
                try:
                    exact = (
                        self.store.selected_exact_duplicate(post)
                        if rules.enabled
                        else self.store.exact_duplicate(post)
                    )
                    candidates = (
                        []
                        if exact
                        else (
                            self.store.selected_duplicate_candidates(post)
                            if rules.enabled
                            else self.store.duplicate_candidates(post)
                        )
                    )
                    duplicate_id = exact["id"] if exact else None
                    if candidates:
                        with usage_scope(post["id"]):
                            duplicate_id = await self.rewriter.find_duplicate(
                                post["original"],
                                candidates,
                                **({"group_events": True} if rules.enabled else {}),
                            )
                    if not self.still_pending(post, rules):
                        continue
                    if isinstance(duplicate_id, int):
                        self.store.update_post(
                            post["id"],
                            state="duplicate",
                            duplicate_of=duplicate_id,
                            reason=f"Повтор поста #{duplicate_id}",
                        )
                        continue
                    with usage_scope(post["id"]):
                        draft = await self.rewriter.rewrite(post["original"], post["url"])
                    if not self.still_pending(post, rules):
                        continue
                    self.store.save_draft(post["id"], draft)
                    if self.store.get("paused") != "1" and self.store.get("mode") == "manual":
                        await self.deliver_preview(self.store.post(post["id"]))
                except QualityError as exc:
                    if not self.still_pending(post, rules):
                        continue
                    self.store.update_post(post["id"], state="blocked", reason=str(exc)[:1000])
                    await self.notify(
                        f"Пост #{post['id']} не прошёл проверку смысла.\n\n"
                        f"Причина: {str(exc)[:700]}",
                        reply_markup={
                            "inline_keyboard": [
                                [
                                    {
                                        "text": "Подробнее",
                                        "callback_data": f"admin:show:{post['id']}",
                                    }
                                ],
                                [
                                    {
                                        "text": "Оригинал",
                                        "callback_data": f"admin:original:{post['id']}",
                                    }
                                ],
                            ]
                        },
                    )
                except Exception as exc:
                    if not self.still_pending(post, rules):
                        continue
                    attempts = post["attempts"] + 1
                    self.store.update_post(
                        post["id"],
                        attempts=attempts,
                        state="failed" if attempts >= 3 else "pending",
                        reason=type(exc).__name__,
                        next_attempt=time.time() + min(3600, 60 * 2**attempts),
                    )
                    if attempts >= 3:
                        await self.notify(
                            f"Не удалось обработать #{post['id']}. "
                            f"После проверки настроек: /retry {post['id']}"
                        )
                finally:
                    self.processing_id = None
            for post in self.store.work(
                "ready",
                self.settings.max_posts_per_cycle,
                unnotified_only=self.store.get("mode") == "manual",
            ):
                if self.store.get("paused") == "1":
                    break
                if self.store.get("mode") == "auto":
                    async with self.lock:
                        await self.publish(post["id"], manual=False)
                elif not post["notified"]:
                    await self.deliver_preview(post)

    async def screen_candidates(self, rules):
        # Snapshot at most 50 candidates: later arrivals never extend this cycle's work.
        window = self.store.screening_work(rules.revision, 50)
        for offset in range(0, len(window), 10):
            if (
                self.store.get("paused") == "1"
                or self.store.content_rules().revision != rules.revision
            ):
                return
            batch = [p for p in window[offset : offset + 10] if self.still_pending(p, rules)]
            if not batch:
                continue
            self.store.set("screen_source_cursor", str(batch[-1]["source_id"]))
            # Rewrite cannot safely process overlong sources either; do not truncate facts.
            for post in batch:
                if len(post["original"]) > 16000:
                    self.store.update_post(
                        post["id"], state="blocked", reason="Исходник длиннее 16000 символов."
                    )
            batch = [post for post in batch if len(post["original"]) <= 16000]
            if not batch:
                continue
            try:
                with usage_scope(*(p["id"] for p in batch)):
                    results = await self.rewriter.screen(
                        [
                            {
                                "id": p["id"],
                                "text": p["original"],
                                "published_at": p["published_at"],
                            }
                            for p in batch
                        ],
                        rules,
                        self.store.feedback_examples(),
                    )
                for result in results:
                    post = next(p for p in batch if p["id"] == result.id)
                    if (
                        self.still_pending(post, rules)
                        and not self.store.post(post["id"])["editorial_override"]
                    ):
                        self.store.apply_screening(result, rules)
            except Exception as exc:
                for post in batch:
                    if (
                        not self.still_pending(post, rules)
                        or self.store.post(post["id"])["editorial_override"]
                    ):
                        continue
                    attempts = post["attempts"] + 1
                    self.store.update_post(
                        post["id"],
                        attempts=attempts,
                        state="failed" if attempts >= 3 else "pending",
                        reason=f"Не удалось оценить материал: {type(exc).__name__}",
                        next_attempt=time.time() + min(3600, 60 * 2**attempts),
                    )

    def still_pending(self, post, rules=None):
        current = self.store.post(post["id"])
        return (
            current
            and current["state"] == "pending"
            and current["target"] == self.store.get("target")
            and (rules is None or self.store.content_rules().revision == rules.revision)
            and (current["editorial_override"] or not self.store.stale(current, rules))
        )

    async def publish(self, post_id: int, *, manual: bool):
        """Caller holds lock; state is committed BEFORE the non-idempotent send."""
        post = self.store.post(post_id)
        if not post or post["state"] != "ready":
            return "Пост отсутствует, уже обработан или не прошёл проверку смысла."
        if (
            not manual
            and not post["editorial_override"]
            and not post["image_message_id"]
            and self.store.stale(post)
        ):
            self.store.update_post(
                post_id,
                state="blocked",
                reason="Новость устарела. Проверьте актуальность и выберите её вручную.",
            )
            return "Автопубликация остановлена: новость устарела."
        if self.store.image_busy(post_id):
            return "Дождитесь генерации картинки и проверьте результат."
        if not manual and post["image_candidate"]:
            return "Выберите обложку или подтвердите публикацию без картинки."
        if not manual and self.store.get("paused") == "1":
            return "Автопубликация на паузе. Сначала /resume."
        if not manual and self.store.get("mode") != "auto":
            return "Включён ручной режим."
        if post["target"] != self.store.get("target"):
            return "Канал изменён. Этот черновик привязан к прежнему каналу."
        duplicate_id = self.store.published_duplicate(post)
        if duplicate_id is not None:
            self.store.update_post(
                post_id,
                state="duplicate",
                duplicate_of=duplicate_id,
                reason=f"Повтор уже отправленного поста #{duplicate_id}",
            )
            return f"Повторная публикация остановлена: совпадение с постом #{duplicate_id}."
        next_send = max(float(self.store.get("next_send", "0")), post["next_attempt"])
        if time.time() < next_send:
            return "Действует интервал публикации. Повторите позже."
        self.store.update_post(post_id, state="sending")
        try:
            text, formatting = post_kwargs(post["draft"])
            if post["image_id"] and not post["image_message_id"]:
                image = self.store.image(post["image_id"])
                if not image:
                    raise ValueError("Image missing")
                if utf16_len(text) <= 1024:
                    photo_options = (
                        {"caption_entities": formatting["entities"]} if formatting else {}
                    )
                    result = await self.telegram.send_photo(
                        post["target"], image["content"], caption=text, **photo_options
                    )
                else:
                    photo = await self.telegram.send_photo(
                        post["target"],
                        image["content"],
                        caption="AI illustration",
                        disable_notification=True,
                    )
                    self.store.update_post(post_id, image_message_id=int(photo["message_id"]))
                    result = await self.telegram.send(post["target"], text, **formatting)
            else:
                result = await self.telegram.send(post["target"], text, **formatting)
            message_id = int(result["message_id"])
        except TelegramError as exc:
            if exc.code == 429:
                retry_at = time.time() + max(1, exc.retry_after)
                self.store.update_post(post_id, state="ready", next_attempt=retry_at)
                self.store.set("next_send", str(retry_at))
                return "Telegram ограничил частоту. Повторите позже."
            # 5xx may occur after Telegram accepted the request.
            state = (
                "uncertain"
                if (exc.code >= 500 or self.store.post(post_id)["image_message_id"])
                else "send_failed"
            )
            self.store.update_post(post_id, state=state, reason=str(exc))
        except Exception as exc:
            self.store.update_post(post_id, state="uncertain", reason=type(exc).__name__)
        else:
            self.store.update_post(post_id, state="published", message_id=message_id)
            self.store.set("next_send", str(time.time() + self.settings.publish_interval_seconds))
            return f"Пост #{post_id} опубликован."
        message = (
            f"Отправка #{post_id}: {self.store.post(post_id)['state']}. "
            "Проверьте канал и /queue. Автоматический повтор отключён."
        )
        await self.notify(message)
        return message

    async def run(self):
        while True:
            self.wakeup.clear()
            try:
                await self.cycle()
            except Exception as exc:
                log.error("Worker cycle failed: %s", type(exc).__name__)
            try:
                await asyncio.wait_for(self.wakeup.wait(), self.settings.poll_interval_seconds)
            except TimeoutError:
                pass

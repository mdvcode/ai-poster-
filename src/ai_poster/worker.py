import asyncio
import hashlib
import logging
import time

from ai_poster.ai import QualityError
from ai_poster.telegram import TelegramError

log = logging.getLogger(__name__)


def draft_revision(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


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
        revision = draft_revision(post["draft"])
        await self.telegram.send(
            self.settings.owner_id,
            post["draft"],
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
        if not current or current["state"] != "ready" or current["draft"] != post["draft"]:
            return
        try:
            await self.preview(post)
            current = self.store.post(post["id"])
            if current and current["state"] == "ready" and current["draft"] == post["draft"]:
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
            for post in self.store.work("pending", self.settings.max_posts_per_cycle):
                if self.store.get("paused") == "1":
                    break
                if not self.still_pending(post):
                    continue
                self.processing_id = post["id"]
                try:
                    exact = self.store.exact_duplicate(post)
                    candidates = [] if exact else self.store.duplicate_candidates(post)
                    duplicate_id = exact["id"] if exact else None
                    if candidates:
                        duplicate_id = await self.rewriter.find_duplicate(
                            post["original"], candidates
                        )
                    if not self.still_pending(post):
                        continue
                    if isinstance(duplicate_id, int):
                        self.store.update_post(
                            post["id"],
                            state="duplicate",
                            duplicate_of=duplicate_id,
                            reason=f"Повтор поста #{duplicate_id}",
                        )
                        continue
                    draft = await self.rewriter.rewrite(post["original"], post["url"])
                    if not self.still_pending(post):
                        continue
                    self.store.update_post(post["id"], draft=draft, state="ready", reason=None)
                    if self.store.get("paused") != "1" and self.store.get("mode") == "manual":
                        await self.deliver_preview(self.store.post(post["id"]))
                except QualityError as exc:
                    if not self.still_pending(post):
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
                    if not self.still_pending(post):
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

    def still_pending(self, post):
        current = self.store.post(post["id"])
        return (
            current
            and current["state"] == "pending"
            and current["target"] == self.store.get("target")
        )

    async def publish(self, post_id: int, *, manual: bool):
        """Caller holds lock; state is committed BEFORE the non-idempotent send."""
        post = self.store.post(post_id)
        if not post or post["state"] != "ready":
            return "Пост отсутствует, уже обработан или не прошёл проверку смысла."
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
            result = await self.telegram.send(post["target"], post["draft"])
            message_id = int(result["message_id"])
        except TelegramError as exc:
            if exc.code == 429:
                retry_at = time.time() + max(1, exc.retry_after)
                self.store.update_post(post_id, state="ready", next_attempt=retry_at)
                self.store.set("next_send", str(retry_at))
                return "Telegram ограничил частоту. Повторите позже."
            # 5xx may occur after Telegram accepted the request.
            state = "uncertain" if exc.code >= 500 else "send_failed"
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

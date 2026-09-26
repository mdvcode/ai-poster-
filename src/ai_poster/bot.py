import asyncio
import logging

from ai_poster.sources import normalize_handle
from ai_poster.telegram import TelegramError
from ai_poster.worker import draft_revision

log = logging.getLogger(__name__)
HELP = """AI Poster — Telegram/X → черновик → ваш канал.

/channel @my_channel — подключить канал (бот и вы — администраторы)
/add telegram @source — добавить Telegram-канал
/add x @account — добавить X-аккаунт
/sources — источники и ошибки
/remove ID — отключить источник и его очередь
/resume — запустить сбор и обработку
/pause — остановить сбор и публикации
/mode manual — черновики с подтверждением (по умолчанию)
/mode auto — включить автопубликацию проверенных текстов
/run — проверить источники сейчас
/queue — последние 20 незавершённых постов
/show ID — показать черновик / причину блокировки
/original ID — прочитать исходник
/post ID — подтвердить публикацию
/skip ID — пропустить пост
/retry ID — повторить обработку/отправку после явной ошибки
/resolve ID sent|retry — разрешить неопределённую отправку после проверки канала
/status — состояние

Добавленные источники читаются начиная с новых постов. Каждый текст проходит
перефразирование и отдельную AI-проверку смысла; ссылка на источник сохраняется.
"""


class Bot:
    def __init__(self, store, worker, telegram, settings):
        self.store = store
        self.worker = worker
        self.telegram = telegram
        self.settings = settings

    async def reply(self, text: str):
        # 1800 code points fit even with supplementary-plane emoji in UTF-16.
        for start in range(0, len(text), 1800):
            await self.telegram.send(self.settings.owner_id, text[start : start + 1800])

    async def handle(self, update):
        callback = update.get("callback_query")
        message = callback.get("message", {}) if callback else update.get("message", {})
        sender = callback.get("from", {}) if callback else message.get("from", {})
        if (
            sender.get("id") != self.settings.owner_id
            or message.get("chat", {}).get("type") != "private"
            or message["chat"].get("id") != self.settings.owner_id
        ):
            return
        if callback:
            # Consume update durably before command execution: no command replay after a crash.
            await self.telegram.call("answerCallbackQuery", callback_query_id=callback["id"])
            data = callback.get("data", "")
            fields = data.split(":")
            if (
                len(fields) not in {2, 3}
                or fields[0] not in {"post", "skip"}
                or not fields[1].isdigit()
                or (fields[0] == "post" and len(fields) != 3)
            ):
                return
            text = "/" + " ".join(fields)
        else:
            text = message.get("text", "")
        parts = text.split()
        if not parts:
            return
        command, args = parts[0].split("@")[0].lower(), parts[1:]
        try:
            result = await self.command(command, args)
        except (ValueError, IndexError):
            result = "Проверьте аргументы команды и доступ к каналу. Подсказка: /help."
        except TelegramError as exc:
            result = f"Telegram API: код {exc.code}. Проверьте права бота и доступность канала."
        except Exception as exc:
            log.warning("Command failed: %s", type(exc).__name__)
            result = f"Не удалось выполнить команду: {type(exc).__name__}. Проверьте настройки."
        if result:
            await self.reply(result)

    async def command(self, command: str, args: list[str]):
        if command in {"/start", "/help"}:
            return HELP
        if command == "/status":
            return (
                f"Канал: {self.store.get('target') or 'не подключён'}\n"
                f"Режим: {self.store.get('mode')}\nПауза: {self.store.get('paused') == '1'}\n"
                f"Источников: {len(self.store.sources())}\nПосты: {self.store.counts()}"
            )
        if command in {"/pause", "/resume"}:
            if command == "/resume" and not self.store.get("target"):
                return "Сначала подключите канал: /channel @my_channel."
            self.store.set("paused", "1" if command == "/pause" else "0")
            self.worker.wakeup.set()
            return "Пауза включена." if command == "/pause" else "Сбор и обработка включены."
        if command == "/mode":
            if args not in (["manual"], ["auto"]):
                return "Используйте /mode manual или /mode auto."
            self.store.set("mode", args[0])
            self.worker.wakeup.set()
            return f"Режим: {args[0]}."
        if command == "/run":
            self.worker.wakeup.set()
            return "Проверка запрошена." if self.store.get("paused") == "0" else "Сначала /resume."
        if command == "/sources":
            return (
                "\n".join(
                    f"#{s['id']} {s['kind']} @{s['handle']} — {s['error'] or 'OK'}"
                    for s in self.store.sources()
                )
                or "Источников пока нет."
            )
        if command == "/queue":
            return (
                "\n".join(
                    f"#{p['id']} · {p['state']} · /show {p['id']}" for p in self.store.queue()
                )
                or "Очередь пуста."
            )
        if command in {"/show", "/original"}:
            post = self.store.post(int(args[0]))
            if not post:
                return "Пост не найден."
            if command == "/original":
                return post["original"]
            if post["state"] == "ready":
                await self.worker.preview(post)
                return f"Черновик #{post['id']}. Канал: {post['target']}"
            return (
                f"#{post['id']} · {post['state']}\n{post['reason'] or ''}\n"
                f"{post['url']}\n{post['draft'] or ''}"
            )
        # Mutations of sources, targets and posts serialize with the worker.
        async with self.worker.lock:
            if command == "/channel":
                target = await self.telegram.validate_channel(args[0], self.settings.owner_id)
                if any(
                    s["kind"] == "telegram" and s["external_id"] == target
                    for s in self.store.sources()
                ):
                    return "Канал публикации не может быть источником."
                self.store.set("target", target)
                return f"Канал {target} подключён. Добавьте источники и выполните /resume."
            if command == "/add":
                kind, value = args
                if kind not in self.worker.sources:
                    return "Используйте /add telegram @channel или /add x @account."
                handle = normalize_handle(kind, value)
                external_id, cursor = await self.worker.sources[kind].resolve(handle)
                if kind == "telegram" and external_id == self.store.get("target"):
                    return "Нельзя использовать канал публикации как источник."
                self.store.add_source(kind, handle, external_id, cursor)
                return f"Источник {kind} @{handle} добавлен. Будут обрабатываться новые посты."
            if command == "/remove":
                self.store.remove_source(int(args[0]))
                return "Источник отключён, его незавершённая очередь отменена."
            if command == "/post":
                if len(args) == 2:
                    post = self.store.post(int(args[0]))
                    if not post or draft_revision(post["draft"] or "") != args[1]:
                        return "Черновик изменился. Откройте актуальный текст через /show ID."
                return await self.worker.publish(int(args[0]), manual=True)
            if command in {"/skip", "/retry", "/resolve"}:
                post = self.store.post(int(args[0]))
                if not post:
                    return "Пост не найден."
                if command == "/skip" and post["state"] not in {"published", "sending"}:
                    self.store.update_post(post["id"], state="skipped")
                    return "Пост пропущен."
                if command == "/retry" and post["state"] in {"failed", "blocked", "send_failed"}:
                    self.store.update_post(
                        post["id"],
                        state="pending",
                        attempts=0,
                        next_attempt=0,
                        notified=0,
                        draft=None,
                        reason=None,
                    )
                    self.worker.wakeup.set()
                    return "Пост будет заново обработан и проверен."
                if command == "/resolve" and post["state"] == "uncertain":
                    if args[1] == "sent":
                        self.store.update_post(post["id"], state="published")
                        return "Отмечено как опубликованное по вашей проверке."
                    if args[1] == "retry":
                        self.store.update_post(post["id"], state="ready", notified=0)
                        return "Разрешена повторная отправка. Используйте /post ID."
                return "Операция недоступна для текущего состояния поста."
        return "Неизвестная команда. /help"

    async def run(self):
        while True:
            try:
                updates = await self.telegram.call(
                    "getUpdates",
                    offset=int(self.store.get("offset")),
                    timeout=30,
                    allowed_updates=["message", "callback_query"],
                )
                for update in updates:
                    self.store.set("offset", str(update["update_id"] + 1))
                    try:
                        await self.handle(update)
                    except Exception as exc:
                        log.warning("Update handling failed: %s", type(exc).__name__)
            except Exception as exc:
                log.warning("Bot polling failed: %s", type(exc).__name__)
                await asyncio.sleep(5)

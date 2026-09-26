"""Owner-only Telegram admin screens; access is checked by Bot.handle."""

import json
import time

import httpx

from ai_poster.sources import normalize_handle
from ai_poster.telegram import TelegramError

PAGE_SIZE = 6
INPUT_TTL = 15 * 60
STATES = {
    "pending": "Ожидает обработки",
    "ready": "Готов к публикации",
    "blocked": "Не прошёл проверку смысла",
    "failed": "Ошибка обработки",
    "send_failed": "Ошибка отправки",
    "uncertain": "Отправка не подтверждена",
    "published": "Опубликован",
    "skipped": "Пропущен",
    "sending": "Отправляется",
}


def button(text, action):
    return {"text": text, "callback_data": f"admin:{action}"}


def back():
    return [button("← Админка", "home")]


class Admin:
    def __init__(self, bot):
        self.bot = bot
        self.store = bot.store

    async def screen(self, text, rows, message_id=None):
        markup = {"inline_keyboard": rows}
        if message_id is not None:
            try:
                await self.bot.telegram.call(
                    "editMessageText",
                    chat_id=self.bot.settings.owner_id,
                    message_id=message_id,
                    text=text,
                    reply_markup=markup,
                    link_preview_options={"is_disabled": True},
                )
                return
            except TelegramError as exc:
                if exc.code != 400:
                    raise
                # An old/deleted/unchanged panel can always be reopened as a new message.
        await self.bot.telegram.send(self.bot.settings.owner_id, text, reply_markup=markup)

    def clear_input(self):
        self.store.set("admin_input", "")

    async def home(self, message_id=None, notice=""):
        self.clear_input()
        paused = self.store.get("paused") == "1"
        manual = self.store.get("mode") == "manual"
        target = self.store.get("target_label") or self.store.get("target") or "не подключён"
        counts = self.store.counts()
        text = (
            "⚙️ Админка AI Poster\n\n"
            f"Канал публикации: {target}\n"
            f"Источников: {len(self.store.sources())}\n"
            f"Сбор: {'на паузе' if paused else 'включён'}\n"
            f"Публикация: {'после вашего подтверждения' if manual else 'автоматическая'}\n"
            f"Готовых черновиков: {counts.get('ready', 0)}\n\n"
            "Читаем только выбранные публичные источники. "
            "Вход в личный Telegram не используется.\n\n"
            "Добавьте источники, подключите свой канал и включите сбор."
        )
        if notice:
            text = notice + "\n\n" + text
        await self.screen(
            text,
            [
                [button("📥 Источники", "sources:0"), button("📣 Мой канал", "channel")],
                [button("📝 Черновики", "queue:0"), button("⚙️ Публикация", "mode")],
                [
                    button(
                        "▶️ Включить сбор" if paused else "⏸ Пауза", "resume" if paused else "pause"
                    )
                ],
                [button("🔄 Проверить сейчас", "run"), button("ℹ️ Как пользоваться", "help")],
            ],
            message_id,
        )

    async def sources(self, page=0, message_id=None):
        self.clear_input()
        all_sources = self.store.sources()
        page = max(0, min(page, max(0, (len(all_sources) - 1) // PAGE_SIZE)))
        sources = all_sources[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
        text = "📥 Источники контента\n\n"
        if not sources:
            text += "Пока нет источников. Выберите Telegram или X и отправьте ссылку."
        else:
            text += "\n".join(
                f"{'Telegram' if s['kind'] == 'telegram' else 'X'} · @{s['handle']}"
                + (f" — ошибка: {s['error']}" if s["error"] else "")
                for s in sources
            )
        text += "\n\nПри подключении читаем посты за последние 72 часа, затем новые записи."
        rows = [[button("＋ Telegram", "add:telegram"), button("＋ X", "add:x")]]
        rows += [[button(f"Удалить @{s['handle']}", f"remove:{s['id']}")] for s in sources]
        navigation = []
        if page:
            navigation.append(button("← Назад", f"sources:{page - 1}"))
        if (page + 1) * PAGE_SIZE < len(all_sources):
            navigation.append(button("Далее →", f"sources:{page + 1}"))
        if navigation:
            rows.append(navigation)
        rows.append(back())
        await self.screen(text, rows, message_id)

    async def prompt(self, kind, message_id=None):
        if kind == "x" and not self.bot.settings.x_bearer_token.get_secret_value().strip():
            self.clear_input()
            await self.screen(
                "Чтение X ещё не подключено.\n\nДля него нужен ключ X API с доступом к "
                "чтению публикаций. Добавьте X_BEARER_TOKEN в локальные настройки сервиса "
                "и перезапустите его. Затем здесь можно будет добавлять аккаунты по ссылке.",
                [[button("← Источники", "sources:0")], back()],
                message_id,
            )
            return
        self.store.set("admin_input", json.dumps({"kind": kind, "created": time.time()}))
        prompts = {
            "telegram": "Отправьте ссылку на Telegram-канал или его @username.\n\n"
            "Например: https://t.me/channel_name\nНужен публичный канал с веб-просмотром. "
            "Вход в личный аккаунт не нужен; закрытые каналы недоступны.",
            "x": "Отправьте ссылку на X-аккаунт или его @username.\n\n"
            "Например: https://x.com/account_name",
            "channel": "Подключение вашего канала\n\nДобавьте бота администратором с правом "
            "публикации. Затем отправьте @username, ссылку или перешлите сюда "
            "публикацию из канала. Для закрытого канала также подойдёт его ID.",
        }
        await self.screen(prompts[kind], [[button("Отмена", "home")]], message_id)

    async def input(self, message):
        try:
            pending = json.loads(self.store.get("admin_input"))
            if time.time() - pending["created"] > INPUT_TTL:
                await self.home(notice="Время ввода истекло. Выберите действие заново.")
                return
            kind = pending["kind"]
            if kind not in {"telegram", "x", "channel"}:
                raise ValueError("Unknown input state")
        except (ValueError, TypeError, KeyError):
            await self.home()
            return
        value = message.get("text", "").strip()
        try:
            if kind == "channel":
                origin = message.get("forward_origin", {})
                if origin.get("type") == "channel":
                    value = str(origin["chat"]["id"])
                elif not (value.startswith("-100") and value[1:].isdigit()):
                    value = "@" + normalize_handle("telegram", value)
                result = await self.bot.command("/channel", [value])
            else:
                value = normalize_handle(kind, value)
                result = await self.bot.command("/add", [kind, value])
        except TelegramError as exc:
            result = (
                f"Telegram не подтвердил доступ (код {exc.code}). Проверьте, что вы и бот "
                "— администраторы канала и бот может публиковать. Отправьте канал ещё раз."
            )
        except httpx.HTTPStatusError as exc:
            result = (
                f"Источник вернул ошибку {exc.response.status_code}. Проверьте доступ к API "
                "и повторите ввод или нажмите «Отмена»."
            )
        except (ValueError, IndexError, KeyError):
            result = (
                "Не удалось добавить канал/аккаунт. Проверьте ссылку, тип источника и доступ. "
                "Отправьте корректную ссылку или @username ещё раз."
            )
        except Exception as exc:
            result = f"Не удалось подключить: {type(exc).__name__}. Повторите ввод или отмените."
        else:
            await self.home(notice=result)
            return
        await self.screen(result, [[button("Отмена", "home")]])

    async def queue(self, page=0, message_id=None):
        self.clear_input()
        page = max(0, page)
        posts = self.store.queue(limit=PAGE_SIZE + 1, offset=page * PAGE_SIZE)
        if not posts and page:
            await self.queue(0, message_id)
            return
        text = "📝 Черновики и обработка\n\n"
        text += (
            "\n".join(
                f"#{p['id']} · {STATES.get(p['state'], p['state'])}" for p in posts[:PAGE_SIZE]
            )
            or "Очередь пуста."
        )
        rows = [[button(f"Открыть #{p['id']}", f"show:{p['id']}")] for p in posts[:PAGE_SIZE]]
        navigation = []
        if page:
            navigation.append(button("← Назад", f"queue:{page - 1}"))
        if len(posts) > PAGE_SIZE:
            navigation.append(button("Далее →", f"queue:{page + 1}"))
        if navigation:
            rows.append(navigation)
        rows.append(back())
        await self.screen(text, rows, message_id)

    async def mode(self, message_id=None):
        self.clear_input()
        manual = self.store.get("mode") == "manual"
        await self.screen(
            "⚙️ Публикация\n\nТекущий режим: "
            + ("с вашим подтверждением." if manual else "автоматический.")
            + "\nВ обоих режимах каждый текст проходит отдельную проверку смысла.",
            [
                [button("✓ С подтверждением" if manual else "С подтверждением", "manual")],
                [button("Автоматически" if manual else "✓ Автоматически", "auto")],
                back(),
            ],
            message_id,
        )

    async def callback(self, data, message_id=None):
        parts = data.split(":")
        action = parts[1] if len(parts) > 1 else "home"
        arg = parts[2] if len(parts) > 2 else ""
        self.clear_input()
        if action == "home":
            await self.home(message_id)
        elif action in {"sources", "queue"} and arg.isdigit():
            await getattr(self, action)(int(arg), message_id)
        elif action == "add" and arg in {"telegram", "x"}:
            await self.prompt(arg, message_id)
        elif action == "channel":
            await self.prompt("channel", message_id)
        elif action in {"pause", "resume", "run"}:
            if action == "resume" and not self.store.sources():
                await self.home(message_id, "Сначала добавьте хотя бы один источник.")
            elif action in {"resume", "run"} and not self.store.get("target"):
                await self.home(message_id, "Сначала подключите «Мой канал».")
            else:
                notice = await self.bot.command("/" + action, [])
                await self.home(message_id, notice)
        elif action == "mode":
            await self.mode(message_id)
        elif action == "manual":
            await self.bot.command("/mode", ["manual"])
            await self.mode(message_id)
        elif action == "auto":
            await self.screen(
                "Включить автоматическую публикацию?\n\nВсе проверенные черновики, "
                "включая уже готовые, будут публиковаться без вашего подтверждения, "
                "когда сбор включён.",
                [
                    [button("Включить автопубликацию", "auto_confirm")],
                    [button("Оставить текущий режим", "mode")],
                ],
                message_id,
            )
        elif action == "auto_confirm":
            await self.bot.command("/mode", ["auto"])
            await self.mode(message_id)
        elif action in {"remove", "remove_confirm"} and arg.isdigit():
            source = next((s for s in self.store.sources() if s["id"] == int(arg)), None)
            if not source:
                await self.sources(message_id=message_id)
            elif action == "remove":
                await self.screen(
                    f"Удалить источник @{source['handle']}?\n\nЧтение будет отключено, "
                    "его незавершённые черновики будут пропущены.",
                    [
                        [button("Удалить источник", f"remove_confirm:{arg}")],
                        [button("Отмена", "sources:0")],
                    ],
                    message_id,
                )
            else:
                await self.bot.command("/remove", [arg])
                await self.sources(message_id=message_id)
        elif action in {"show", "original", "retry"} and arg.isdigit():
            post = self.store.post(int(arg))
            if not post:
                await self.queue(message_id=message_id)
                return
            result = await self.bot.command("/" + action, [arg])
            if result:
                await self.bot.reply(result)
            rows = [[button("📄 Оригинал", f"original:{arg}")]]
            if post["state"] in {"failed", "blocked", "send_failed"}:
                rows.append([button("Повторить обработку", f"retry:{arg}")])
            rows += [[button("← Черновики", "queue:0")], back()]
            await self.screen(f"Пост #{arg}. Выберите действие.", rows)
        elif action == "help":
            await self.screen(
                "Как пользоваться\n\n1. «Мой канал» — подключите канал публикации.\n"
                "2. «Источники» → «＋ Telegram» или «＋ X» — отправьте ссылку.\n"
                "3. «Включить сбор» — бот загрузит посты за последние 72 часа, затем новые.\n"
                "4. Получите черновик и нажмите «Опубликовать» или «Пропустить».\n\n"
                "Проверка обычно запускается раз в несколько минут. Пауза останавливает "
                "сбор и публикации. Фотографии и видео пока не переносятся.",
                [back()],
                message_id,
            )
        else:
            await self.home(message_id)

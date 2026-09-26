import argparse
import asyncio
import fcntl
import logging
import os
import signal
from contextlib import contextmanager
from pathlib import Path

import httpx
from pydantic import ValidationError
from telethon import TelegramClient

from ai_poster.ai import Rewriter
from ai_poster.bot import Bot
from ai_poster.config import Settings
from ai_poster.db import Store
from ai_poster.sources import TelegramSource, XSource
from ai_poster.telegram import Telegram
from ai_poster.worker import Worker

log = logging.getLogger(__name__)


@contextmanager
def single_instance(path: str):
    lock_path = Path(path + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Другой экземпляр уже использует эту базу данных.") from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def reader_client(settings):
    if not settings.telegram_api_id or not settings.telegram_api_hash.get_secret_value():
        return None
    Path(settings.telegram_session_path).parent.mkdir(parents=True, exist_ok=True)
    return TelegramClient(
        settings.telegram_session_path,
        settings.telegram_api_id,
        settings.telegram_api_hash.get_secret_value(),
        flood_sleep_threshold=0,
    )


async def login(settings):
    reader = reader_client(settings)
    if reader is None:
        raise RuntimeError("Заполните TELEGRAM_API_ID и TELEGRAM_API_HASH в .env.")
    try:
        await reader.start()
        print("Telegram reader авторизован. Сессия сохранена локально.")
    finally:
        await reader.disconnect()


async def serve(settings):
    store = Store(settings.database_path)
    reader = reader_client(settings)
    tasks = []
    try:
        if reader:
            await reader.connect()
            if not await reader.is_user_authorized():
                await reader.disconnect()
                reader = None
                log.warning("Telegram reader disabled: run ai-poster login first")
        async with httpx.AsyncClient(timeout=30) as client:
            telegram = Telegram(settings.telegram_bot_token.get_secret_value(), client)
            await telegram.call("getMe")
            webhook = await telegram.call("getWebhookInfo")
            if webhook.get("url"):
                raise RuntimeError("У бота настроен webhook. Отключите его перед long polling.")
            try:
                await telegram.call(
                    "setMyCommands",
                    commands=[
                        {"command": "admin", "description": "Открыть админку"},
                        {"command": "start", "description": "Начать работу"},
                        {"command": "pause", "description": "Остановить сбор и публикации"},
                        {"command": "help", "description": "Все команды"},
                    ],
                    scope={"type": "chat", "chat_id": settings.owner_id},
                )
            except Exception as exc:
                log.warning("Could not set owner command menu: %s", type(exc).__name__)
            worker = Worker(
                store,
                {
                    "telegram": TelegramSource(reader),
                    "x": XSource(settings.x_bearer_token.get_secret_value(), client),
                },
                Rewriter(settings, client),
                telegram,
                settings,
            )
            bot = Bot(store, worker, telegram, settings)
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stop.set)
            log.info(
                "AI Poster started (mode=%s, paused=%s)", store.get("mode"), store.get("paused")
            )
            tasks = [
                asyncio.create_task(bot.run()),
                asyncio.create_task(worker.run()),
                asyncio.create_task(stop.wait()),
            ]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        if reader:
            await reader.disconnect()
        store.close()


def main():
    parser = argparse.ArgumentParser(description="AI content poster for Telegram")
    parser.add_argument("command", choices=["run", "login"], nargs="?", default="run")
    args = parser.parse_args()
    os.umask(0o077)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # HTTP request logging would expose the Telegram token in request URLs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telethon").setLevel(logging.WARNING)
    try:
        settings = Settings()
        with single_instance(settings.database_path):
            asyncio.run(login(settings) if args.command == "login" else serve(settings))
    except ValidationError as exc:
        parser.exit(2, f"Ошибка конфигурации .env:\n{exc}\n")
    except RuntimeError as exc:
        parser.exit(1, f"{exc}\n")
    except Exception as exc:
        parser.exit(1, f"Ошибка запуска: {type(exc).__name__}. Проверьте .env и доступ к API.\n")


if __name__ == "__main__":
    main()

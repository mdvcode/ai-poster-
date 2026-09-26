from unittest.mock import AsyncMock

import pytest

from ai_poster.config import Settings
from ai_poster.db import Store
from ai_poster.sources import Item
from ai_poster.worker import Worker


@pytest.fixture
def settings():
    return Settings(
        _env_file=None,
        telegram_bot_token="fake-token",
        owner_id=42,
        openai_api_key="fake-key",
        publish_interval_seconds=1,
    )


@pytest.fixture
def store(tmp_path):
    db = Store(str(tmp_path / "test.sqlite3"))
    db.set("target", "-100123")
    db.set("paused", "0")
    db.add_source("telegram", "source", "-100456", "10")
    yield db
    db.close()


@pytest.fixture
def telegram():
    api = AsyncMock()
    api.send.return_value = {"message_id": 100}
    return api


@pytest.fixture
def worker(store, settings, telegram):
    source = AsyncMock()
    source.fetch.return_value = (
        [Item("11", "Company earned $10 million.", "https://t.me/source/11")],
        "11",
    )
    rewriter = AsyncMock()
    rewriter.rewrite.return_value = "The company earned $10 million."
    return Worker(store, {"telegram": source}, rewriter, telegram, settings)

import sqlite3

import pytest

from ai_poster.db import Store
from ai_poster.sources import Item


def test_new_source_cutoff_and_reactivation_are_fixed(store, monkeypatch):
    monkeypatch.setattr("ai_poster.db.time.time", lambda: 1_800_000_000)
    store.add_source("x", "chosen", "123", "20")
    source = store.sources()[-1]
    cutoff = 1_800_000_000 - 72 * 3600
    assert source["history_since"] == cutoff
    monkeypatch.setattr("ai_poster.db.time.time", lambda: 1_800_000_060)
    store.add_source("x", "chosen", "123", "25")
    assert store.sources()[-1]["history_since"] == cutoff
    store.ingest(source["id"], [], "25", "target")
    assert store.sources()[-1]["history_since"] is None
    store.add_source("x", "chosen", "123", "26")
    assert store.sources()[-1]["history_since"] is None
    store.remove_source(source["id"])
    store.add_source("x", "chosen", "123", "26")
    assert store.sources()[-1]["history_since"] == cutoff + 60


def test_history_completion_is_atomic_and_deduplicated(store):
    source = store.sources()[0]
    cutoff = source["history_since"]

    class BrokenItem:
        @property
        def text(self):
            raise ValueError("failed")

    with pytest.raises(ValueError):
        store.ingest(source["id"], [Item("9", "history", "url"), BrokenItem()], "11", "target")
    assert store.sources()[0]["history_since"] == cutoff
    assert store.sources()[0]["cursor"] == "10"
    assert store.db.execute("SELECT count(*) FROM posts").fetchone()[0] == 0
    for _ in range(2):
        store.ingest(source["id"], [Item("9", "history", "url")], "11", "target")
    assert store.sources()[0]["history_since"] is None
    assert store.db.execute("SELECT count(*) FROM posts").fetchone()[0] == 1


def test_existing_database_schedules_history_once_for_active_sources(tmp_path, monkeypatch):
    path = str(tmp_path / "legacy.sqlite3")
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE sources (
            id INTEGER PRIMARY KEY, kind TEXT NOT NULL, handle TEXT NOT NULL,
            external_id TEXT NOT NULL, cursor TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
            error TEXT, UNIQUE(kind, external_id))""")
        db.execute("INSERT INTO sources VALUES (1,'telegram','chosen','public:chosen','20',1,NULL)")
        db.execute("INSERT INTO sources VALUES (2,'telegram','other','public:other','30',0,NULL)")
    monkeypatch.setattr("ai_poster.db.time.time", lambda: 1_800_000_000)
    db = Store(path)
    assert db.sources()[0]["history_since"] == 1_800_000_000 - 72 * 3600
    assert db.db.execute("SELECT history_since FROM sources WHERE id=2").fetchone()[0] is None
    db.close()
    monkeypatch.setattr("ai_poster.db.time.time", lambda: 1_800_000_600)
    db = Store(path)
    assert db.sources()[0]["history_since"] == 1_800_000_000 - 72 * 3600
    db.ingest(1, [Item("19", "older", "url")], "20", "target")
    db.close()
    db = Store(path)
    assert db.sources()[0]["history_since"] is None
    assert db.sources()[0]["cursor"] == "20"
    db.close()

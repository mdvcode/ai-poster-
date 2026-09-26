import hashlib
import sqlite3
import time
from pathlib import Path


class Store:
    """Small synchronous transactions; network/AI work must stay outside transactions."""

    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY, kind TEXT NOT NULL, handle TEXT NOT NULL,
                external_id TEXT NOT NULL, cursor TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
                error TEXT, UNIQUE(kind, external_id)
            );
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES sources(id),
                external_id TEXT NOT NULL, original TEXT NOT NULL, url TEXT NOT NULL,
                target TEXT NOT NULL, digest TEXT NOT NULL, draft TEXT,
                state TEXT NOT NULL DEFAULT 'pending', reason TEXT,
                attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
                notified INTEGER NOT NULL DEFAULT 0, message_id INTEGER,
                created REAL NOT NULL, UNIQUE(source_id, external_id), UNIQUE(target, digest)
            );
            CREATE INDEX IF NOT EXISTS posts_work ON posts(state, next_attempt, id);
        """)
        # A crash after sending but before recording success cannot be safely retried.
        with self.db:
            self.db.execute("UPDATE posts SET state='uncertain' WHERE state='sending'")
        for key, value in {"mode": "manual", "paused": "1", "target": "", "offset": "0"}.items():
            self.db.execute("INSERT OR IGNORE INTO settings VALUES (?,?)", (key, value))
        self.db.commit()

    def get(self, key: str, default: str = "") -> str:
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set(self, key: str, value: str):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, value))

    def sources(self):
        return self.db.execute("SELECT * FROM sources WHERE active=1 ORDER BY id").fetchall()

    def add_source(self, kind: str, handle: str, external_id: str, cursor: str):
        with self.db:
            self.db.execute(
                """
                INSERT INTO sources(kind,handle,external_id,cursor) VALUES (?,?,?,?)
                ON CONFLICT(kind,external_id) DO UPDATE SET active=1,handle=excluded.handle
            """,
                (kind, handle, external_id, cursor),
            )

    def remove_source(self, source_id: int):
        with self.db:
            self.db.execute("UPDATE sources SET active=0 WHERE id=?", (source_id,))
            self.db.execute(
                """UPDATE posts SET state='skipped'
                WHERE source_id=?
                AND state IN ('pending','ready','blocked','failed','send_failed')""",
                (source_id,),
            )

    def source_error(self, source_id: int, error: str | None):
        with self.db:
            self.db.execute("UPDATE sources SET error=? WHERE id=?", (error, source_id))

    def ingest(self, source_id: int, items, cursor: str, target: str):
        with self.db:
            for item in items:
                if not item.text.strip():
                    continue
                digest = hashlib.sha256(" ".join(item.text.split()).encode()).hexdigest()
                self.db.execute(
                    """INSERT OR IGNORE INTO posts
                    (source_id, external_id, original, url, target, digest, created)
                    VALUES (?,?,?,?,?,?,?)""",
                    (source_id, item.id, item.text, item.url, target, digest, time.time()),
                )
            self.db.execute(
                "UPDATE sources SET cursor=?,error=NULL WHERE id=?", (cursor, source_id)
            )

    def post(self, post_id: int):
        return self.db.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()

    def update_post(self, post_id: int, **fields):
        allowed = {"draft", "state", "reason", "attempts", "next_attempt", "notified", "message_id"}
        if not fields or not fields.keys() <= allowed:
            raise ValueError("Invalid post fields")
        with self.db:
            self.db.execute(
                f"UPDATE posts SET {','.join(f'{k}=?' for k in fields)} WHERE id=?",
                (*fields.values(), post_id),
            )

    def work(self, state: str, limit: int = 5, *, unnotified_only: bool = False):
        return self.db.execute(
            """SELECT p.* FROM posts p JOIN sources s ON s.id=p.source_id
            WHERE p.state=? AND p.next_attempt<=? AND s.active=1 AND p.target=?
            AND (?=0 OR p.notified=0)
            ORDER BY p.id LIMIT ?""",
            (state, time.time(), self.get("target"), int(unnotified_only), limit),
        ).fetchall()

    def queue(self, limit: int = 20, offset: int = 0):
        return self.db.execute(
            """SELECT * FROM posts
            WHERE state NOT IN ('published','skipped') ORDER BY id DESC LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()

    def counts(self):
        return dict(self.db.execute("SELECT state,COUNT(*) FROM posts GROUP BY state").fetchall())

    def close(self):
        self.db.close()

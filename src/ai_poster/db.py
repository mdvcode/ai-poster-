import hashlib
import sqlite3
import time
from pathlib import Path

from ai_poster.dedupe import content_key, near_identical, overlap
from ai_poster.editorial import FEEDBACK, ContentRules


def post_version(post) -> str:
    return hashlib.sha256(f"{post['state']}\0{post['draft'] or ''}".encode()).hexdigest()[:24]


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
                error TEXT, history_since REAL, UNIQUE(kind, external_id)
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
        # Upgrade existing installations once; keep the fixed cutoff across failed reads/restarts.
        if "history_since" not in {r[1] for r in self.db.execute("PRAGMA table_info(sources)")}:
            with self.db:
                self.db.execute("BEGIN")
                self.db.execute("ALTER TABLE sources ADD COLUMN history_since REAL")
                self.db.execute(
                    "UPDATE sources SET history_since=? WHERE active=1", (time.time() - 72 * 3600,)
                )
        columns = {r[1] for r in self.db.execute("PRAGMA table_info(posts)")}
        with self.db:
            for column, definition in (
                ("content_key", "TEXT"),
                ("duplicate_of", "INTEGER"),
                ("edited_by_owner", "INTEGER NOT NULL DEFAULT 0"),
                ("editorial_revision", "TEXT"),
                ("editorial_score", "INTEGER"),
                ("editorial_detail", "TEXT"),
                ("editorial_reason", "TEXT"),
                ("editorial_override", "INTEGER NOT NULL DEFAULT 0"),
                ("feedback", "TEXT"),
            ):
                if column not in columns:
                    self.db.execute(f"ALTER TABLE posts ADD COLUMN {column} {definition}")
            for row in self.db.execute("SELECT id,original FROM posts WHERE content_key IS NULL"):
                self.db.execute(
                    "UPDATE posts SET content_key=? WHERE id=?",
                    (content_key(row["original"]), row["id"]),
                )
            self.db.execute("CREATE INDEX IF NOT EXISTS posts_content ON posts(target,content_key)")
        self.db.execute("""CREATE TABLE IF NOT EXISTS draft_history (
            id INTEGER PRIMARY KEY, post_id INTEGER NOT NULL REFERENCES posts(id),
            target TEXT NOT NULL, generated_at REAL NOT NULL)""")
        self.db.commit()
        if self.get("draft_history_initialized") != "1":
            with self.db:
                self.db.execute("""INSERT INTO draft_history(post_id,target,generated_at)
                    SELECT id,target,created FROM posts WHERE draft IS NOT NULL""")
                self.db.execute(
                    "INSERT OR REPLACE INTO settings VALUES ('draft_history_initialized','1')"
                )
        # A crash after sending but before recording success cannot be safely retried.
        with self.db:
            self.db.execute("UPDATE posts SET state='uncertain' WHERE state='sending'")
        for key, value in {
            "mode": "manual",
            "paused": "1",
            "target": "",
            "offset": "0",
            "content_rules": ContentRules().model_dump_json(),
        }.items():
            self.db.execute("INSERT OR IGNORE INTO settings VALUES (?,?)", (key, value))
        self.db.commit()

        # Remove only our exact legacy footer, without changing already sent/uncertain posts.
        if self.get("source_footer_removed") != "1":
            with self.db:
                rows = self.db.execute(
                    """SELECT id,draft,url FROM posts WHERE draft IS NOT NULL
                    AND state NOT IN ('published','sending','uncertain')"""
                ).fetchall()
                for row in rows:
                    body = row["draft"]
                    for label in ("Source", "Источник"):
                        body = body.removesuffix(f"\n\n{label}: {row['url']}")
                    if body != row["draft"]:
                        self.db.execute(
                            "UPDATE posts SET draft=?,notified=0 WHERE id=?", (body, row["id"])
                        )
                self.db.execute(
                    "INSERT OR REPLACE INTO settings VALUES ('source_footer_removed','1')"
                )

    def get(self, key: str, default: str = "") -> str:
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set(self, key: str, value: str):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, value))

    def sources(self):
        return self.db.execute("SELECT * FROM sources WHERE active=1 ORDER BY id").fetchall()

    def source_is_active(self, kind: str, handle: str, external_id: str) -> bool:
        return (
            self.db.execute(
                "SELECT 1 FROM sources WHERE active=1 AND kind=? AND handle=? AND external_id=?",
                (kind, handle, external_id),
            ).fetchone()
            is not None
        )

    def add_source(self, kind: str, handle: str, external_id: str, cursor: str):
        with self.db:
            self.db.execute(
                """
                INSERT INTO sources(kind,handle,external_id,cursor,history_since) VALUES (?,?,?,?,?)
                ON CONFLICT(kind,external_id) DO UPDATE SET active=1,handle=excluded.handle,
                history_since=CASE WHEN sources.active=0 THEN excluded.history_since
                                   ELSE sources.history_since END
            """,
                (kind, handle, external_id, cursor, time.time() - 72 * 3600),
            )

    def remove_source(self, source_id: int):
        with self.db:
            self.db.execute("UPDATE sources SET active=0 WHERE id=?", (source_id,))
            self.db.execute(
                """UPDATE posts SET state='skipped'
                WHERE source_id=?
                AND state IN ('pending','ready','blocked','failed','send_failed','filtered')""",
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
                    (source_id, external_id, original, url, target, digest, created,content_key)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        source_id,
                        item.id,
                        item.text,
                        item.url,
                        target,
                        digest,
                        time.time(),
                        content_key(item.text),
                    ),
                )
            self.db.execute(
                "UPDATE sources SET cursor=?,error=NULL,history_since=NULL WHERE id=?",
                (cursor, source_id),
            )

    def post(self, post_id: int):
        return self.db.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()

    def update_post(self, post_id: int, **fields):
        allowed = {
            "draft",
            "state",
            "reason",
            "attempts",
            "next_attempt",
            "notified",
            "message_id",
            "duplicate_of",
            "edited_by_owner",
            "editorial_revision",
            "editorial_score",
            "editorial_detail",
            "editorial_reason",
            "editorial_override",
            "feedback",
        }
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
            WHERE state NOT IN ('published','skipped','deleted','duplicate','filtered')
            ORDER BY id DESC LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()

    def counts(self):
        return dict(self.db.execute("SELECT state,COUNT(*) FROM posts GROUP BY state").fetchall())

    def exact_duplicate(self, post):
        return self.db.execute(
            "SELECT id FROM posts WHERE target=? AND content_key=? AND id<? ORDER BY id LIMIT 1",
            (post["target"], content_key(post["original"]), post["id"]),
        ).fetchone()

    def duplicate_candidates(self, post):
        rows = self.db.execute(
            """SELECT id,original FROM posts WHERE target=? AND id<? AND created>=?
            AND state!='duplicate' ORDER BY id DESC LIMIT 200""",
            (post["target"], post["id"], time.time() - 14 * 86400),
        ).fetchall()
        ranked = sorted(
            ((overlap(post["original"], r["original"]), dict(r)) for r in rows),
            key=lambda pair: pair[0],
            reverse=True,
        )
        return [r for score, r in ranked[:5] if score >= 0.1]

    def published_duplicate(self, post):
        rows = self.db.execute(
            """SELECT * FROM posts WHERE target=? AND id!=?
            AND state IN ('published','sending','uncertain')""",
            (post["target"], post["id"]),
        ).fetchall()
        for previous in rows:
            if near_identical(post["draft"] or "", previous["draft"] or "") or near_identical(
                post["original"], previous["original"]
            ):
                return previous["id"]
        return None

    def delete_post(self, post_id: int, version: str, feedback: str | None = None):
        post = self.post(post_id)
        if not post or post_version(post) != version:
            raise ValueError("Пост изменился. Обновите страницу.")
        if post["state"] in {"published", "sending", "uncertain"}:
            raise ValueError(
                "Пост уже отправлен или отправляется; удалить его из черновиков нельзя."
            )
        if feedback is not None and feedback not in FEEDBACK:
            raise ValueError("Неизвестная причина удаления")
        self.update_post(post_id, state="deleted", feedback=feedback)

    def edit_post(self, post_id: int, text: str, version: str):
        from ai_poster.telegram import utf16_len

        post = self.post(post_id)
        if not post or post_version(post) != version or post["state"] != "ready":
            raise ValueError("Черновик изменился или ещё не готов. Обновите страницу.")
        body = text.strip()
        if not body:
            raise ValueError("Текст не может быть пустым.")
        draft = body
        if utf16_len(draft) > 4096:
            raise ValueError("Текст превышает лимит Telegram: 4096 символов.")
        self.update_post(post_id, draft=draft, notified=0, edited_by_owner=1)

    def content_rules(self):
        raw = self.get("content_rules")
        return ContentRules.model_validate_json(raw) if raw else ContentRules()

    def save_content_rules(self, rules):
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO settings VALUES ('content_rules',?)",
                (rules.model_dump_json(),),
            )
            # Re-evaluate unsent candidates; preserve owner choices and existing drafts.
            self.db.execute("""UPDATE posts SET state='pending', editorial_revision=NULL,
                editorial_score=NULL,editorial_detail=NULL,editorial_reason=NULL,
                reason=NULL,attempts=0,next_attempt=0
                WHERE state IN ('pending','filtered') AND draft IS NULL""")

    def screening_work(self, revision, limit=10):
        return self.db.execute(
            """SELECT p.* FROM posts p JOIN sources s ON s.id=p.source_id
            WHERE p.state='pending' AND s.active=1 AND p.target=? AND p.editorial_override=0
            AND coalesce(p.editorial_revision,'')!=? AND p.next_attempt<=?
            ORDER BY p.id LIMIT ?""",
            (self.get("target"), revision, time.time(), limit),
        ).fetchall()

    def apply_screening(self, result, rules):
        accepted = result.suitable and result.score >= rules.min_score
        reason = result.reason
        if result.suitable and result.score < rules.min_score:
            reason = f"Оценка {result.score} ниже порога {rules.min_score}. " + reason
        self.update_post(
            result.id,
            editorial_revision=rules.revision,
            editorial_score=result.score,
            editorial_detail=result.model_dump_json(),
            editorial_reason=result.reason,
            state="pending" if accepted else "filtered",
            reason=None if accepted else reason,
            attempts=0,
            next_attempt=0,
        )

    def editorial_work(self, rules, limit):
        return self.db.execute(
            """SELECT p.* FROM posts p JOIN sources s ON s.id=p.source_id
            WHERE p.state='pending' AND p.target=? AND s.active=1 AND p.next_attempt<=?
            AND (p.editorial_override=1 OR (p.editorial_revision=? AND p.editorial_score>=?))
            ORDER BY p.editorial_override DESC, p.editorial_score DESC,
                json_extract(p.editorial_detail,'$.substance') DESC, p.id
            LIMIT ?""",
            (self.get("target"), time.time(), rules.revision, rules.min_score, limit),
        ).fetchall()

    def drafts_today(self, rules):
        start, end = rules.day_bounds(time.time())
        return self.db.execute(
            """SELECT count(*) FROM draft_history WHERE target=?
            AND generated_at>=? AND generated_at<?""",
            (self.get("target"), start, end),
        ).fetchone()[0]

    def save_draft(self, post_id, draft):
        with self.db:
            self.db.execute(
                "UPDATE posts SET draft=?,state='ready',reason=NULL WHERE id=?", (draft, post_id)
            )
            self.db.execute(
                """INSERT INTO draft_history(post_id,target,generated_at)
                SELECT id,target,? FROM posts WHERE id=?""",
                (time.time(), post_id),
            )

    def choose_post(self, post_id, version):
        post = self.post(post_id)
        if (
            not post
            or post_version(post) != version
            or post["state"] not in {"filtered", "pending"}
        ):
            raise ValueError("Пост изменился. Обновите страницу.")
        source = self.db.execute(
            "SELECT active FROM sources WHERE id=?", (post["source_id"],)
        ).fetchone()
        if not source[0] or post["target"] != self.get("target"):
            raise ValueError("Источник отключён или канал публикации изменился.")
        self.update_post(
            post_id, state="pending", editorial_override=1, reason=None, attempts=0, next_attempt=0
        )

    def feedback_examples(self):
        return [
            {"text": r["original"][:2000], "reason": FEEDBACK[r["feedback"]]}
            for r in self.db.execute(
                """SELECT original,feedback FROM posts
                WHERE target=? AND feedback IS NOT NULL ORDER BY id DESC LIMIT 12""",
                (self.get("target"),),
            )
            if r["feedback"] in FEEDBACK
        ]

    def selected_duplicate_candidates(self, post):
        # Ranking may select a newer, richer source first: do not restrict by lower id.
        rows = self.db.execute(
            """SELECT id,original,state FROM posts
            WHERE target=? AND id!=? AND state IN
            ('ready','published','sending','uncertain','deleted','skipped')
            AND created>=? ORDER BY id DESC LIMIT 100""",
            (post["target"], post["id"], time.time() - 14 * 86400),
        ).fetchall()
        ranked = sorted(rows, key=lambda r: overlap(post["original"], r["original"]), reverse=True)
        return [dict(r) for r in ranked[:20]]

    def selected_exact_duplicate(self, post):
        return self.db.execute(
            """SELECT id FROM posts WHERE target=? AND content_key=? AND id!=?
            AND state IN ('ready','published','sending','uncertain','deleted','skipped')
            ORDER BY id LIMIT 1""",
            (post["target"], content_key(post["original"]), post["id"]),
        ).fetchone()

    def close(self):
        self.db.close()

"""Durable usage estimates. Never store prompts, provider bodies or credentials."""

import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from zoneinfo import ZoneInfo

_scope = ContextVar("usage_scope", default=((), None))
_call = ContextVar("usage_call", default=None)
RATE_VERSION = "2026-09-28"
# USD per million tokens: input, output, cache read, 5m write, 1h write.
RATES = {
    ("anthropic", "claude-sonnet-4-6"): (3, 15, 0.3, 3.75, 6),
    ("openai", "gpt-4.1-mini"): (0.4, 1.6, 0.1, 0, 0),
    ("openai", "gpt-4.1-mini-2025-04-14"): (0.4, 1.6, 0.1, 0, 0),
}


@contextmanager
def usage_scope(*post_ids, stage=None):
    token = _scope.set((tuple(dict.fromkeys(post_ids)), stage))
    try:
        yield
    finally:
        _scope.reset(token)


def token_cost(provider, model, usage):
    """Return counters and estimate, or unknown when usage/rates are incomplete."""
    if not isinstance(usage, dict):
        return {}, None
    try:
        if provider == "anthropic":
            inp, out = usage["input_tokens"], usage["output_tokens"]
            cached = usage.get("cache_read_input_tokens", 0)
            writes = usage.get("cache_creation_input_tokens", 0)
            details = usage.get("cache_creation") or {}
            long_write = details.get("ephemeral_1h_input_tokens", 0)
            short_write = details.get("ephemeral_5m_input_tokens", writes - long_write)
            if short_write + long_write != writes:
                return {}, None
        else:
            inp, out = usage["prompt_tokens"], usage["completion_tokens"]
            cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
            inp -= cached
            short_write, long_write = 0, 0
        values = (inp, out, cached, short_write, long_write)
        if any(type(n) is not int or n < 0 for n in values):
            return {}, None
        counters = dict(
            zip(
                (
                    "input_tokens",
                    "output_tokens",
                    "cached_tokens",
                    "cache_write_tokens",
                    "cache_write_long_tokens",
                ),
                values,
                strict=True,
            )
        )
        rate = RATES.get((provider, model))
        cost = sum(n * r for n, r in zip(values, rate, strict=True)) / 1_000_000 if rate else None
        return counters, cost
    except (KeyError, TypeError, ValueError, AttributeError):
        return {}, None


class UsageMeter:
    def __init__(self, store):
        self.store = store

    @contextmanager
    def call(self, provider, model, stage):
        post_ids, override = _scope.get()
        identifier = uuid.uuid4().hex
        db = self.store.db
        with db:
            db.execute(
                """INSERT INTO ai_calls(id,created,provider,model,stage,status,rate_version)
                VALUES (?,?,?,?,?,'running',?)""",
                (identifier, time.time(), provider, model, override or stage, RATE_VERSION),
            )
            db.executemany(
                "INSERT INTO ai_call_posts VALUES (?,?)", [(identifier, p) for p in post_ids]
            )
        token = _call.set(identifier)
        try:
            yield
        except BaseException as exc:
            with db:
                db.execute(
                    "UPDATE ai_calls SET status='error',error_type=? WHERE id=?",
                    (type(exc).__name__, identifier),
                )
            raise
        else:
            with db:
                db.execute("UPDATE ai_calls SET status='success' WHERE id=?", (identifier,))
        finally:
            _call.reset(token)

    def response(self, provider, model, result):
        counters, cost = token_cost(provider, model, result.get("usage"))
        if provider == "fal":
            cost = 0.003  # One requested image <= 1 megapixel; estimate, not an invoice.
        identifier = _call.get()
        if not identifier:
            return
        fields = {**counters, "usd": cost, "response_received": 1}
        with self.store.db:
            self.store.db.execute(
                "UPDATE ai_calls SET " + ",".join(f"{name}=?" for name in fields) + " WHERE id=?",
                (*fields.values(), identifier),
            )

    def summary(self):
        rules = self.store.content_rules()
        now = time.time()
        start, end = rules.day_bounds(now)
        local = datetime.fromtimestamp(now, ZoneInfo(rules.timezone))
        month_start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()

        def period(start, end):
            return dict(
                self.store.db.execute(
                    """SELECT count(*) AS calls,
                coalesce(sum(usd),0) AS usd,coalesce(sum(usd IS NULL),0) AS unknown,
                coalesce(sum(input_tokens+coalesce(cached_tokens,0)
                    +coalesce(cache_write_tokens,0)+coalesce(cache_write_long_tokens,0)),0)
                    AS input_tokens,coalesce(sum(output_tokens),0) AS output_tokens
                FROM ai_calls WHERE created>=? AND created<?""",
                    (start, end),
                ).fetchone()
            )

        groups = [
            dict(row)
            for row in self.store.db.execute(
                """SELECT provider,model,stage,
            count(*) AS calls,coalesce(sum(usd),0) AS usd,sum(usd IS NULL) AS unknown
            FROM ai_calls WHERE created>=? AND created<? GROUP BY provider,model,stage
            ORDER BY usd DESC""",
                (month_start, now + 1),
            )
        ]
        recent = [
            dict(row)
            for row in self.store.db.execute("""SELECT created,provider,model,
            stage,status,usd,error_type FROM ai_calls ORDER BY created DESC LIMIT 30""")
        ]
        return {
            "today": period(start, end),
            "month": period(month_start, now + 1),
            "groups": groups,
            "recent": recent,
            "timezone": rules.timezone,
            "tracking_since": float(self.store.get("usage_tracking_since")),
            "rate_version": RATE_VERSION,
        }

    def post_summary(self, post_id):
        # Split batch screening equally across its participants, never charge the full batch
        # to every post. Global totals always sum original calls exactly once.
        return dict(
            self.store.db.execute(
                """SELECT count(*) AS calls,
            coalesce(sum(c.usd/(SELECT count(*) FROM ai_call_posts WHERE call_id=c.id)),0) AS usd,
            coalesce(sum(c.usd IS NULL),0) AS unknown
            FROM ai_calls c JOIN ai_call_posts p ON p.call_id=c.id WHERE p.post_id=?""",
                (post_id,),
            ).fetchone()
        )

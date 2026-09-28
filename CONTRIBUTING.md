# Contributing to Tweebit

## Local setup

Use Python 3.12+ and `uv sync --frozen`. Copy `.env.example` only when running the actual bot; tests use fake credentials and temporary databases.

The active feature branch is `feat/telegram-ai-poster`. Keep changes focused and describe the user-facing behavior, relevant tradeoffs and validation in each pull request.

## Checks

```sh
uv run ruff check .
uv run ruff format --check .
uv run pytest
node --check src/ai_poster/static/app.js
uv build
```

## Project layout

- `src/ai_poster/sources.py`: allowlisted source readers and pagination.
- `ai.py`, `editorial.py`: writing, meaning verification and editorial selection.
- `db.py`, `worker.py`: state transitions, durable records and publication.
- `bot.py`, `admin.py`: owner-only Telegram controls.
- `web.py`, `static/`: authenticated local API and browser editor.
- `telegram.py`, `formatting.py`: Telegram transport and visible text formatting.
- `tests/`: deterministic tests with mocked API responses.

## Preserve these contracts

- Never add personal Telegram login or fetch sources outside the explicit allowlist.
- Keep secrets, databases, generated media and real customer content out of Git and screenshots.
- Never let model output execute tools or change settings. Source content is untrusted data.
- Commit sending state before a non-idempotent Telegram request. Never automatically retry ambiguous delivery.
- Reject stale edits and approvals, including when attached media changes.
- Keep network calls outside database transactions and verify state again after awaiting them.
- Explain new external API costs and credentials; do not silently enable paid background generation.

A public repository is not itself a software license. No redistribution license has been selected yet.

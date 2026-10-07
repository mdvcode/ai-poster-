<div align="center">

# Tweebit

**An AI-assisted editorial desk for your Telegram channel.**

Collect posts from selected Telegram and X sources, turn the best stories into original English drafts, and publish after review.

[![CI](https://github.com/mdvcode/ai-poster-/actions/workflows/ci.yml/badge.svg?branch=feat%2Ftelegram-ai-poster)](https://github.com/mdvcode/ai-poster-/actions/workflows/ci.yml)
![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB)
![Local first](https://img.shields.io/badge/Local-first-345b4c)
![Human review](https://img.shields.io/badge/Publishing-human_review-d3f36b)

[Get started](#quick-start) · [User guide](docs/USER_GUIDE.md) · [Architecture](docs/ARCHITECTURE.md) · [Roadmap](docs/ROADMAP.md)

</div>

> **Status:** a working, single-owner local application. Posts are English by default; the bot and editor interface are Russian. Collection starts paused, and publishing requires approval by default.

## What it does

| Step | What happens |
| --- | --- |
| Collect | Read only explicitly selected public Telegram channels and X accounts. Import up to 72 hours of available history when a source is connected. |
| Select | Score relevance, usefulness, novelty and substance against editable editorial rules. Default: automation and news, at least 70/100, up to five drafts a day. Screen a bounded, source-balanced window without waiting for the entire backlog. |
| Write | Compose a new English post with a headline, short paragraphs, meaningful lists and restrained emphasis. |
| Check | Compare the draft with its source for missing facts, unsupported claims and excessive similarity. Check for duplicate coverage. |
| Review | Preview, edit, restyle, delete or manually select rejected material in the local web editor. |
| Publish | Send the approved post to your connected Telegram channel. Keep uncertain deliveries visible instead of blindly retrying. |

Source publication dates are retained. The default maximum news age is 72 hours, configurable under content rules; older material can be selected manually. Unknown legacy dates are displayed explicitly.

The editor includes [AI usage and estimated costs](docs/USAGE.md) per post, day, month and processing stage. Accounting begins at installation of the usage ledger; it cannot reconstruct earlier charges.

Sources are available privately in the editor. Published posts do not receive an automatic source footer.

## Why local

The editor binds to `127.0.0.1`. A one-time code from the Telegram bot opens the owner session. API keys stay on the server; posts and settings live in SQLite.

**No personal Telegram account is connected.** Telegram sources use anonymous public previews. The application cannot read your private chats, contacts or private channels. Only channels you explicitly add are fetched.

## Quick start

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), a Telegram bot token, your numeric Telegram user ID, and an Anthropic or OpenAI API key.

```sh
git clone https://github.com/mdvcode/ai-poster-.git
cd ai-poster-
git switch feat/telegram-ai-poster
uv sync --frozen
cp .env.example .env
# Fill in .env locally, then:
uv run ai-poster run
```

Minimum configuration for Claude:

```dotenv
TELEGRAM_BOT_TOKEN=your_bot_token
OWNER_ID=your_numeric_user_id
AI_PROVIDER=anthropic
ANTHROPIC_API_KEY=your_anthropic_api_key
```

Then, in the bot's private chat:

1. Send `/start` to open the admin menu.
2. Add the bot as an administrator of your destination channel with permission to publish.
3. Connect the channel and add your chosen sources.
4. Send `/web`, open the supplied local URL, and enter its one-time code.
5. Review the content rules and enable collection.
6. Review a ready draft, then publish it.

The default editor address is `http://127.0.0.1:8765`; `WEB_PORT` changes it. Existing installations may use another port. `/web` always returns the configured address. A running Mac and network connection are required.

## Illustrations

The image workflow uses **FLUX.1 Schnell through fal.ai**, with generation initiated by the owner. Configure `FAL_KEY` locally to enable it; text generation can keep using Claude. See [image setup and costs](docs/IMAGES.md).

Images are conceptual illustrations, not evidence of reported events. They require review before being attached to a post. Image API access is separate from your Claude subscription/API balance.

## Configuration

| Variable | Purpose | Default |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | Bot token from BotFather | Required |
| `OWNER_ID` | Numeric owner ID; private-chat controls only | Required |
| `AI_PROVIDER` | `anthropic` or `openai` | `.env.example`: `anthropic` |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | Key for the selected text provider | One required |
| `ANTHROPIC_MODEL` / `OPENAI_MODEL` | Text model | See `.env.example` |
| `VERIFY_MODEL` | Optional verification model from the same provider | Same as writing model |
| `X_BEARER_TOKEN` | X app-only token with timeline-read access | Optional |
| `FAL_KEY` | fal.ai image generation API key | Optional |
| `IMAGE_DAILY_LIMIT` | Image attempts per editorial day, including failures | `10` |
| `DATABASE_PATH` | SQLite database | `data/poster.sqlite3` |
| `POLL_INTERVAL_SECONDS` | Delay after a collection/processing cycle | `300` |
| `MAX_POSTS_PER_CYCLE` | Maximum pending posts processed in a cycle | `5` |
| `PUBLISH_INTERVAL_SECONDS` | Minimum interval between publications | `60` |
| `OUTPUT_LANGUAGE` | Generated post language | `English` |
| `WEB_ENABLED` / `WEB_PORT` | Local editor | `true` / `8765` |

Editorial topics, exclusions, scoring threshold, timezone and daily draft limit are configured in the editor, not in environment variables. Keys are never committed. See [security and privacy](SECURITY.md).

## Important limits

- Meaning checks compare a draft to its source; they **do not independently verify the news**. Review headlines and factual claims before publishing.
- Semantic duplicate detection is probabilistic. Significant new developments should pass, but mistakes are possible.
- Telegram public previews may change or become unavailable. Private channels, account login and access workarounds are not supported.
- Source images, videos, polls and linked articles are not read or copied. X replies, reposts and context-dependent quote posts are skipped.
- One owner, one active destination channel and one process per bot/database. Existing drafts stay bound to their original destination.
- Drafts longer than Telegram's limit are blocked rather than silently truncated.
- A sending timeout may mean a message was delivered. Check the channel before resolving an uncertain send; there is no exactly-once guarantee.
- Docker currently runs the bot; its loopback-bound web editor is not exposed by the supplied Compose file. Use the native quick start for the local editor.

## Development

```sh
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run pytest
node --check src/ai_poster/static/app.js
uv build
```

Tests use temporary databases and mocked external APIs. They do not need production keys and do not publish to your channel.

Read [CONTRIBUTING.md](CONTRIBUTING.md) for the project layout and contribution workflow. The [product review and roadmap](docs/ROADMAP.md) separates implemented features from remaining work.

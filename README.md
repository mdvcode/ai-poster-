<div align="center">

# Tweebit

**Your AI-assisted editorial desk for Telegram.**

Turn selected Telegram and X posts into reviewed drafts for your channel.

[![CI](https://github.com/mdvcode/ai-poster-/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/mdvcode/ai-poster-/actions/workflows/ci.yml)
![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB)
![Local first](https://img.shields.io/badge/Local-first-345b4c)
![Review by default](https://img.shields.io/badge/Publishing-review_by_default-d3f36b)

[Quick start](#quick-start) · [Workflow](#editorial-workflow) · [Commands](#bot-commands) · [User guide](docs/USER_GUIDE.md) · [Roadmap](docs/ROADMAP.md)

</div>

Tweebit collects text from sources you choose, scores stories against your editorial rules, writes a new post, and checks it against the original. Review the draft in a local web editor or Telegram before publishing. Optional AI illustrations are generated and selected separately.

**Current scope:** one owner and one active destination channel. Posts default to English; the bot and editor use Russian. Collection starts paused, and manual approval is the default publication mode.

## Editorial workflow

```text
Selected sources → Editorial selection → Draft + checks → Review → Telegram
                                                            ↑
                                                   Optional illustration
```

| Capability | What you get |
| --- | --- |
| Selected sources | Public Telegram channel previews and X account timelines. New sources import up to 72 hours of available history, then continue from saved cursors. |
| Editorial selection | Editable topics, audience, exclusions, age limit and scoring threshold. Source-balanced selection prevents a busy source from taking every draft slot. |
| Drafting and checks | Readable paragraphs, bullets and bold emphasis; checks for missing facts, unsupported additions, excessive similarity and duplicate coverage. |
| Local review | Searchable queues and archive, original source text, editing, AI restyling, phone/desktop previews and a focused reading view. |
| Illustrations | Owner-initiated FLUX.1 Schnell generation through fal.ai. Review a candidate, then select it or publish text only. |
| Usage visibility | Persisted AI usage and estimated costs by post, day, month, model and stage. Unknown charges remain visible. |
| Delivery recovery | Saved publication state, stale-approval protection and explicit handling of uncertain sends. |

Original sources and links remain available privately in the editor. Published posts have no automatic source footer.

## Quick start

### 1. Prepare the requirements

- macOS or Linux, Python **3.12+**, and [uv](https://docs.astral.sh/uv/).
- A Telegram bot token and your numeric Telegram user ID.
- An **Anthropic or OpenAI API key** with API billing access.
- A destination Telegram channel where both you and the bot are administrators. The bot needs permission to publish.

X and illustrations are optional. Telegram sources use anonymous public previews: no personal Telegram account or session is required. The native runner uses POSIX process locking; direct Windows execution is not supported.

### 2. Install and configure

Install from the repository's `main` branch:

```sh
git clone https://github.com/mdvcode/ai-poster-.git
cd ai-poster-
uv sync --frozen
cp .env.example .env
```

Edit `.env` locally. The supplied example selects Anthropic:

```dotenv
TELEGRAM_BOT_TOKEN=your_bot_token
OWNER_ID=your_numeric_user_id
AI_PROVIDER=anthropic
ANTHROPIC_API_KEY=your_anthropic_api_key
```

For OpenAI, set `AI_PROVIDER=openai` and `OPENAI_API_KEY` instead. Only the selected text provider's key is required.

Start the service from the repository directory:

```sh
uv run ai-poster run
```

Keep the process and computer running with network access. Stop it with `Ctrl+C`.

### 3. Connect your channel and sources

In your bot's private chat:

1. Send `/start` to open the admin menu.
2. Open **Мой канал** to connect the destination channel.
3. Open **Источники** and add the public Telegram channels or X accounts you want to read.
4. Send `/web`, open the supplied URL **on the computer running Tweebit**, and enter the one-time code.
5. Review **Правила контента**, then click **Включить сбор** to start collection and processing.
6. Open a ready draft, inspect its original and preview, then click **Опубликовать**.

The editor defaults to `http://127.0.0.1:8765`. `WEB_PORT` changes the port; `/web` returns the configured address. Login codes last ten minutes and work once. Sessions last twelve hours and end on restart.

## Review and publication modes

**Manual mode** is the default: ready drafts wait for approval. Edit text, use `**bold**` emphasis, restyle it with AI, or delete an unwanted draft. Deleting preserves its record for duplicate detection; there is currently no restore action.

**Automatic mode** is opt-in through `/mode auto` or the bot's publication menu. It also applies to eligible drafts that are already ready. Active image generation or an unreviewed image candidate holds automatic publication until you make a decision.

`/pause` pauses new background collection/processing and automatic publication; requests already in progress may finish. Ready drafts can still be published manually. `/run` requests a cycle without overriding pause. Mode and pause settings survive restart.

| Editorial rule | Default |
| --- | --- |
| Topics | Automation and news |
| Minimum score | 70/100 across relevance, usefulness, novelty and substance |
| Daily draft limit | 5 generated drafts, including deleted or regenerated drafts |
| Maximum source age | 72 hours; `0` disables the age limit |
| Editorial timezone | `Europe/Berlin` |

Change these rules in the editor. Manually selected material still undergoes duplicate and fidelity checks and counts toward the draft limit. Disabling editorial selection also disables its daily draft limit; the age check remains active. These limits are **not a dollar spending cap**.

## Optional illustrations

Set `FAL_KEY` in `.env` and restart to enable **Обложка** in a ready draft. The text provider creates a visual brief; fal.ai generates one 1024 × 768 JPEG per request. A candidate is attached only after you select it.

`IMAGE_DAILY_LIMIT` defaults to ten attempts per editorial day, including failures and interruptions. There are no automatic image retries. Text and image providers bill separately.

Short illustrated posts use a photo caption. Text exceeding the caption limit is sent in full after the photo as a second message. See [image setup, cost estimates and recovery](docs/IMAGES.md).

## Bot commands

Commands work only for `OWNER_ID` in the bot's private chat. The button menu covers common actions; `/help` shows the full reference.

| Command | Purpose |
| --- | --- |
| `/start`, `/admin` | Open the admin menu |
| `/web` | Get an editor URL and one-time login code |
| `/channel @name` | Connect a destination; private destinations can use their numeric channel ID |
| `/add telegram @name`, `/add x @name` | Add a source |
| `/sources`, `/remove ID` | Inspect source errors or disable a source and its unfinished queue |
| `/resume`, `/pause`, `/run` | Resume, pause, or request a cycle |
| `/mode manual`, `/mode auto` | Select the publication mode |
| `/queue`, `/show ID`, `/original ID` | Inspect unfinished posts, drafts and source text |
| `/post ID`, `/skip ID` | Publish a ready draft or skip a post |
| `/retry ID` | Regenerate after a processing failure, quality block or known send failure; clears the draft and image selection |
| `/resolve ID sent`, `/resolve ID retry` | Resolve uncertain delivery after checking the channel |
| `/status`, `/help` | Check state or view help |

For manual recovery, use `/pause` or `/mode manual` before `/resolve ID retry`, then `/post ID`. In active automatic mode, the resolved draft can be sent by the next cycle. A confirmed photo delivery is retained so recovery can resume the text message.

## Configuration

Settings are read from `.env` in the working directory. Restart after changing them. Keep credentials private.

| Variable | Purpose | Default |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token | Required |
| `OWNER_ID` | Numeric Telegram owner ID | Required |
| `AI_PROVIDER` | `anthropic` or `openai` | `anthropic` in the example; `openai` when omitted |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` | Selected text provider's key | One required |
| `ANTHROPIC_MODEL` | Anthropic text model | `claude-sonnet-4-6` |
| `OPENAI_MODEL` | OpenAI text model | `gpt-4.1-mini` |
| `VERIFY_MODEL` | Verification model from the same provider | Same as writing model |
| `X_BEARER_TOKEN` | X app-only token with timeline-read access | Optional |
| `FAL_KEY` | fal.ai image generation key | Optional |
| `IMAGE_DAILY_LIMIT` | Image attempts per editorial day | `10` |
| `DATABASE_PATH` | SQLite database | `data/poster.sqlite3` |
| `POLL_INTERVAL_SECONDS` | Delay after a collection/processing cycle | `300` |
| `MAX_POSTS_PER_CYCLE` | Maximum pending posts processed per cycle | `5` |
| `PUBLISH_INTERVAL_SECONDS` | Minimum interval between publications | `60` |
| `OUTPUT_LANGUAGE` | Generated draft language | `English` |
| `WEB_ENABLED`, `WEB_PORT` | Local editor and port | `true`, `8765` |

Editorial rules are stored in SQLite and edited in **Правила контента**, rather than environment variables.

## Troubleshooting and backups

| Symptom | What to check |
| --- | --- |
| No drafts arrive | Check `/status`, pause, channel, `/sources`, editorial rejections, daily quota and API access. |
| Commands are ignored | Verify your numeric `OWNER_ID` and use the bot's private chat. |
| Channel connection fails | Check both owner and bot administrator rights and publishing permission. |
| Editor login fails | Get a fresh `/web` code and open the URL on the same computer as the service. |
| Startup reports a webhook | Remove the bot's existing webhook before running Tweebit's long polling. |
| Startup reports another instance | Stop the existing service. Run one process per bot/database. |
| Delivery is uncertain | Check the channel, including both messages for a long illustrated post, then use `/resolve`. |

To back up a native installation, stop the service, copy the database at `DATABASE_PATH` to a private location, and restart. Keep `.env` separately. SQLite contains sources, drafts, history, usage records and generated images. Automated backups and image retention are planned.

### Docker

The supplied Compose setup supports operation through the Telegram bot. Set `WEB_ENABLED=false` in `.env`, then run:

```sh
docker compose up -d --build
docker compose logs -f
```

The container runs as a non-root user and persists SQLite in the `poster-data` named volume. Preserve the volume across updates and back it up while the service is stopped.

The editor binds to loopback inside the container and is not exposed by this Compose file. Adding a port mapping alone will not make it accessible. Use the native quick start for the local web editor.

## Privacy and current limits

- Only explicitly added sources are fetched. Telegram reading does not access personal chats, contacts or private source channels.
- Source text and drafts go to the selected text provider; requested image briefs go to fal.ai. Keys remain on the server. See [security and privacy](SECURITY.md).
- Fidelity checks compare a draft with its source; they do not independently establish whether the news is true. Semantic duplicate checks can also make mistakes.
- Telegram public previews can become unavailable. Source media, linked articles and threads are not analyzed or copied. X replies, reposts and quote posts are skipped. Previously collected source edits are not synchronized.
- Generated images are conceptual illustrations, not evidence of reported events.
- Usage totals are estimates, not invoices or account balances. Earlier charges and unfamiliar model rates can be unknown. There is no dollar spending cap. See [usage accounting](docs/USAGE.md).
- Drafts stay bound to their original destination. Changing the active channel does not redirect them.
- Oversized source text or drafts beyond Telegram's limit are blocked rather than truncated. Ambiguous sends require human resolution; exactly-once delivery is not guaranteed.
- `OUTPUT_LANGUAGE` changes drafting, but preview badges and the restyling prompt currently assume English. Interface translation is not implemented.
- The editor is for trusted local use by one owner. Public hosting, multiple replicas and multiple owners are not supported by this setup.

## Development

```sh
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run pytest
node --check src/ai_poster/static/app.js
uv build
```

Tests use temporary databases and mocked APIs. They need no production keys and do not publish to your channel. They verify application behavior, not live model quality or provider availability.

Next priorities: preserve approved content during delivery retries, add spending budgets, and make service health and recovery clear in the editor. Scheduling, draft restoration and backup/retention tools follow. See the [product review and roadmap](docs/ROADMAP.md) for evidence and acceptance criteria.

## Documentation

- [User guide](docs/USER_GUIDE.md) — selection, editing, freshness and recovery.
- [Architecture](docs/ARCHITECTURE.md) — components, states and trust boundaries.
- [AI usage](docs/USAGE.md) — what cost estimates include.
- [Illustrations](docs/IMAGES.md) — setup, review and photo/text delivery.
- [Roadmap](docs/ROADMAP.md) — implemented features and recommended changes.
- [Contributing](CONTRIBUTING.md) — development workflow and reliability contracts.
- [Russian setup and command reference](docs/README.ru.md).

## License

No license has been selected, and the repository currently contains no `LICENSE` file.

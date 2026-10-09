# Product review and roadmap

Review date: October 7, 2026. This assessment is based on the current code and tests. Recommendations below are planned work, not implemented features or live provider benchmarks.

## Keep what already works

- Explicit source selection without a personal Telegram account.
- Bounded, source-balanced screening, configurable editorial rules and source-age checks.
- Drafting, fidelity review and duplicate detection, with the original available for inspection.
- Manual approval by default, revision checks and explicit uncertain-delivery states.
- Searchable review queues, a consolidated archive and phone/desktop previews.
- Owner-initiated illustrations with separate candidate selection and attempt limits.
- Durable usage estimates with visible unknown costs.

These features support the main workflow: choose sources, review a useful draft, and publish it. Adding more providers or automatic image generation is a lower priority than improving cost control and recovery.

## Add first

| Priority | Current gap and evidence | Recommended change | Acceptance criterion |
| --- | --- | --- | --- |
| P1 | A known send failure uses the same retry path as generation failure. It clears the saved draft and selected cover ([bot.py](../src/ai_poster/bot.py#L229), [web.py](../src/ai_poster/web.py#L296)). | Separate **resend saved content** from **regenerate draft**. Preserve owner edits and selected media when only delivery failed. | A known delivery retry makes no AI call and preserves text/media. Regeneration explains what will be replaced before proceeding. Existing uncertain-send checks remain required. |
| P1 | Usage is recorded, but no dollar budget is enforced ([usage.py](../src/ai_poster/usage.py)). The daily draft quota does not cover all paid calls and disappears when editorial selection is disabled. | Add daily/monthly spending budgets and configurable rates; treat provider reconciliation as a separate feature. | Every paid call requires an atomic budget reservation. Unknown rates cannot silently bypass the budget. The editor distinguishes estimated usage from provider charges. |
| P1 | Periodic editor refresh suppresses connection errors ([app.js](../src/ai_poster/static/app.js#L112)); source cards lack a last successful poll ([app.js](../src/ai_poster/static/app.js#L45)). | Show offline/stale state, last successful source poll, next cycle and retry time. Add documented service startup/recovery for unattended use. | Stopping the service changes the editor to an offline state on the next failed refresh. Each source exposes its last success and a useful failure reason. Restart behavior is observable. |
| P1 | Uncertain posts appear in the editor without resolution controls. Recovery exists only through Telegram commands ([app.js](../src/ai_poster/static/app.js#L63), [bot.py](../src/ai_poster/bot.py#L243)). | Add a guided delivery check in the editor, with actions to confirm delivery or authorize another send. | The owner must check the actual channel before resolution. Partial photo/text delivery is explained, stale actions are rejected, and ambiguous sends are never retried automatically. |

## Add next

| Priority | Improvement | Acceptance criterion |
| --- | --- | --- |
| P2 | Restore deleted drafts and offer a short undo window. Deleted records already remain in SQLite ([db.py](../src/ai_poster/db.py#L314)). | Restoring preserves duplicate history and returns the post through current state, channel, age and revision checks. |
| P2 | Add backup/restore tools, export and retention for unused cover variants. Image blobs currently remain in SQLite ([db.py](../src/ai_poster/db.py#L91)). | Backup restoration is exercised; owners can see retained media size and clean unused variants without breaking published history. |
| P2 | Add a publication calendar. The current send interval is a throttle, not a schedule ([worker.py](../src/ai_poster/worker.py#L335)). | Scheduled jobs survive restart, display the timezone and recheck channel, approval revision, age and delivery state before sending. |
| P2 | Build a fixed editorial evaluation set. Mocked API tests verify application behavior but do not measure model quality ([test_ai.py](../tests/test_ai.py)). | Prompt/model changes report results for factual qualifications, numbers, headlines, duplicate decisions and meaningful new developments. |
| P2 | Add an onboarding checklist and consistent language handling. The API already returns the configured language ([web.py](../src/ai_poster/web.py#L167)). | First use explains channel → sources → rules → collection. Preview labels and restyling agree with the configured post language. |

Implement delivery recovery and budget enforcement first, then connection health. Use actual editorial feedback to decide whether scheduling, more visual styles or additional providers are worth expanding.

## Simplify or remove

- **Remove regeneration from delivery-only retries.** A Telegram error should not discard an approved post or trigger another paid rewrite.
- **Remove misleading live status.** Cached “collection enabled” counts should be marked stale when the editor cannot reach the service.
- **Replace hardcoded language/platform labels.** The UI says English/EN regardless of `OUTPUT_LANGUAGE`, and labels the computer as a Mac ([index.html](../src/ai_poster/static/index.html#L28), [app.js](../src/ai_poster/static/app.js#L64)). The restyling prompt also assumes English ([ai.py](../src/ai_poster/ai.py#L288)). Use the actual settings and “this computer.”
- **Move setup details into settings/help.** Keep the cover screen focused on generation, review and selection; credential names, local file paths and provider setup belong in help ([app.js](../src/ai_poster/static/app.js#L141)).
- **Shorten always-visible scoring instructions.** Keep the rule controls and a short explanation; put the full mechanics behind optional help ([index.html](../src/ai_poster/static/index.html#L49)). Use a consistent form of address throughout the Russian interface.

Keep originals, freshness warnings, waiting reasons and explicit cover selection visible. They help the owner make publication decisions.

## Repository presentation

The English README documents the current feature branch, native setup, optional providers, modes, recovery commands, Docker limitations and data flow. It separates existing capabilities from these recommendations.

The application is available on `main`; changes should use a separate task branch. No `LICENSE` file exists and no license has been selected. A representative screenshot using synthetic content would help readers understand the editor; no production content or credentials should appear in it.

## Validation

At the review date, all **228 tests** passed, along with Ruff lint/format checks and the JavaScript syntax check. Source distribution and wheel builds also succeeded. Tests use temporary databases and mocked APIs. Live editorial quality, image quality and provider billing have not been benchmarked by this review.

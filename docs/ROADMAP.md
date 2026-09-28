# Product review and roadmap

Review date: September 28, 2026. This is a code-based assessment, not a claim that live provider performance or editorial quality has been benchmarked.

## Delivered in this update

- English repository overview, setup/contribution/security documentation and issue/PR templates.
- Phone/desktop post previews, focused reading mode and sticky publication controls.
- Optional fal.ai illustration generation with an explicit candidate → select → publish flow.
- Small fixed image size, daily attempt quota, no automatic paid retries and separate configuration.
- Photo captions for short posts; complete text after the photo for long posts, including partial-send recovery.

Image calls are covered with mocked provider responses. A real fal.ai generation still requires a configured key/balance and a live acceptance check. Image quality has not been measured on this channel's real posts.

## Next priorities

| Priority | Gap and evidence | Improvement | Acceptance criterion |
| --- | --- | --- | --- |
| P1 | `Worker.cycle` screens a bounded batch and can defer generation while unassessed candidates remain. Large feeds can delay useful drafts. | Snapshot a bounded collection window and select its best items; add per-source quotas or fair scheduling. | A busy source cannot starve a quieter source or postpone the first draft indefinitely; regression tests cover a large backlog. |
| P1 | Source dates enforce history import, but `Item`/stored posts do not retain an original publication timestamp for later ranking. `created` is local ingestion time. | Store source publication time, show age in the editor, and add configurable expiry for time-sensitive news. | A three-day-old source post imported today is visibly three days old; stale news is not treated as fresh. |
| P1 | Draft/image count limits exist, but there is no durable text token/cost ledger or total spend cap. A draft may require several model calls. | Record model, usage, estimated cost and failures per stage; add daily/monthly budgets and an inexpensive brief model option. | The owner can see cost per draft/cover and stop new paid work at the configured budget. Estimates are distinguished from provider billing. |
| P1 | Source errors are shown, but “waiting” does not explain every bottleneck. A sleeping or closed Mac stops local work. | Show last successful poll, current stage, retry time and quota wait reason; add supported macOS service startup/recovery. | Every queued item has a clear waiting reason; startup and restart are documented and observable. |
| P2 | Formatting and fidelity prompts guide output, but AI can still overstate headlines or inconsistently group stories. | Build a fixed editorial evaluation set covering qualification, numbers, genuinely new developments and near-duplicate stories. | Model/prompt changes report regressions in factual fidelity, readability and duplicate decisions before rollout. |
| P2 | Long illustrated posts require two Telegram messages. | Offer an explicit short-caption rewrite and reusable style presets, each with fidelity review. | The owner sees the exact one-message/two-message layout before publishing; shortening never silently removes essential facts. |
| P2 | Publication is immediate and deleted drafts have no restore action. | Add a publication calendar and reversible draft removal, retaining deduplication history. | Scheduled jobs survive restart and respect channel/state/revision checks; restored posts are revalidated. |
| P2 | SQLite retains generated image blobs indefinitely; Compose does not expose the local editor. | Add backup/retention tools and an explicitly secured hosting path if remote access becomes necessary. | Backup/restore is exercised, image retention is visible, and remote hosting includes authentication/TLS rather than exposing the current local server. |

## Recommended order

First improve queue fairness, source freshness and visibility into waiting reasons: these determine whether good drafts appear at the right time. Next measure actual spending and editorial quality. Then add scheduling and more visual styles based on real usage.

For illustrations, start with a small set of manually reviewed, text-free covers. Compare relevance and artifacts before increasing the number of generated variants. A cheap image model is useful only if the covers clarify the story.

## Repository follow-up

The feature remains on `feat/telegram-ai-poster` until reviewed and merged. The repository's default branch is separate. No license has been selected; the owner should choose one before presenting the project as freely reusable open source. Do not infer a license from public visibility.

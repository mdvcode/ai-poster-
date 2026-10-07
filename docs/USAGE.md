# AI usage and cost estimates

Open **Расходы AI** in the local editor to see today's and this month's estimated USD cost, token counts, costs by model/stage and the last 30 API calls. Each post also shows its associated cost estimate.

Accounting starts when this version first opens the database. Previous usage is **unknown**, not free. Calls and their estimates are stored in SQLite and survive restart. The totals cover this local application, not other applications using the same provider account.

## What is counted

- Editorial screening, duplicate checks, drafting, verification and restyling.
- The text-model image brief and the fal.ai image request separately.
- Every attempt, including paid responses later rejected by the fidelity/schema checks.
- Failed or interrupted calls remain visible. A timeout without token usage has an unknown cost; it is not recorded as $0.

Batch-screening cost is split equally among participating posts in the per-post view. The overall total counts each request once. Per-post allocation is an approximation, especially when input lengths differ.

## How prices are calculated

Text tokens come from provider response usage fields. Estimates use the saved rate version; cached input and Anthropic cache writes are counted separately. The current rate table supports the installed defaults: Claude Sonnet 4.6 and GPT-4.1 Mini (including its April 14, 2025 snapshot). For an unrecognized model, token counts remain visible and cost is unknown; no speculative fallback price is applied.

Standard direct-API prices checked September 28, 2026, in USD per million tokens:

| Model | Input | Output | Cache read | 5-minute cache write | 1-hour cache write |
| --- | ---: | ---: | ---: | ---: | ---: |
| Claude Sonnet 4.6 | 3.00 | 15.00 | 0.30 | 3.75 | 6.00 |
| GPT-4.1 Mini | 0.40 | 1.60 | 0.10 | — | — |

Sources: [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing), [OpenAI model pricing](https://developers.openai.com/api/docs/models/gpt-4.1-mini).

A successful fal response is estimated at $0.003 for the app's one 1024 × 768 image request, including responses whose content subsequently fails local validation. This is a conservative estimate of a possibly billable response, not proof of a provider charge. [fal.ai pricing](https://fal.ai/models/fal-ai/flux/schnell).

The API does not provide an invoice here. Totals exclude taxes, X API access, discounts/credits, account-wide spending and requests whose charges cannot be determined. Unknown costs are displayed alongside the known subtotal. Existing estimates are preserved if the rate table changes later.

There is **no dollar spending cap** in this version. Daily draft and image-attempt limits still apply. Billing reconciliation, configurable rates for other models and enforceable dollar budgets remain follow-up work.

The ledger stores timestamps, model/stage, counters, estimates, post IDs and exception class names. It does not store API keys, prompts, response bodies or raw error messages.

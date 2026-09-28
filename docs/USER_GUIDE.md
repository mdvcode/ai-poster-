# User guide

The editor and bot use Russian labels; generated posts default to English.

## Start a review session

Keep Tweebit running locally. Send `/web` in the bot's private chat, open the supplied URL on the same computer, and enter the one-time code. Codes expire after ten minutes; sessions expire after twelve hours or a service restart.

Connect the destination through **Мой канал**. Both you and the bot need administrator access to that channel, and the bot needs permission to publish. Add only the public Telegram channels and X accounts you want to read under **Источники**. Telegram does not require a personal account session; X needs `X_BEARER_TOKEN` and the appropriate API access.

## Understand the queue

| Section | Meaning |
| --- | --- |
| Черновики | Written and checked; ready for your review. |
| В обработке | Waiting for screening, generation, retries or daily capacity. It does not mean every row is currently running. |
| Требуют внимания | Quality failure, API failure or uncertain delivery. Read the reason before retrying. |
| Отклонено | Excluded by editorial rules or below the score threshold. |
| Дубли | Detected repeated content; held out of publication. |
| Опубликовано | Publication history. |
| Удалено | Removed from the queue, retained for duplicate suppression and optional feedback. |

Newly added sources import up to 72 hours of available text posts; existing sources continue from saved cursors. Collection requires **Включить сбор**. **Проверить сейчас** requests a cycle but does not override pause.

## Decide which stories become drafts

Open **Правила контента**. Defaults are automation and news, a minimum score of 70/100, and five drafts per day. Each material receives up to 25 points for relevance, usefulness, novelty and substance. Exclusions and optional deletion feedback guide selection.

Screened candidates are ranked before writing. Manual **Создать черновик** prioritizes a material but still respects the daily limit, duplicate checks and fidelity checks. Changing the rules re-evaluates pending/rejected materials; it does not silently rewrite existing drafts.

The daily limit counts generated drafts, not published posts. It resets according to the selected timezone. Meaning checks compare the draft with its source, not with independently verified reporting.

## Make a post readable

1. Open **Предпросмотр** and switch between phone and desktop widths. **Развернуть** hides the list for focused review.
2. Use **Редактировать** for your changes. Separate paragraphs with blank lines; `**bold**` provides emphasis and `•` makes plain-text bullets.
3. **Оформить** asks AI to restructure the saved text and then checks fidelity. It changes the draft, so review the result before publishing.
4. Open **Обложка** to generate/select an illustration. [Provider setup and costs](IMAGES.md).
5. Click **Опубликовать** and review the confirmation. Unsaved text is saved first. A selected image is included; unselected candidates are not.

The preview approximates Telegram; its app fonts and width can differ. Long illustrated posts use a photo followed by the complete text because Telegram limits photo captions.

**Удалить** removes an unwanted draft from the queue. An optional reason informs later selection. It does not delete a message from the published channel. There is currently no restore button.

## Pause and recover

**Пауза** stops collection and automatic publication. You may still manually publish a ready post. Manual review is the default. `/mode auto` explicitly enables automatic publication, including eligible existing drafts.

An old approval button cannot publish a changed text/image. Reopen the current draft before confirming.

If delivery is uncertain, check the channel first:

- `/resolve ID sent`: mark the complete post as delivered after you verified it.
- `/resolve ID retry`: allow another send only after checking what is missing. If the photo was confirmed sent, the text resumes without a second photo.
- `/retry ID`: recreate and recheck a post after a processing failure; old image selection is cleared.

Use `/help` for the complete command list and `/sources` for source errors. If nothing arrives, check pause, source errors, the daily draft budget, rejected materials and API balances before repeatedly requesting a cycle.

## Back up

Stop the service, copy `data/poster.sqlite3` to a private backup location, then restart. Keep `.env` separately and privately. The database includes drafts, source settings, review history and generated images. Do not run two services against one bot/database.

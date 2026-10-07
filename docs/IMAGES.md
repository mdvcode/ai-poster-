# Image generation

Tweebit uses **FLUX.1 Schnell via fal.ai** for inexpensive editorial illustrations. Claude (or the configured text provider) creates the visual brief; fal.ai creates the image. A Claude API key cannot be used to pay for fal.ai.

## Connect the provider

1. Sign in to [fal.ai](https://fal.ai/) and add a balance in its billing dashboard.
2. Create an API key in the [fal.ai key dashboard](https://fal.ai/dashboard/keys). Keep it private.
3. Add the key to the local `.env` file, never to a Git commit or screenshot:

   ```dotenv
   FAL_KEY=your_fal_api_key
   IMAGE_DAILY_LIMIT=10
   ```

4. Restart the existing Tweebit process. Do not start a second process against the same database/bot.
5. Send `/web` to the bot for a fresh editor login if needed.

## Create an illustration

Open a ready draft → **Обложка** → optionally describe an idea → **Создать картинку**.

The current text is saved first. One request creates one 1024 × 768 JPEG. Review the result and click **Использовать**, then check **Предпросмотр**. Click **Без картинки** to publish text only. Generating a variant does not automatically attach it. The editor shows the three most recent variants; the selected image is shown in the post preview.

**Опубликовать** sends the saved text and selected image. In automatic mode, generation and an unreviewed new candidate hold publication until you select an image or choose text only. Manual publication explicitly confirms whether the post includes an image.

The brief asks for a conceptual illustration with no invented chart values, embedded text, logos or documentary-looking evidence. Review the actual result: models can still produce unwanted details.

## Expected cost

Pricing checked September 28, 2026:

| Usage | Estimated image cost |
| --- | --- |
| One 1024 × 768 image | $0.003 |
| Five images a day, 30 days | $0.45 |
| Ten images a day, 30 days | $0.90 |

[fal.ai lists $0.003 per megapixel, rounded up](https://fal.ai/models/fal-ai/flux/schnell). This resolution is billed as one megapixel. Taxes, retries/new variants and the separate text-model brief are excluded. A Claude brief can cost more than the image itself depending on the selected model and token usage. These are estimates, not a provider spending cap or a quality guarantee.

For comparison, [OpenAI GPT Image 1 Mini](https://developers.openai.com/api/docs/models/gpt-image-1-mini) lists $0.005 for low-quality square output and $0.011 for medium, plus input charges. It is not integrated in this version. Schnell is the economical starting point for text-free conceptual covers; typography and precise brand layouts need separate evaluation.

`IMAGE_DAILY_LIMIT` counts attempts across all posts, including failures and interrupted requests, using the editorial timezone. Default: 10. There are no automatic image retries. A timed-out request may still be charged by fal.ai; inspect provider usage before creating another variant.

## How publication works

- Up to 1,024 UTF-16 units of visible text: one photo with a formatted caption.
- Longer text: a photo labelled “AI illustration”, followed by the complete formatted post. The photo is sent silently; the text is a separate message.
- Text is never truncated to fit a caption. Shorten it manually if you want a single message.
- If Telegram confirms the photo but rate-limits the text, a retry sends only the text.
- Ambiguous delivery is held as `uncertain`. Check both messages in the channel before `/resolve ID sent` or `/resolve ID retry`. A known delivered photo is retained when resuming text delivery.

Images are stored in the local SQLite database and served only to authenticated editor sessions. They are included in database backups. fal.ai receives the generated visual brief; the selected text provider receives the draft and your image direction. See [Security](../SECURITY.md).

The client uses the [synchronous fal API](https://fal.ai/docs/documentation/model-apis/inference/synchronous), requests an inline JPEG, validates it, and does not download arbitrary response URLs. The [model API reference](https://fal.ai/models/fal-ai/flux/schnell/api) describes supported input and output fields.

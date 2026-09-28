"""Explicit, owner-initiated illustration generation. No automatic paid retries."""

import base64
import io

import httpx
from PIL import Image, UnidentifiedImageError

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MODEL = "fal-ai/flux/schnell"


class ImageError(Exception):
    pass


class ImageGenerator:
    def __init__(self, settings, client: httpx.AsyncClient):
        self.settings = settings
        self.client = client

    @property
    def configured(self):
        return bool(self.settings.fal_key.get_secret_value().strip())

    async def generate(self, prompt: str) -> bytes:
        if not self.configured:
            raise ImageError("Добавьте FAL_KEY в локальный .env и перезапустите сервис.")
        try:
            response = await self.client.post(
                f"https://fal.run/{MODEL}",
                headers={
                    "Authorization": f"Key {self.settings.fal_key.get_secret_value()}",
                    "X-Fal-No-Retry": "1",
                },
                json={
                    "prompt": prompt,
                    "image_size": {"width": 1024, "height": 768},
                    "num_images": 1,
                    "num_inference_steps": 4,
                    "sync_mode": True,
                    "output_format": "jpeg",
                    "enable_safety_checker": True,
                },
                timeout=120,
                follow_redirects=False,
            )
            response.raise_for_status()
            result = response.json()
            if result.get("has_nsfw_concepts") != [False]:
                raise ImageError("Изображение не прошло проверку провайдера. Измените описание.")
            uri = result["images"][0]["url"]
            prefix = "data:image/jpeg;base64,"
            if (
                not isinstance(uri, str)
                or not uri.startswith(prefix)
                or len(uri) > 12 * 1024 * 1024
            ):
                raise ImageError("Провайдер вернул неподдерживаемое изображение.")
            content = base64.b64decode(uri[len(prefix) :], validate=True)
            validate_image(content)
            return content
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code in {401, 403}:
                raise ImageError("Проверьте FAL_KEY и доступ к fal.ai.") from None
            if code == 402:
                raise ImageError("Недостаточно средств на счёте fal.ai.") from None
            raise ImageError(f"fal.ai вернул ошибку {code}. Автоповтор отключён.") from None
        except httpx.HTTPError:
            raise ImageError(
                "Ответ fal.ai не получен. Запрос мог быть оплачен; автоповтор отключён."
            ) from None
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise ImageError("Некорректный ответ сервиса изображений.") from None


def validate_image(content):
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise ImageError("Изображение слишком большое или пустое.")
    try:
        with Image.open(io.BytesIO(content)) as image:
            if image.format != "JPEG" or image.width * image.height > 2_000_000:
                raise ImageError("Ожидалось небольшое JPEG-изображение.")
            image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ImageError("Не удалось прочитать изображение.") from None

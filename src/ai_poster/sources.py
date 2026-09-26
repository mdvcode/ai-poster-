import re
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup


@dataclass(frozen=True)
class Item:
    id: str
    text: str
    url: str


def normalize_handle(kind: str, value: str) -> str:
    domains = r"(?:t\.me|telegram\.me)" if kind == "telegram" else r"(?:x\.com|twitter\.com)"
    value = re.sub(rf"^https?://{domains}/", "", value.strip(), flags=re.I)
    value = value.removeprefix("@").rstrip("/")
    if kind == "telegram" and value.startswith("s/"):
        value = value[2:]
    pattern = r"[A-Za-z][A-Za-z0-9_]{3,31}" if kind == "telegram" else r"[A-Za-z0-9_]{1,15}"
    if not re.fullmatch(pattern, value):
        raise ValueError("Укажите @username или ссылку на публичный канал/аккаунт.")
    return value.lower()


class TelegramSource:
    """Anonymous public previews. No Telegram account, session, or Bot API credentials."""

    def __init__(self, client: httpx.AsyncClient, allowed=None):
        self.client = client
        self.allowed = allowed

    async def page(self, handle: str, before: int | None = None):
        handle = normalize_handle("telegram", handle)
        # Build a fresh request: never inherit cookies or Authorization from other APIs.
        request = httpx.Request(
            "GET",
            f"https://t.me/s/{handle}",
            params={"before": before} if before is not None else None,
        )
        response = await self.client.send(request, auth=None, follow_redirects=False)
        response.raise_for_status()
        if "text/html" not in response.headers.get("content-type", ""):
            raise ValueError("Telegram не вернул публичную страницу канала.")
        if len(response.content) > 5_000_000:
            raise ValueError("Страница Telegram превышает допустимый размер.")
        soup = BeautifulSoup(response.text, "html.parser")
        identity = soup.select_one(".tgme_channel_info_header_username")
        history = soup.select_one(".tgme_channel_history")
        if (
            not identity
            or identity.get_text(strip=True).lstrip("@").lower() != handle
            or history is None
        ):
            raise ValueError("Публичный просмотр этого канала недоступен; вход не выполняется.")
        items = []
        for post in history.select(".tgme_widget_message[data-post]"):
            match = re.fullmatch(r"([A-Za-z0-9_]+)/([0-9]+)", post.get("data-post", ""))
            if not match or match[1].lower() != handle:
                raise ValueError("Страница содержит пост другого канала; чтение остановлено.")
            text = post.select_one(".tgme_widget_message_text")
            body = ""
            if text and not any("truncat" in name for name in text.get("class", [])):
                for element in text.select("script, style"):
                    element.decompose()
                for br in text.select("br"):
                    br.replace_with("\n")
                # Preserve link destinations as text only; never fetch links, embeds or media.
                for link in text.select("a[href]"):
                    href = link.get("href", "")
                    if href.startswith(("https://", "http://")) and href != link.get_text():
                        link.append(f" ({href})")
                body = text.get_text().strip()
            items.append(Item(match[2], body, f"https://t.me/{handle}/{match[2]}"))
        has_older = soup.select_one("a.tme_messages_more[data-before]") is not None
        return sorted(items, key=lambda item: int(item.id)), has_older

    async def resolve(self, handle: str):
        # Called only when the owner explicitly adds this candidate in the admin panel.
        handle = normalize_handle("telegram", handle)
        items, _ = await self.page(handle)
        return f"public:{handle}", str(max((int(item.id) for item in items), default=0))

    async def fetch(self, source):
        handle = normalize_handle("telegram", source["handle"])
        external_id = source["external_id"]
        if (
            external_id != f"public:{handle}"
            or self.allowed is None
            or not self.allowed("telegram", handle, external_id)
        ):
            raise PermissionError("Канал отсутствует в разрешённом списке публичных источников.")
        cursor = int(source["cursor"])
        before = None
        collected = {}
        for _ in range(50):
            if not self.allowed("telegram", handle, external_id):
                raise PermissionError("Источник отключён; чтение остановлено.")
            items, has_older = await self.page(handle, before)
            if not items:
                if before is not None:
                    raise ValueError("Неполная история; курсор не изменён.")
                return [], source["cursor"]
            for item in items:
                if int(item.id) > cursor:
                    collected[int(item.id)] = item
            oldest = min(int(item.id) for item in items)
            if oldest <= cursor or not has_older:
                return [collected[k] for k in sorted(collected)], str(
                    max(collected, default=cursor)
                )
            if before is not None and oldest >= before:
                raise ValueError("Пагинация Telegram не продвигается; курсор не изменён.")
            before = oldest
        raise ValueError("Слишком большой пропуск истории; курсор не изменён.")


class XSource:
    def __init__(self, token: str, client: httpx.AsyncClient, allowed=None):
        self.token = token
        self.allowed = allowed
        self.client = client

    async def get(self, path: str, params=None):
        if not self.token:
            raise ValueError("Для X нужен X_BEARER_TOKEN в .env.")
        response = await self.client.get(
            f"https://api.x.com/2/{path}",
            params=params,
            headers={"Authorization": f"Bearer {self.token}"},
            follow_redirects=False,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("errors"):
            raise ValueError("X API вернул неполные данные; курсор сохранён.")
        return data

    async def resolve(self, handle: str):
        handle = normalize_handle("x", handle)
        user = await self.get(f"users/by/username/{handle}")
        external_id = user["data"]["id"]
        if not re.fullmatch(r"[0-9]{1,20}", external_id):
            raise ValueError("X вернул некорректный идентификатор аккаунта.")
        page = await self.get(f"users/{external_id}/tweets", {"max_results": 5})
        cursor = max((int(p["id"]) for p in page.get("data", [])), default=0)
        return external_id, str(cursor)

    async def fetch(self, source):
        handle = normalize_handle("x", source["handle"])
        if (
            self.allowed is None
            or not self.allowed("x", handle, source["external_id"])
            or not re.fullmatch(r"[0-9]{1,20}", source["external_id"])
        ):
            raise PermissionError("X-аккаунт отсутствует в разрешённом списке источников.")
        params = {
            "max_results": 100,
            "exclude": "retweets,replies",
            "tweet.fields": "note_tweet,referenced_tweets",
        }
        if source["cursor"] != "0":
            params["since_id"] = source["cursor"]
        items = []
        cursor = int(source["cursor"])
        for _ in range(100):
            if not self.allowed("x", handle, source["external_id"]):
                raise PermissionError("Источник X отключён; чтение остановлено.")
            page = await self.get(f"users/{source['external_id']}/tweets", params)
            for post in page.get("data", []):
                cursor = max(cursor, int(post["id"]))
                # Quotes require context from another post; do not rewrite incomplete material.
                if post.get("referenced_tweets"):
                    continue
                body = post.get("note_tweet", {}).get("text", post["text"])
                items.append(Item(post["id"], body, f"https://x.com/i/web/status/{post['id']}"))
            token = page.get("meta", {}).get("next_token")
            if not token:
                return sorted(items, key=lambda item: int(item.id)), str(cursor)
            params["pagination_token"] = token
        raise ValueError("X pagination limit reached; cursor unchanged")

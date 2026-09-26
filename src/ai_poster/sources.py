import re
from dataclasses import dataclass

import httpx
from telethon import TelegramClient, utils
from telethon.tl.types import Channel


@dataclass(frozen=True)
class Item:
    id: str
    text: str
    url: str


def normalize_handle(kind: str, value: str) -> str:
    domains = r"(?:t\.me|telegram\.me)" if kind == "telegram" else r"(?:x\.com|twitter\.com)"
    value = re.sub(rf"^https?://{domains}/", "", value.strip(), flags=re.I)
    value = value.removeprefix("@").rstrip("/")
    pattern = r"[A-Za-z][A-Za-z0-9_]{3,31}" if kind == "telegram" else r"[A-Za-z0-9_]{1,15}"
    if not re.fullmatch(pattern, value):
        raise ValueError("Укажите @username или ссылку на публичный канал/аккаунт.")
    return value.lower()


class TelegramSource:
    def __init__(self, client: TelegramClient | None):
        self.client = client

    async def entity(self, handle: str):
        if self.client is None:
            raise ValueError("Telegram reader не настроен. Выполните ai-poster login.")
        entity = await self.client.get_entity(handle)
        if not isinstance(entity, Channel) or not entity.broadcast:
            raise ValueError("Источник должен быть Telegram-каналом.")
        return entity

    async def resolve(self, handle: str):
        entity = await self.entity(handle)
        latest = await self.client.get_messages(entity, limit=1)
        return str(utils.get_peer_id(entity)), str(latest[0].id) if latest else "0"

    async def fetch(self, source):
        entity = await self.entity(source["handle"])
        if str(utils.get_peer_id(entity)) != source["external_id"]:
            raise ValueError("Username сменил владельца. Добавьте источник заново.")
        items = []
        cursor = source["cursor"]
        async for message in self.client.iter_messages(
            entity, min_id=int(cursor), reverse=True, limit=100
        ):
            cursor = str(message.id)
            if message.raw_text:
                items.append(
                    Item(
                        str(message.id),
                        message.raw_text,
                        f"https://t.me/{source['handle']}/{message.id}",
                    )
                )
        return items, cursor


class XSource:
    def __init__(self, token: str, client: httpx.AsyncClient):
        self.token = token
        self.client = client

    async def get(self, path: str, params=None):
        if not self.token:
            raise ValueError("Для X нужен X_BEARER_TOKEN в .env.")
        response = await self.client.get(
            f"https://api.x.com/2/{path}",
            params=params,
            headers={"Authorization": f"Bearer {self.token}"},
        )
        response.raise_for_status()
        data = response.json()
        if data.get("errors"):
            raise ValueError("X API вернул неполные данные; курсор сохранён.")
        return data

    async def resolve(self, handle: str):
        user = await self.get(f"users/by/username/{handle}")
        external_id = user["data"]["id"]
        page = await self.get(f"users/{external_id}/tweets", {"max_results": 5})
        cursor = max((int(p["id"]) for p in page.get("data", [])), default=0)
        return external_id, str(cursor)

    async def fetch(self, source):
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

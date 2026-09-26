import httpx


class TelegramError(Exception):
    def __init__(self, code: int, retry_after: int = 0):
        self.code = code
        self.retry_after = retry_after
        # Never include raw response/request: URL contains the bot token.
        super().__init__(f"Telegram API error {code}")


class Telegram:
    def __init__(self, token: str, client: httpx.AsyncClient):
        self.base = f"https://api.telegram.org/bot{token}"
        self.client = client

    async def call(self, method: str, **payload):
        response = await self.client.post(f"{self.base}/{method}", json=payload, timeout=40)
        data = response.json()
        if not data.get("ok"):
            raise TelegramError(
                data.get("error_code", response.status_code),
                data.get("parameters", {}).get("retry_after", 0),
            )
        return data["result"]

    async def send(self, chat_id: str | int, text: str, **kwargs):
        return await self.call(
            "sendMessage",
            chat_id=chat_id,
            text=text,
            link_preview_options={"is_disabled": True},
            **kwargs,
        )

    async def validate_channel(self, value: str, owner_id: int) -> str:
        chat = await self.call("getChat", chat_id=value)
        if chat["type"] != "channel":
            raise ValueError("Нужен Telegram-канал, не группа.")
        me = await self.call("getMe")
        owner = await self.call("getChatMember", chat_id=chat["id"], user_id=owner_id)
        member = await self.call("getChatMember", chat_id=chat["id"], user_id=me["id"])
        if owner["status"] not in {"creator", "administrator"}:
            raise ValueError("Вы должны быть владельцем или администратором канала.")
        if member["status"] != "administrator" or not member.get("can_post_messages"):
            raise ValueError("Добавьте бота администратором с правом публикации сообщений.")
        return str(chat["id"])


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2

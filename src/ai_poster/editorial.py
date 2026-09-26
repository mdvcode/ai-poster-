"""Owner-editable selection policy and structured AI screening contracts."""

import hashlib
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator

EXCLUSIONS = {
    "ads": "Реклама и промокоды",
    "giveaways": "Розыгрыши",
    "jobs": "Вакансии",
    "events": "Анонсы мероприятий",
    "vague": "Общие рассуждения без конкретики",
}
FEEDBACK = {
    "off_topic": "Не моя тема",
    "ads": "Реклама",
    "uninteresting": "Неинтересно",
    "duplicate": "Повтор",
}


class ContentRules(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool = True
    topics: str = Field(
        default="Автоматизация и новости.",
        min_length=1,
        max_length=2000,
    )
    audience: str = Field(
        default=(
            "Читатели, которым интересны автоматизация, полезные инструменты и значимые новости."
        ),
        min_length=1,
        max_length=1000,
    )
    exclusions: list[str] = Field(default_factory=lambda: list(EXCLUSIONS), max_length=5)
    extra_exclusions: str = Field(default="", max_length=2000)
    daily_limit: int = Field(default=5, ge=1, le=50)
    min_score: int = Field(default=70, ge=1, le=100)
    timezone: str = "Europe/Berlin"

    @field_validator("topics", "audience")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Укажите тему и аудиторию")
        return value.strip()

    @field_validator("exclusions")
    @classmethod
    def known_exclusions(cls, value):
        if len(set(value)) != len(value) or not set(value) <= EXCLUSIONS.keys():
            raise ValueError("Неизвестное исключение")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (KeyError, ValueError) as exc:
            raise ValueError("Неизвестный часовой пояс") from exc
        return value

    @property
    def revision(self):
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()[:24]

    def day_bounds(self, now):
        today = datetime.fromtimestamp(now, ZoneInfo(self.timezone)).date()
        start = datetime.combine(today, time(), ZoneInfo(self.timezone))
        end = datetime.combine(today + timedelta(days=1), time(), ZoneInfo(self.timezone))
        return start.timestamp(), end.timestamp()


class Screening(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: int
    suitable: bool
    relevance: int = Field(ge=0, le=25)
    usefulness: int = Field(ge=0, le=25)
    novelty: int = Field(ge=0, le=25)
    substance: int = Field(ge=0, le=25)
    reason: str = Field(min_length=1, max_length=800)

    @property
    def score(self):
        return self.relevance + self.usefulness + self.novelty + self.substance


class ScreeningBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    items: list[Screening]

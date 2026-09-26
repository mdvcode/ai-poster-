from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    telegram_bot_token: SecretStr
    owner_id: int = Field(gt=0)
    ai_provider: Literal["openai", "anthropic"] = "openai"
    openai_api_key: SecretStr = SecretStr("")
    anthropic_api_key: SecretStr = SecretStr("")
    openai_model: str = "gpt-4.1-mini"
    anthropic_model: str = "claude-sonnet-4-6"
    verify_model: str = ""
    telegram_api_id: int | None = None
    telegram_api_hash: SecretStr = SecretStr("")
    x_bearer_token: SecretStr = SecretStr("")
    database_path: str = "data/poster.sqlite3"
    telegram_session_path: str = "data/reader"
    poll_interval_seconds: int = Field(default=300, ge=30)
    publish_interval_seconds: int = Field(default=60, ge=1)
    max_posts_per_cycle: int = Field(default=5, ge=1, le=50)
    output_language: str = "Russian"

    @field_validator("telegram_api_id", mode="before")
    @classmethod
    def empty_api_id(cls, value):
        return None if value == "" else value

    @field_validator("telegram_bot_token")
    @classmethod
    def nonempty_secret(cls, value):
        if not value.get_secret_value().strip():
            raise ValueError("Required secret is empty")
        return value

    @model_validator(mode="after")
    def selected_provider_key(self) -> Self:
        name = "anthropic_api_key" if self.ai_provider == "anthropic" else "openai_api_key"
        if not getattr(self, name).get_secret_value().strip():
            raise ValueError(f"Заполните {name.upper()} для AI_PROVIDER={self.ai_provider}")
        return self

    @property
    def rewrite_model(self) -> str:
        return self.anthropic_model if self.ai_provider == "anthropic" else self.openai_model

    @property
    def verification_model(self) -> str:
        return self.verify_model.strip() or self.rewrite_model

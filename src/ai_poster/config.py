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
    fal_key: SecretStr = SecretStr("")
    image_daily_limit: int = Field(default=10, ge=1, le=100)
    x_bearer_token: SecretStr = SecretStr("")
    database_path: str = "data/poster.sqlite3"
    poll_interval_seconds: int = Field(default=300, ge=30)
    publish_interval_seconds: int = Field(default=60, ge=1)
    max_posts_per_cycle: int = Field(default=5, ge=1, le=50)
    output_language: str = "English"
    web_enabled: bool = True
    web_port: int = Field(default=8765, ge=1024, le=65535)

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

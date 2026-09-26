from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    telegram_bot_token: SecretStr
    owner_id: int = Field(gt=0)
    openai_api_key: SecretStr
    openai_model: str = "gpt-4.1-mini"
    verify_model: str = "gpt-4.1-mini"
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

    @field_validator("telegram_bot_token", "openai_api_key")
    @classmethod
    def nonempty_secret(cls, value):
        if not value.get_secret_value().strip():
            raise ValueError("Required secret is empty")
        return value

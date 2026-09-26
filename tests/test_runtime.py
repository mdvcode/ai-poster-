import pytest
from pydantic import ValidationError

from ai_poster.config import Settings
from ai_poster.main import single_instance


def test_single_instance_lock_releases_after_exit(tmp_path):
    path = str(tmp_path / "poster.sqlite3")
    with single_instance(path):
        with pytest.raises(RuntimeError, match="экземпляр"):
            with single_instance(path):
                pytest.fail("Second process obtained lock")
    with single_instance(path):
        pass


def test_empty_optional_reader_config_does_not_prevent_x_only_setup():
    settings = Settings(
        _env_file=None,
        telegram_bot_token="token",
        owner_id=42,
        openai_api_key="key",
        telegram_api_id="",
    )
    assert settings.telegram_api_id is None


def test_required_credentials_are_not_empty():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, telegram_bot_token="", owner_id=42, openai_api_key="")

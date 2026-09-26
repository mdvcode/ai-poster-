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


def test_legacy_personal_credentials_are_not_loaded():
    settings = Settings(
        _env_file=None,
        telegram_bot_token="token",
        owner_id=42,
        openai_api_key="key",
        telegram_api_id="",
    )
    assert not hasattr(settings, "telegram_api_id")
    assert not hasattr(settings, "telegram_session_path")


def test_required_credentials_are_not_empty():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, telegram_bot_token="", owner_id=42, openai_api_key="")


def test_claude_only_key_is_sufficient():
    settings = Settings(
        _env_file=None,
        telegram_bot_token="token",
        owner_id=42,
        ai_provider="anthropic",
        anthropic_api_key="claude-key",
    )
    assert settings.openai_api_key.get_secret_value() == ""
    assert settings.rewrite_model == "claude-sonnet-4-6"
    assert settings.verification_model == settings.rewrite_model


@pytest.mark.parametrize(
    "provider,key", [("anthropic", "openai_api_key"), ("openai", "anthropic_api_key")]
)
def test_other_provider_key_cannot_satisfy_selected_provider(provider, key):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            telegram_bot_token="token",
            owner_id=42,
            ai_provider=provider,
            **{key: "other-key"},
        )


def test_claude_verify_model_override():
    settings = Settings(
        _env_file=None,
        telegram_bot_token="token",
        owner_id=42,
        ai_provider="anthropic",
        anthropic_api_key="key",
        verify_model="claude-opus-4-6",
    )
    assert settings.verification_model == "claude-opus-4-6"


def test_selected_provider_cannot_have_blank_key():
    with pytest.raises(ValidationError, match="ANTHROPIC_API_KEY"):
        Settings(
            _env_file=None,
            telegram_bot_token="token",
            owner_id=42,
            ai_provider="anthropic",
            anthropic_api_key="   ",
        )

from autosport_bot.core.config import Settings


def test_settings_model_has_defaults() -> None:
    settings = Settings(
        TELEGRAM_BOT_TOKEN="token",
    )
    assert settings.poll_interval_seconds == 10
    assert settings.itmo_client_id == "student-personal-cabinet"

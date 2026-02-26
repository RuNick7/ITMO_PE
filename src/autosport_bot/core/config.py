from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    telegram_bot_token: str = Field(alias="TELEGRAM_BOT_TOKEN")
    database_path: str = Field(default="autosport.db", alias="DATABASE_PATH")
    itmo_client_id: str = Field(default="student-personal-cabinet", alias="ITMO_CLIENT_ID")
    itmo_redirect_uri: str = Field(default="https://my.itmo.ru/login/callback", alias="ITMO_REDIRECT_URI")
    itmo_realm: str = Field(default="itmo", alias="ITMO_REALM")
    poll_interval_seconds: int = Field(default=10, alias="POLL_INTERVAL_SECONDS")


@lru_cache
def get_settings() -> Settings:
    return Settings()

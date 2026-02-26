from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class UserPreferences:
    chat_id: int
    auto_sections: list[str] = field(default_factory=list)
    auto_days: list[str] = field(default_factory=list)
    auto_times: list[str] = field(default_factory=list)


@dataclass
class UserTokens:
    chat_id: int
    itmo_login: str = ""
    telegram_tag: str = ""
    access_token: str = ""
    refresh_token: str = ""
    access_expires_at: int = 0
    refresh_expires_at: int = 0


@dataclass
class AutoEnrollRule:
    chat_id: int
    id: int = 0
    enabled: bool = False
    priority: int = 0
    section_name: str = ""
    day_code: int = -1
    time_slot_start: str = ""
    type_id: int = 0
    after_date: str = ""

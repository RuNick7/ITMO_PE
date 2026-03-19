from __future__ import annotations

import sqlite3
from pathlib import Path

from autosport_bot.storage.models import AutoEnrollRule, UserPreferences, UserTokens


class InMemoryRepository:
    def __init__(self) -> None:
        self._preferences: dict[int, UserPreferences] = {}
        self._tokens: dict[int, UserTokens] = {}
        self._priority_modes: dict[int, bool] = {}

    def get_preferences(self, chat_id: int) -> UserPreferences:
        if chat_id not in self._preferences:
            self._preferences[chat_id] = UserPreferences(chat_id=chat_id)
        return self._preferences[chat_id]

    def save_tokens(self, tokens: UserTokens) -> None:
        self._tokens[tokens.chat_id] = tokens

    def get_tokens(self, chat_id: int) -> UserTokens | None:
        return self._tokens.get(chat_id)

    def list_all_user_ids(self) -> list[int]:
        return sorted(self._preferences.keys())

    def list_enabled_auto_enroll_user_ids(self) -> list[int]:
        return sorted(self._preferences.keys())

    def set_priority_mode(
        self,
        telegram_id: int,
        has_priority_mode: bool,
        checked_at_iso: str,
    ) -> None:
        _ = checked_at_iso
        self._priority_modes[telegram_id] = has_priority_mode

    def set_priority_mode_bulk(
        self,
        updates: list[tuple[int, bool, str]],
    ) -> None:
        for telegram_id, has_priority_mode, _ in updates:
            self._priority_modes[telegram_id] = has_priority_mode

    def get_priority_mode(self, telegram_id: int) -> bool:
        return bool(self._priority_modes.get(telegram_id, False))


class SQLiteRepository:
    def __init__(self, db_path: str) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                telegram_tag TEXT NOT NULL DEFAULT '',
                itmo_login TEXT NOT NULL DEFAULT '',
                access_token TEXT NOT NULL DEFAULT '',
                refresh_token TEXT NOT NULL DEFAULT '',
                access_expires_at INTEGER NOT NULL DEFAULT 0,
                refresh_expires_at INTEGER NOT NULL DEFAULT 0,
                weekly_limit INTEGER NOT NULL DEFAULT 2,
                weekly_limit_set INTEGER NOT NULL DEFAULT 0,
                has_priority_mode INTEGER NOT NULL DEFAULT 0,
                priority_checked_at TEXT NOT NULL DEFAULT '',
                auto_sections TEXT NOT NULL DEFAULT '[]',
                auto_days TEXT NOT NULL DEFAULT '[]',
                auto_times TEXT NOT NULL DEFAULT '[]'
            );
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auto_enroll_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 0,
                priority INTEGER NOT NULL DEFAULT 0,
                section_name TEXT NOT NULL DEFAULT '',
                day_code INTEGER NOT NULL DEFAULT -1,
                time_slot_start TEXT NOT NULL DEFAULT '',
                type_id INTEGER NOT NULL DEFAULT 0,
                after_date TEXT NOT NULL DEFAULT '',
                last_lesson_id INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        # Migration: old schema had telegram_id as primary key and no id column.
        cols = self._conn.execute("PRAGMA table_info(auto_enroll_rules);").fetchall()
        has_id = any(col["name"] == "id" for col in cols)
        if not has_id:
            self._conn.execute("ALTER TABLE auto_enroll_rules RENAME TO auto_enroll_rules_old;")
            self._conn.execute(
                """
                CREATE TABLE auto_enroll_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    priority INTEGER NOT NULL DEFAULT 0,
                    section_name TEXT NOT NULL DEFAULT '',
                    day_code INTEGER NOT NULL DEFAULT -1,
                    time_slot_start TEXT NOT NULL DEFAULT '',
                    type_id INTEGER NOT NULL DEFAULT 0,
                    after_date TEXT NOT NULL DEFAULT '',
                    last_lesson_id INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            self._conn.execute(
                """
                INSERT INTO auto_enroll_rules (
                    telegram_id, enabled, priority, section_name, day_code, time_slot_start, type_id, after_date, last_lesson_id
                )
                SELECT telegram_id, enabled, 0, section_name, day_code, time_slot_start, type_id, after_date, last_lesson_id
                FROM auto_enroll_rules_old;
                """
            )
            self._conn.execute("DROP TABLE auto_enroll_rules_old;")
            cols = self._conn.execute("PRAGMA table_info(auto_enroll_rules);").fetchall()
        # Add missing columns for already migrated databases.
        user_cols = self._conn.execute("PRAGMA table_info(users);").fetchall()
        if not any(col["name"] == "weekly_limit" for col in user_cols):
            self._conn.execute("ALTER TABLE users ADD COLUMN weekly_limit INTEGER NOT NULL DEFAULT 2;")
        if not any(col["name"] == "weekly_limit_set" for col in user_cols):
            self._conn.execute("ALTER TABLE users ADD COLUMN weekly_limit_set INTEGER NOT NULL DEFAULT 0;")
        if not any(col["name"] == "has_priority_mode" for col in user_cols):
            self._conn.execute("ALTER TABLE users ADD COLUMN has_priority_mode INTEGER NOT NULL DEFAULT 0;")
        if not any(col["name"] == "priority_checked_at" for col in user_cols):
            self._conn.execute("ALTER TABLE users ADD COLUMN priority_checked_at TEXT NOT NULL DEFAULT '';")
        if not any(col["name"] == "priority" for col in cols):
            self._conn.execute("ALTER TABLE auto_enroll_rules ADD COLUMN priority INTEGER NOT NULL DEFAULT 0;")
        self._conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_auto_enroll_rules_unique
            ON auto_enroll_rules (telegram_id, section_name, day_code, time_slot_start, type_id, after_date);
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_auto_enroll_rules_telegram_id
            ON auto_enroll_rules (telegram_id);
            """
        )
        self._conn.commit()

    def ensure_user(self, telegram_id: int, telegram_tag: str) -> None:
        self._conn.execute(
            """
            INSERT INTO users (telegram_id, telegram_tag)
            VALUES (?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                telegram_tag=excluded.telegram_tag;
            """,
            (telegram_id, telegram_tag),
        )
        self._conn.execute(
            """
            UPDATE users
            SET weekly_limit = 0
            WHERE telegram_id = ? AND weekly_limit_set = 0;
            """,
            (telegram_id,),
        )
        self._conn.commit()

    def save_tokens(self, tokens: UserTokens) -> None:
        self._conn.execute(
            """
            INSERT INTO users (
                telegram_id, telegram_tag, itmo_login, access_token, refresh_token,
                access_expires_at, refresh_expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                telegram_tag=excluded.telegram_tag,
                itmo_login=excluded.itmo_login,
                access_token=excluded.access_token,
                refresh_token=excluded.refresh_token,
                access_expires_at=excluded.access_expires_at,
                refresh_expires_at=excluded.refresh_expires_at;
            """,
            (
                tokens.chat_id,
                tokens.telegram_tag,
                tokens.itmo_login,
                tokens.access_token,
                tokens.refresh_token,
                tokens.access_expires_at,
                tokens.refresh_expires_at,
            ),
        )
        self._conn.commit()

    def get_tokens(self, telegram_id: int) -> UserTokens | None:
        row = self._conn.execute(
            """
            SELECT telegram_id, telegram_tag, itmo_login, access_token, refresh_token,
                   access_expires_at, refresh_expires_at
            FROM users
            WHERE telegram_id = ?;
            """,
            (telegram_id,),
        ).fetchone()
        if row is None:
            return None
        return UserTokens(
            chat_id=row["telegram_id"],
            telegram_tag=row["telegram_tag"],
            itmo_login=row["itmo_login"],
            access_token=row["access_token"],
            refresh_token=row["refresh_token"],
            access_expires_at=row["access_expires_at"],
            refresh_expires_at=row["refresh_expires_at"],
        )

    def list_all_user_ids(self) -> list[int]:
        rows = self._conn.execute(
            """
            SELECT telegram_id
            FROM users
            ORDER BY telegram_id ASC;
            """
        ).fetchall()
        return [int(row["telegram_id"]) for row in rows]

    def list_enabled_auto_enroll_user_ids(self) -> list[int]:
        rows = self._conn.execute(
            """
            SELECT DISTINCT telegram_id
            FROM auto_enroll_rules
            WHERE enabled = 1
            ORDER BY telegram_id ASC;
            """
        ).fetchall()
        return [int(row["telegram_id"]) for row in rows]

    def set_priority_mode(
        self,
        telegram_id: int,
        has_priority_mode: bool,
        checked_at_iso: str,
    ) -> None:
        self._conn.execute(
            """
            UPDATE users
            SET has_priority_mode = ?, priority_checked_at = ?
            WHERE telegram_id = ?;
            """,
            (1 if has_priority_mode else 0, checked_at_iso, telegram_id),
        )
        self._conn.commit()

    def set_priority_mode_bulk(
        self,
        updates: list[tuple[int, bool, str]],
    ) -> None:
        self._conn.executemany(
            """
            UPDATE users
            SET has_priority_mode = ?, priority_checked_at = ?
            WHERE telegram_id = ?;
            """,
            [(1 if has_mode else 0, checked_at_iso, telegram_id) for telegram_id, has_mode, checked_at_iso in updates],
        )
        self._conn.commit()

    def get_priority_mode(self, telegram_id: int) -> bool:
        row = self._conn.execute(
            """
            SELECT has_priority_mode
            FROM users
            WHERE telegram_id = ?;
            """,
            (telegram_id,),
        ).fetchone()
        if row is None:
            return False
        return bool(row["has_priority_mode"])

    def clear_itmo_binding(self, telegram_id: int) -> None:
        self._conn.execute(
            """
            UPDATE users
            SET itmo_login = '',
                access_token = '',
                refresh_token = '',
                access_expires_at = 0,
                refresh_expires_at = 0
            WHERE telegram_id = ?;
            """,
            (telegram_id,),
        )
        self._conn.commit()

    def upsert_auto_enroll_rule(self, rule: AutoEnrollRule) -> None:
        priority = rule.priority if rule.priority > 0 else self._next_user_priority(rule.chat_id)
        self._conn.execute(
            """
            INSERT INTO auto_enroll_rules (
                telegram_id, enabled, priority, section_name, day_code, time_slot_start, type_id, after_date
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id, section_name, day_code, time_slot_start, type_id, after_date) DO UPDATE SET
                enabled=excluded.enabled,
                section_name=excluded.section_name;
            """,
            (
                rule.chat_id,
                1 if rule.enabled else 0,
                priority,
                rule.section_name,
                rule.day_code,
                rule.time_slot_start,
                rule.type_id,
                rule.after_date,
            ),
        )
        self._conn.commit()

    def list_enabled_auto_enroll_rules(self) -> list[AutoEnrollRule]:
        rows = self._conn.execute(
            """
            SELECT id, telegram_id, enabled, priority, section_name, day_code, time_slot_start, type_id, after_date
            FROM auto_enroll_rules
            WHERE enabled = 1
            ORDER BY telegram_id ASC, priority ASC, id DESC;
            """
        ).fetchall()
        return [
            AutoEnrollRule(
                id=row["id"],
                chat_id=row["telegram_id"],
                enabled=bool(row["enabled"]),
                priority=row["priority"],
                section_name=row["section_name"],
                day_code=row["day_code"],
                time_slot_start=row["time_slot_start"],
                type_id=row["type_id"],
                after_date=row["after_date"],
            )
            for row in rows
        ]

    def list_user_auto_enroll_rules(self, telegram_id: int) -> list[AutoEnrollRule]:
        rows = self._conn.execute(
            """
            SELECT id, telegram_id, enabled, priority, section_name, day_code, time_slot_start, type_id, after_date
            FROM auto_enroll_rules
            WHERE telegram_id = ? AND enabled = 1
            ORDER BY priority ASC, id DESC;
            """,
            (telegram_id,),
        ).fetchall()
        return [
            AutoEnrollRule(
                id=row["id"],
                chat_id=row["telegram_id"],
                enabled=bool(row["enabled"]),
                priority=row["priority"],
                section_name=row["section_name"],
                day_code=row["day_code"],
                time_slot_start=row["time_slot_start"],
                type_id=row["type_id"],
                after_date=row["after_date"],
            )
            for row in rows
        ]

    def get_user_auto_enroll_rule(self, rule_id: int, telegram_id: int) -> AutoEnrollRule | None:
        row = self._conn.execute(
            """
            SELECT id, telegram_id, enabled, priority, section_name, day_code, time_slot_start, type_id, after_date
            FROM auto_enroll_rules
            WHERE id = ? AND telegram_id = ? AND enabled = 1;
            """,
            (rule_id, telegram_id),
        ).fetchone()
        if row is None:
            return None
        return AutoEnrollRule(
            id=row["id"],
            chat_id=row["telegram_id"],
            enabled=bool(row["enabled"]),
            priority=row["priority"],
            section_name=row["section_name"],
            day_code=row["day_code"],
            time_slot_start=row["time_slot_start"],
            type_id=row["type_id"],
            after_date=row["after_date"],
        )

    def disable_auto_enroll_rule(self, rule_id: int, telegram_id: int) -> None:
        self._conn.execute(
            """
            UPDATE auto_enroll_rules
            SET enabled = 0
            WHERE id = ? AND telegram_id = ?;
            """,
            (rule_id, telegram_id),
        )
        self._conn.commit()

    def _next_user_priority(self, telegram_id: int) -> int:
        row = self._conn.execute(
            """
            SELECT COALESCE(MAX(priority), 0) AS max_priority
            FROM auto_enroll_rules
            WHERE telegram_id = ?;
            """,
            (telegram_id,),
        ).fetchone()
        if row is None:
            return 1
        return int(row["max_priority"] or 0) + 1

    def reorder_user_auto_enroll_rule(self, rule_id: int, telegram_id: int, direction: str) -> None:
        rules = self.list_user_auto_enroll_rules(telegram_id)
        ids = [r.id for r in rules]
        if rule_id not in ids:
            return
        idx = ids.index(rule_id)
        if direction == "up" and idx > 0:
            ids[idx - 1], ids[idx] = ids[idx], ids[idx - 1]
        elif direction == "down" and idx < len(ids) - 1:
            ids[idx + 1], ids[idx] = ids[idx], ids[idx + 1]
        else:
            return
        for priority, rid in enumerate(ids, start=1):
            self._conn.execute(
                """
                UPDATE auto_enroll_rules
                SET priority = ?
                WHERE id = ? AND telegram_id = ?;
                """,
                (priority, rid, telegram_id),
            )
        self._conn.commit()

    def set_user_rule_priorities(self, telegram_id: int, ordered_rule_ids: list[int]) -> None:
        for priority, rid in enumerate(ordered_rule_ids, start=1):
            self._conn.execute(
                """
                UPDATE auto_enroll_rules
                SET priority = ?
                WHERE id = ? AND telegram_id = ? AND enabled = 1;
                """,
                (priority, rid, telegram_id),
            )
        self._conn.commit()

    def get_weekly_limit(self, telegram_id: int) -> int | None:
        row = self._conn.execute(
            """
            SELECT weekly_limit, weekly_limit_set
            FROM users
            WHERE telegram_id = ?;
            """,
            (telegram_id,),
        ).fetchone()
        if row is None:
            return None
        if not bool(row["weekly_limit_set"]):
            return None
        return int(row["weekly_limit"] or 0)

    def set_weekly_limit(self, telegram_id: int, limit: int) -> None:
        self._conn.execute(
            """
            UPDATE users
            SET weekly_limit = ?, weekly_limit_set = 1
            WHERE telegram_id = ?;
            """,
            (max(1, limit), telegram_id),
        )
        self._conn.commit()

    def set_auto_enroll_last_lesson(self, rule_id: int, lesson_id: int) -> None:
        self._conn.execute(
            """
            UPDATE auto_enroll_rules
            SET last_lesson_id = ?
            WHERE id = ?;
            """,
            (lesson_id, rule_id),
        )
        self._conn.commit()

    def get_auto_enroll_last_lesson(self, rule_id: int) -> int:
        row = self._conn.execute(
            """
            SELECT last_lesson_id
            FROM auto_enroll_rules
            WHERE id = ?;
            """,
            (rule_id,),
        ).fetchone()
        if row is None:
            return 0
        return int(row["last_lesson_id"] or 0)

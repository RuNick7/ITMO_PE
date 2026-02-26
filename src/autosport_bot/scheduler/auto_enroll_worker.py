from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

from aiogram import Bot
import httpx

from autosport_bot.core.config import get_settings
from autosport_bot.my_itmo.auth import ItmoAuthService
from autosport_bot.my_itmo.client import MyItmoClient
from autosport_bot.storage.models import AutoEnrollRule
from autosport_bot.storage.repository import SQLiteRepository

logger = logging.getLogger(__name__)


class AutoEnrollWorker:
    def __init__(self, repository: SQLiteRepository, poll_interval_seconds: int = 30):
        self._repo = repository
        self._poll_interval_seconds = poll_interval_seconds
        self._running = False

    @staticmethod
    def _lesson_matches_rule(lesson: dict, rule: AutoEnrollRule) -> bool:
        can_sign = lesson.get("can_sign_in") or {}
        if isinstance(can_sign, dict) and not bool(can_sign.get("can_sign_in")):
            return False
        if str(lesson.get("section_name") or "") != rule.section_name:
            return False
        if str(lesson.get("time_slot_start") or "") != rule.time_slot_start:
            return False
        if int(lesson.get("type_id") or 0) != int(rule.type_id):
            return False

        raw_date = lesson.get("date")
        if not raw_date:
            return False
        lesson_date = datetime.fromisoformat(str(raw_date)).date()
        if rule.after_date and lesson_date <= datetime.fromisoformat(rule.after_date).date():
            return False
        if lesson_date.weekday() != rule.day_code:
            return False
        return True

    async def run_forever(self, bot: Bot) -> None:
        self._running = True
        logger.info("Auto-enroll worker started")
        while self._running:
            await self._tick(bot)
            await asyncio.sleep(self._poll_interval_seconds)

    async def _tick(self, bot: Bot) -> None:
        rules = self._repo.list_enabled_auto_enroll_rules()
        if not rules:
            return

        grouped: dict[int, list[AutoEnrollRule]] = defaultdict(list)
        for rule in rules:
            grouped[rule.chat_id].append(rule)

        for chat_id, user_rules in grouped.items():
            tokens = self._repo.get_tokens(chat_id)
            if tokens is None or not tokens.access_token:
                continue

            async def get_payload(access_token: str) -> dict:
                client = MyItmoClient(access_token)
                date_start = date.today()
                date_end = date_start + timedelta(days=21)
                return await client.get_sport_schedule(
                    date_start=date_start.isoformat(),
                    date_end=date_end.isoformat(),
                )

            try:
                payload = await get_payload(tokens.access_token)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 401 or not tokens.refresh_token:
                    logger.warning("Auto-enroll schedule fetch error for chat_id=%s: %s", chat_id, exc)
                    continue
                try:
                    refreshed = await ItmoAuthService(get_settings()).refresh(tokens.refresh_token)
                    tokens.access_token = refreshed.access_token
                    tokens.refresh_token = refreshed.refresh_token
                    tokens.access_expires_at = refreshed.access_expires_at
                    tokens.refresh_expires_at = refreshed.refresh_expires_at
                    self._repo.save_tokens(tokens)
                    payload = await get_payload(tokens.access_token)
                except Exception as refresh_exc:
                    logger.warning("Auto-enroll refresh error for chat_id=%s: %s", chat_id, refresh_exc)
                    continue
            except Exception as exc:
                logger.warning("Auto-enroll schedule fetch error for chat_id=%s: %s", chat_id, exc)
                continue

            lessons: list[dict] = []
            for day in (payload.get("result") or []):
                if not isinstance(day, dict):
                    continue
                for item in (day.get("lessons") or []):
                    if isinstance(item, dict):
                        lessons.append(item)

            if not lessons:
                continue

            weekly_limit = self._repo.get_weekly_limit(chat_id)
            signed_per_week = self._count_signed_per_week(lessons)
            user_rules.sort(key=lambda r: (r.priority if r.priority > 0 else 10_000, r.id))

            for rule in user_rules:
                candidates = [it for it in lessons if self._lesson_matches_rule(it, rule)]
                if not candidates:
                    continue

                candidates.sort(key=lambda it: str(it.get("date") or ""))
                selected: dict | None = None
                for candidate in candidates:
                    lesson_id = int(candidate.get("id") or 0)
                    if lesson_id == 0:
                        continue
                    if self._repo.get_auto_enroll_last_lesson(rule.id) == lesson_id:
                        continue
                    if int(candidate.get("available") or 0) <= 0:
                        continue
                    week_key = self._week_key(candidate)
                    if week_key is None:
                        continue
                    if weekly_limit is not None and signed_per_week.get(week_key, 0) >= weekly_limit:
                        continue
                    selected = candidate
                    break

                if selected is None:
                    continue

                lesson_id = int(selected.get("id") or 0)
                week_key = self._week_key(selected)
                if lesson_id == 0 or week_key is None:
                    continue
                try:
                    client = MyItmoClient(tokens.access_token)
                    result = await client.sign_for_lesson(lesson_id)
                    if not result.get("ok"):
                        raise RuntimeError("sign_for_lesson returned not ok")
                    self._repo.set_auto_enroll_last_lesson(rule.id, lesson_id)
                    signed_per_week[week_key] = signed_per_week.get(week_key, 0) + 1
                    await bot.send_message(
                        chat_id=rule.chat_id,
                        text=(
                            "✅ Автозапись выполнена!\n"
                            f"{selected.get('section_name')} | {selected.get('date')} {selected.get('time_slot_start')}\n"
                            f"ID: {lesson_id}"
                        ),
                    )
                except Exception as exc:
                    logger.warning(
                        "Auto-enroll sign error for chat_id=%s lesson_id=%s: %s",
                        rule.chat_id,
                        lesson_id,
                        exc,
                    )

    @staticmethod
    def _week_key(lesson: dict) -> str | None:
        raw_date = lesson.get("date")
        if not raw_date:
            return None
        try:
            dt = datetime.fromisoformat(str(raw_date)).date()
        except ValueError:
            return None
        iso = dt.isocalendar()
        return f"{iso.year}-{iso.week:02d}"

    def _count_signed_per_week(self, lessons: list[dict]) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for lesson in lessons:
            if not bool(lesson.get("signed")):
                continue
            week_key = self._week_key(lesson)
            if week_key is None:
                continue
            counts[week_key] += 1
        return counts

    def stop(self) -> None:
        self._running = False

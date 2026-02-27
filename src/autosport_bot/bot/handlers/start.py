from aiogram import Router
from aiogram.filters import Command
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message
from datetime import date, datetime, timedelta
import httpx

from autosport_bot.bot import context as bot_context
from autosport_bot.bot.keyboards import (
    auto_bulk_offer_keyboard,
    auto_confirm_keyboard,
    back_to_choose_keyboard,
    choose_day_keyboard,
    choose_mode_keyboard,
    choose_time_keyboard,
    help_contact_keyboard,
    main_menu_keyboard,
    my_sport_cancel_confirm_keyboard,
    my_sport_detail_keyboard,
    my_sport_list_keyboard,
    weekly_limit_keyboard,
    settings_delete_confirm_keyboard,
    settings_keyboard,
    subject_autoreg_mode_keyboard,
    subject_catalog_keyboard,
    sport_lessons_keyboard,
    to_menu_keyboard,
)
from autosport_bot.core.config import get_settings
from autosport_bot.my_itmo.auth import ItmoAuthService
from autosport_bot.my_itmo.client import MyItmoClient
from autosport_bot.storage.models import AutoEnrollRule, UserTokens

router = Router()
SEARCH_BY_NAME_WAITING: set[int] = set()
SEARCH_DAY_QUERY: dict[int, str] = {}
LIST_SELECTION_CONTEXT: dict[int, dict[str, str]] = {}
AUTH_WAITING_LOGIN: set[int] = set()
AUTH_WAITING_PASSWORD: dict[int, str] = {}
PENDING_AUTOREG: dict[int, dict] = {}
PENDING_BULK_ENROLL: dict[int, AutoEnrollRule] = {}
PENDING_CANCEL_ALL_RULE: dict[int, int] = {}
PRIORITY_EDIT_WAITING: dict[int, list[int]] = {}
PENDING_SUBJECT_MATCHES: dict[int, list[str]] = {}
PENDING_SUBJECT_AUTOREG: dict[int, str] = {}


def _reset_user_interaction_state(user_id: int) -> None:
    SEARCH_BY_NAME_WAITING.discard(user_id)
    SEARCH_DAY_QUERY.pop(user_id, None)
    LIST_SELECTION_CONTEXT.pop(user_id, None)
    AUTH_WAITING_LOGIN.discard(user_id)
    AUTH_WAITING_PASSWORD.pop(user_id, None)
    PENDING_AUTOREG.pop(user_id, None)
    PENDING_BULK_ENROLL.pop(user_id, None)
    PENDING_CANCEL_ALL_RULE.pop(user_id, None)
    PRIORITY_EDIT_WAITING.pop(user_id, None)
    PENDING_SUBJECT_MATCHES.pop(user_id, None)
    PENDING_SUBJECT_AUTOREG.pop(user_id, None)


def _legend_text() -> str:
    return (
        "Легенда:\n"
        "🔴 задолженность\n"
        "🟢 свободные посещения\n"
        "🩵 открытые занятия\n"
        "🔵 секция\n"
        "⚪ другой/неопределённый тип"
    )


def _lesson_type_title(lesson: dict) -> str:
    can_sign = lesson.get("can_sign_in") or {}
    reasons = can_sign.get("unavailable_reasons") if isinstance(can_sign, dict) else None
    reasons_text = " ".join(reasons).lower() if isinstance(reasons, list) else ""
    type_id = int(lesson.get("type_id") or 0)
    section_level = int(lesson.get("section_level") or 0)

    if "задолж" in reasons_text or type_id == 5:
        return "Занятие для задолженности"
    if section_level == 2 or "отбор" in reasons_text:
        return "Секция"
    if type_id == 1:
        return "Открытое занятие"
    if type_id == 2:
        return "Свободное посещение"
    return "Другое"


def _format_lesson_day_time(raw_date: str | None, raw_time: str | None) -> str:
    day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    if not raw_date:
        return f"— | {raw_time or '--:--'}"
    try:
        dt = datetime.fromisoformat(str(raw_date))
        day = day_names[dt.weekday()]
        # Prefer real start from date because time_slot_start can be slot label.
        time_part = dt.strftime("%H:%M")
        return f"{day} | {time_part}"
    except ValueError:
        return f"— | {raw_time or '--:--'}"


def _my_sport_detail_text(rule: AutoEnrollRule) -> str:
    day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    day = day_names[rule.day_code] if 0 <= rule.day_code <= 6 else "Любой"
    type_map = {
        1: "Открытое занятие",
        2: "Свободное посещение",
        5: "Занятие для задолженности",
    }
    type_title = type_map.get(rule.type_id, "Другое")
    return (
        "Информация об автозаписи:\n"
        f"Предмет: {rule.section_name}\n"
        f"День: {day}\n"
        f"Время: {rule.time_slot_start}\n"
        f"Тип: {type_title}\n"
        f"Дата-ориентир: после {rule.after_date}"
    )


def _available_days_for_query(lessons: list[dict], query: str) -> list[int]:
    matched = _filter_lessons(
        lessons=lessons,
        day_code="any",
        time_code="any",
        query=query,
    )
    days: set[int] = set()
    for item in matched:
        raw_date = item.get("date")
        if not raw_date:
            continue
        try:
            item_date = datetime.fromisoformat(str(raw_date)).date()
        except ValueError:
            continue
        days.add(item_date.weekday())
    return sorted(days)


async def _cleanup_callback_message(callback: CallbackQuery) -> None:
    if callback.message is None:
        return
    try:
        await callback.message.delete()
    except Exception:
        # Message can be too old/non-deletable; keep flow working.
        pass


def _friendly_schedule_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        if status_code == 403:
            return (
                "Похоже, у тебя сейчас нет доступа к записи на физру в my.itmo "
                "(403 Forbidden). Обычно это значит, что физра ещё не назначена."
            )
        if status_code == 401:
            return "Сессия истекла. Повтори вход: /login <itmo_login> <itmo_password>"
    return f"Ошибка при получении расписания: {exc}"


async def _fetch_lessons_for_user(telegram_id: int) -> tuple[list[dict], str | None]:
    if bot_context.repository is None:
        return [], "Ошибка: хранилище не инициализировано."
    tokens = bot_context.repository.get_tokens(telegram_id)
    if tokens is None or not tokens.access_token:
        return [], "Сначала войди в ITMO.ID: /login <itmo_login> <itmo_password>"

    async def request_schedule(access_token: str) -> dict:
        client = MyItmoClient(access_token)
        date_start = date.today()
        date_end = date_start + timedelta(days=21)
        return await client.get_sport_schedule(
            date_start=date_start.isoformat(),
            date_end=date_end.isoformat(),
        )

    try:
        payload = await request_schedule(tokens.access_token)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 401:
            return [], _friendly_schedule_error(exc)
        if not tokens.refresh_token:
            return [], "Токен истёк. Повтори вход: /login <itmo_login> <itmo_password>"

        # Access token expired: refresh and retry once.
        auth_service = ItmoAuthService(get_settings())
        try:
            refreshed = await auth_service.refresh(tokens.refresh_token)
        except Exception:
            return [], "Не удалось обновить токен. Повтори вход: /login <itmo_login> <itmo_password>"

        tokens.access_token = refreshed.access_token
        tokens.refresh_token = refreshed.refresh_token
        tokens.access_expires_at = refreshed.access_expires_at
        tokens.refresh_expires_at = refreshed.refresh_expires_at
        bot_context.repository.save_tokens(tokens)

        try:
            payload = await request_schedule(tokens.access_token)
        except Exception as retry_exc:
            return [], _friendly_schedule_error(retry_exc)
    except Exception as exc:
        return [], _friendly_schedule_error(exc)

    lessons: list[dict] = []
    for day in (payload.get("result") or []):
        if not isinstance(day, dict):
            continue
        day_lessons = day.get("lessons") or []
        if not isinstance(day_lessons, list):
            continue
        lessons.extend([item for item in day_lessons if isinstance(item, dict)])
    return lessons, None


async def _fetch_subject_catalog_for_user(telegram_id: int) -> tuple[list[str], str | None]:
    if bot_context.repository is None:
        return [], "Ошибка: хранилище не инициализировано."
    tokens = bot_context.repository.get_tokens(telegram_id)
    if tokens is None or not tokens.access_token:
        return [], "Сначала войди в ITMO.ID: /login <itmo_login> <itmo_password>"

    async def request_schedule(access_token: str) -> dict:
        client = MyItmoClient(access_token)
        date_start = date.today() - timedelta(days=365)
        date_end = date.today() + timedelta(days=365)
        return await client.get_sport_schedule(
            date_start=date_start.isoformat(),
            date_end=date_end.isoformat(),
        )

    try:
        payload = await request_schedule(tokens.access_token)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 401:
            return [], _friendly_schedule_error(exc)
        if not tokens.refresh_token:
            return [], "Токен истёк. Повтори вход: /login <itmo_login> <itmo_password>"
        auth_service = ItmoAuthService(get_settings())
        try:
            refreshed = await auth_service.refresh(tokens.refresh_token)
        except Exception:
            return [], "Не удалось обновить токен. Повтори вход: /login <itmo_login> <itmo_password>"
        tokens.access_token = refreshed.access_token
        tokens.refresh_token = refreshed.refresh_token
        tokens.access_expires_at = refreshed.access_expires_at
        tokens.refresh_expires_at = refreshed.refresh_expires_at
        bot_context.repository.save_tokens(tokens)
        try:
            payload = await request_schedule(tokens.access_token)
        except Exception as retry_exc:
            return [], _friendly_schedule_error(retry_exc)
    except Exception as exc:
        return [], _friendly_schedule_error(exc)

    subjects: set[str] = set()
    for day in (payload.get("result") or []):
        if not isinstance(day, dict):
            continue
        for item in (day.get("lessons") or []):
            if not isinstance(item, dict):
                continue
            name = str(item.get("section_name") or "").strip()
            if name:
                subjects.add(name)
    return sorted(subjects), None


async def _authenticate_and_save(
    telegram_id: int,
    telegram_tag: str,
    itmo_login: str,
    itmo_password: str,
) -> tuple[bool, str]:
    if bot_context.repository is None:
        return False, "Ошибка: хранилище не инициализировано."

    auth_service = ItmoAuthService(get_settings())
    try:
        pair = await auth_service.login(itmo_login, itmo_password)
    except Exception as exc:
        return False, f"Ошибка входа: {exc}"

    bot_context.repository.save_tokens(
        UserTokens(
            chat_id=telegram_id,
            telegram_tag=telegram_tag,
            itmo_login=itmo_login,
            access_token=pair.access_token,
            refresh_token=pair.refresh_token,
            access_expires_at=pair.access_expires_at,
            refresh_expires_at=pair.refresh_expires_at,
        )
    )
    return True, "Успешно! Токены получены и сохранены в БД."


def _filter_lessons(
    lessons: list[dict],
    day_code: str,
    time_code: str,
    query: str | None = None,
    deduplicate: bool = False,
) -> list[dict]:
    # Remove section-based classes from bot flows.
    filtered = []
    for item in lessons:
        if int(item.get("section_level") or 0) == 2:
            continue
        can_sign = item.get("can_sign_in") or {}
        if isinstance(can_sign, dict) and not bool(can_sign.get("can_sign_in")):
            continue
        filtered.append(item)

    if query:
        q = query.lower()
        filtered = [
            item for item in filtered if q in str(item.get("section_name") or "").lower()
        ]

    if day_code != "any":
        try:
            day_index = int(day_code)
        except ValueError:
            return filtered
        day_filtered: list[dict] = []
        for item in filtered:
            raw_date = item.get("date")
            if not raw_date:
                continue
            try:
                item_date = datetime.fromisoformat(str(raw_date)).date()
            except ValueError:
                continue
            if item_date.weekday() == day_index:
                day_filtered.append(item)
        filtered = day_filtered

    if time_code != "any":
        if time_code.startswith("h") and len(time_code) == 3 and time_code[1:].isdigit():
            target_hour = int(time_code[1:])
            hour_filtered: list[dict] = []
            for item in filtered:
                raw_time = str(item.get("time_slot_start") or "")
                try:
                    lesson_hour = int(raw_time.split(":", maxsplit=1)[0])
                except (ValueError, IndexError):
                    continue
                if lesson_hour == target_hour:
                    hour_filtered.append(item)
            filtered = hour_filtered
        else:
            filtered = [
                item
                for item in filtered
                if str(item.get("time_slot_start") or "") == time_code
            ]

    if deduplicate:
        unique: dict[tuple[str, str, int, int], dict] = {}
        for item in filtered:
            raw_date = item.get("date")
            weekday = -1
            if raw_date:
                try:
                    weekday = datetime.fromisoformat(str(raw_date)).date().weekday()
                except ValueError:
                    weekday = -1
            key = (
                str(item.get("section_name") or ""),
                str(item.get("time_slot_start") or ""),
                int(item.get("type_id") or 0),
                weekday,
            )
            current = unique.get(key)
            if current is None:
                unique[key] = item
                continue
            current_date = str(current.get("date") or "")
            item_date = str(item.get("date") or "")
            if item_date and (not current_date or item_date < current_date):
                unique[key] = item
        filtered = list(unique.values())

    filtered.sort(key=lambda it: (str(it.get("time_slot_start") or "99:99"), str(it.get("section_name") or "")))
    return filtered


@router.message(CommandStart())
async def start_command(message: Message) -> None:
    if message.from_user is not None:
        # /start should cancel any in-progress flow to avoid mixed states.
        _reset_user_interaction_state(message.from_user.id)
    if bot_context.repository is not None and message.from_user is not None:
        bot_context.repository.ensure_user(
            telegram_id=message.from_user.id,
            telegram_tag=message.from_user.username or "",
        )
        user_tokens = bot_context.repository.get_tokens(message.from_user.id)
        if user_tokens is None or not user_tokens.access_token:
            AUTH_WAITING_LOGIN.add(message.from_user.id)
            await message.answer(
                "Привет! Для первого входа введите логин ITMO.ID.\n"
                "Следующим сообщением попрошу пароль."
            )
            return

    await message.answer(
        "Привет! Я AutoSport Bot.\n"
        "Выбери действие в меню ниже.",
        reply_markup=main_menu_keyboard(),
    )


@router.message(Command("set_itmo"))
async def set_itmo_command(message: Message) -> None:
    if bot_context.repository is None:
        await message.answer("Ошибка: хранилище не инициализировано.")
        return
    if message.from_user is None:
        await message.answer("Ошибка: не удалось определить пользователя Telegram.")
        return
    if not message.text:
        await message.answer("Использование: /set_itmo <login> <access_token> <refresh_token>")
        return

    parts = message.text.split(maxsplit=3)
    if len(parts) < 4:
        await message.answer("Использование: /set_itmo <login> <access_token> <refresh_token>")
        return

    _, itmo_login, access_token, refresh_token = parts

    bot_context.repository.save_tokens(
        UserTokens(
            chat_id=message.from_user.id,
            telegram_tag=message.from_user.username or "",
            itmo_login=itmo_login,
            access_token=access_token,
            refresh_token=refresh_token,
        )
    )
    await message.answer("Данные сохранены в БД.")


@router.message(Command("login"))
async def login_command(message: Message) -> None:
    if bot_context.repository is None:
        await message.answer("Ошибка: хранилище не инициализировано.")
        return
    if message.from_user is None:
        await message.answer("Ошибка: не удалось определить пользователя Telegram.")
        return
    if not message.text:
        await message.answer("Использование: /login <itmo_login> <itmo_password>")
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Использование: /login <itmo_login> <itmo_password>")
        return

    _, itmo_login, itmo_password = parts
    await message.answer("Пробую войти в ITMO.ID, подожди...")
    ok, text = await _authenticate_and_save(
        telegram_id=message.from_user.id,
        telegram_tag=message.from_user.username or "",
        itmo_login=itmo_login,
        itmo_password=itmo_password,
    )
    await message.answer(text, reply_markup=main_menu_keyboard() if ok else None)


@router.message(Command("sport"))
async def sport_command(message: Message) -> None:
    if bot_context.repository is None:
        await message.answer("Ошибка: хранилище не инициализировано.")
        return
    if message.from_user is None:
        await message.answer("Ошибка: не удалось определить пользователя Telegram.")
        return

    tokens = bot_context.repository.get_tokens(message.from_user.id)
    if tokens is None or not tokens.access_token:
        await message.answer(
            "Сначала войди в ITMO.ID:\n"
            "/login <itmo_login> <itmo_password>"
        )
        return

    client = MyItmoClient(tokens.access_token)
    date_start = date.today()
    date_end = date_start + timedelta(days=21)

    try:
        payload = await client.get_sport_schedule(
            date_start=date_start.isoformat(),
            date_end=date_end.isoformat(),
        )
    except Exception as exc:
        await message.answer(_friendly_schedule_error(exc))
        return

    lessons: list[dict] = []
    result = payload.get("result") or []
    for day in result:
        if not isinstance(day, dict):
            continue
        day_lessons = day.get("lessons") or []
        if not isinstance(day_lessons, list):
            continue
        lessons.extend([item for item in day_lessons if isinstance(item, dict)])
    lessons = _filter_lessons(lessons, day_code="any", time_code="any")

    if not lessons:
        await message.answer(
            "На выбранный период доступных занятий не найдено.",
            reply_markup=to_menu_keyboard(),
        )
        return

    lines: list[str] = []
    for lesson in lessons[:20]:
        lines.append(
            f"ID {lesson.get('id')} | {lesson.get('section_name')} | "
            f"{lesson.get('date')} {lesson.get('time_slot_start')} | "
            f"мест: {lesson.get('available')}/{lesson.get('limit')}"
        )

    await message.answer(
        "Доступные занятия (первые 20):\n" + "\n".join(lines) + "\n\n"
        "Чтобы увидеть актуальные данные снова, запусти /sport."
    )


@router.message(Command("choose"))
async def choose_command(message: Message) -> None:
    await message.answer(
        "Как хочешь выбрать занятие?",
        reply_markup=choose_mode_keyboard(),
    )


@router.callback_query(lambda c: c.data == "choose_sport")
async def choose_sport_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await _cleanup_callback_message(callback)
    if callback.message is not None:
        await callback.message.answer(
            "Как хочешь выбрать занятие?",
            reply_markup=choose_mode_keyboard(),
        )


@router.callback_query(lambda c: c.data == "back_main")
async def back_main_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.from_user is not None:
        _reset_user_interaction_state(callback.from_user.id)
    await _cleanup_callback_message(callback)
    if callback.message is not None:
        await callback.message.answer(
            "Главное меню:",
            reply_markup=main_menu_keyboard(),
        )


@router.callback_query(lambda c: c.data == "choose_input_name")
async def choose_input_name_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await _cleanup_callback_message(callback)
    if callback.from_user is not None:
        SEARCH_BY_NAME_WAITING.add(callback.from_user.id)
    if callback.message is not None:
        await callback.message.answer("Введи название физры текстом, например: йога")


@router.callback_query(lambda c: c.data == "choose_list")
async def choose_list_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await _cleanup_callback_message(callback)
    if callback.from_user is None:
        if callback.message is not None:
            await callback.message.answer("Ошибка: не удалось определить пользователя Telegram.")
        return
    LIST_SELECTION_CONTEXT[callback.from_user.id] = {"day": "any", "time": "any"}

    if callback.message is not None:
        await callback.message.answer(
            "Выбери день недели:",
            reply_markup=choose_day_keyboard(),
        )


@router.callback_query(lambda c: c.data is not None and c.data.startswith("subject_catalog_pick:"))
async def subject_catalog_pick_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await _cleanup_callback_message(callback)
    if callback.from_user is None:
        if callback.message is not None:
            await callback.message.answer("Ошибка: не удалось определить пользователя Telegram.")
        return
    try:
        idx = int(callback.data.split(":", maxsplit=1)[1])
    except Exception:
        if callback.message is not None:
            await callback.message.answer("Некорректный выбор предмета.")
        return
    matches = PENDING_SUBJECT_MATCHES.get(callback.from_user.id) or []
    if idx < 0 or idx >= len(matches):
        if callback.message is not None:
            await callback.message.answer("Список устарел, попробуй поиск ещё раз.")
        return
    subject = matches[idx]
    PENDING_SUBJECT_AUTOREG[callback.from_user.id] = subject
    if callback.message is not None:
        await callback.message.answer(
            f"Предмет: {subject}\nВыбери режим автозаписи:",
            reply_markup=subject_autoreg_mode_keyboard(),
        )


@router.callback_query(lambda c: c.data is not None and c.data.startswith("subject_autoreg:"))
async def subject_autoreg_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await _cleanup_callback_message(callback)
    if callback.from_user is None:
        if callback.message is not None:
            await callback.message.answer("Ошибка: не удалось определить пользователя Telegram.")
        return
    if bot_context.repository is None:
        if callback.message is not None:
            await callback.message.answer("Ошибка: хранилище не инициализировано.")
        return
    mode = callback.data.split(":", maxsplit=1)[1]
    subject = PENDING_SUBJECT_AUTOREG.pop(callback.from_user.id, "")
    PENDING_SUBJECT_MATCHES.pop(callback.from_user.id, None)
    if not subject:
        if callback.message is not None:
            await callback.message.answer("Сначала выбери предмет из списка.")
        return
    rule = AutoEnrollRule(
        chat_id=callback.from_user.id,
        enabled=True,
        section_name=subject,
        day_code=-1,
        time_slot_start="",
        type_id=1 if mode == "open" else 0,
        after_date=date.today().isoformat(),
    )
    bot_context.repository.upsert_auto_enroll_rule(rule)
    mode_text = "только открытые занятия" if mode == "open" else "любые занятия"
    if callback.message is not None:
        await callback.message.answer(
            f"✅ Автозапись включена: {subject}\nРежим: {mode_text}.",
            reply_markup=to_menu_keyboard(),
        )


@router.callback_query(lambda c: c.data is not None and c.data.startswith("choose_day:"))
async def choose_day_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await _cleanup_callback_message(callback)
    if callback.from_user is None:
        if callback.message is not None:
            await callback.message.answer("Ошибка: не удалось определить пользователя Telegram.")
        return

    day_code = callback.data.split(":", maxsplit=1)[1]

    # Flow for "enter name": query -> day -> show keyboard (subject | time)
    query = SEARCH_DAY_QUERY.pop(callback.from_user.id, None)
    if query is not None:
        lessons, error = await _fetch_lessons_for_user(callback.from_user.id)
        if error:
            if callback.message is not None:
                await callback.message.answer(error)
            return
        filtered_lessons = _filter_lessons(
            lessons,
            day_code=day_code,
            time_code="any",
            query=query,
            deduplicate=True,
        )
        if not filtered_lessons:
            if callback.message is not None:
                await callback.message.answer(
                    "Под выбранные день и название занятия ничего не найдено.",
                    reply_markup=back_to_choose_keyboard(),
                )
            return
        if callback.message is not None:
            await callback.message.answer(
                "Нашёл занятия. Выбери нужное:\n\n" + _legend_text(),
                reply_markup=sport_lessons_keyboard(filtered_lessons, show_time=True),
            )
        return

    LIST_SELECTION_CONTEXT.setdefault(callback.from_user.id, {"time": "any"})
    LIST_SELECTION_CONTEXT[callback.from_user.id]["day"] = day_code

    if callback.message is not None:
        await callback.message.answer(
            "Теперь выбери время:",
            reply_markup=choose_time_keyboard(),
        )


@router.callback_query(lambda c: c.data is not None and c.data.startswith("choose_time:"))
async def choose_time_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await _cleanup_callback_message(callback)
    if callback.from_user is None:
        if callback.message is not None:
            await callback.message.answer("Ошибка: не удалось определить пользователя Telegram.")
        return

    time_code = callback.data.replace("choose_time:", "", 1)
    LIST_SELECTION_CONTEXT.setdefault(callback.from_user.id, {"day": "any"})
    LIST_SELECTION_CONTEXT[callback.from_user.id]["time"] = time_code

    context = LIST_SELECTION_CONTEXT.get(callback.from_user.id, {"day": "any", "time": "any"})
    lessons, error = await _fetch_lessons_for_user(callback.from_user.id)
    if error:
        if callback.message is not None:
            await callback.message.answer(error)
        return

    filtered_lessons = _filter_lessons(
        lessons,
        day_code=context.get("day", "any"),
        time_code=context.get("time", "any"),
        deduplicate=True,
    )
    if not filtered_lessons:
        if callback.message is not None:
            await callback.message.answer(
                "Под выбранные день/время занятия не найдены.",
                reply_markup=back_to_choose_keyboard(),
            )
        return
    if callback.message is not None:
        await callback.message.answer(
            "Выбери занятие:\n\n" + _legend_text(),
            reply_markup=sport_lessons_keyboard(filtered_lessons, show_time=True),
        )


@router.callback_query(lambda c: c.data is not None and c.data.startswith("sport_pick:"))
async def sport_pick_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await _cleanup_callback_message(callback)
    if bot_context.repository is None:
        if callback.message is not None:
            await callback.message.answer("Ошибка: хранилище не инициализировано.")
        return
    if callback.from_user is None:
        if callback.message is not None:
            await callback.message.answer("Ошибка: не удалось определить пользователя Telegram.")
        return

    lesson_id = callback.data.split(":", maxsplit=1)[1]
    tokens = bot_context.repository.get_tokens(callback.from_user.id)
    if tokens is None or not tokens.access_token:
        if callback.message is not None:
            await callback.message.answer("Сначала войди в ITMO.ID: /login <itmo_login> <itmo_password>")
        return

    client = MyItmoClient(tokens.access_token)
    date_start = date.today()
    date_end = date_start + timedelta(days=21)
    try:
        payload = await client.get_sport_schedule(date_start=date_start.isoformat(), date_end=date_end.isoformat())
    except Exception as exc:
        if callback.message is not None:
            await callback.message.answer(_friendly_schedule_error(exc))
        return

    selected_lesson: dict | None = None
    for day in (payload.get("result") or []):
        if not isinstance(day, dict):
            continue
        day_lessons = day.get("lessons") or []
        if not isinstance(day_lessons, list):
            continue
        for item in day_lessons:
            if isinstance(item, dict) and str(item.get("id")) == lesson_id:
                if int(item.get("section_level") or 0) == 2:
                    continue
                selected_lesson = item
                break
        if selected_lesson is not None:
            break

    if selected_lesson is None:
        if callback.message is not None:
            await callback.message.answer("Занятие не найдено. Обнови список через /choose.")
        return

    if callback.from_user is not None:
        PENDING_AUTOREG[callback.from_user.id] = selected_lesson

    if callback.message is not None:
        day_time = _format_lesson_day_time(
            selected_lesson.get("date"),
            selected_lesson.get("time_slot_start"),
        )
        await callback.message.answer(
            "Выбрано занятие:\n"
            f"Название: {selected_lesson.get('section_name')}\n"
            f"Тип: {_lesson_type_title(selected_lesson)}\n"
            f"День | Время: {day_time}\n"
            f"Мест всего: {selected_lesson.get('limit')}\n\n"
            "Если хочешь, включи автозапись на такое же занятие на будущие недели.",
        )
        await callback.message.answer(
            "Подтвердить автозапись?",
            reply_markup=auto_confirm_keyboard(),
        )


@router.callback_query(lambda c: c.data == "auto_cancel")
async def auto_cancel_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await _cleanup_callback_message(callback)
    if callback.from_user is not None:
        PENDING_AUTOREG.pop(callback.from_user.id, None)
    if callback.message is not None:
        await callback.message.answer("Автозапись отменена.")


@router.callback_query(lambda c: c.data == "auto_confirm")
async def auto_confirm_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await _cleanup_callback_message(callback)
    if callback.from_user is None:
        if callback.message is not None:
            await callback.message.answer("Ошибка: не удалось определить пользователя Telegram.")
        return
    if bot_context.repository is None:
        if callback.message is not None:
            await callback.message.answer("Ошибка: хранилище не инициализировано.")
        return

    lesson = PENDING_AUTOREG.pop(callback.from_user.id, None)
    if not lesson:
        if callback.message is not None:
            await callback.message.answer("Сначала выбери занятие.")
        return

    raw_date = lesson.get("date")
    day_code = -1
    after_date = ""
    if raw_date:
        dt = datetime.fromisoformat(str(raw_date))
        day_code = dt.weekday()
        after_date = dt.date().isoformat()

    rule = AutoEnrollRule(
        chat_id=callback.from_user.id,
        enabled=True,
        section_name=str(lesson.get("section_name") or ""),
        day_code=day_code,
        time_slot_start=str(lesson.get("time_slot_start") or ""),
        type_id=int(lesson.get("type_id") or 0),
        after_date=after_date,
    )
    bot_context.repository.upsert_auto_enroll_rule(rule)
    PENDING_BULK_ENROLL[callback.from_user.id] = rule

    if callback.message is not None:
        await callback.message.answer(
            "✅ Автозапись включена.\n"
            "Бот будет ждать, когда появится такое же занятие на будущие даты, и запишет тебя автоматически."
        )
        await callback.message.answer(
            "Записать тебя на все доступные сейчас занятия\n"
            "с таким же предметом, днём и временем?",
            reply_markup=auto_bulk_offer_keyboard(),
        )


@router.callback_query(lambda c: c.data == "auto_bulk_no")
async def auto_bulk_no_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await _cleanup_callback_message(callback)
    if callback.from_user is not None:
        PENDING_BULK_ENROLL.pop(callback.from_user.id, None)
    if callback.message is not None:
        await callback.message.answer("Ок, оставил только автозапись на будущее.")


@router.callback_query(lambda c: c.data == "auto_bulk_yes")
async def auto_bulk_yes_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await _cleanup_callback_message(callback)
    if callback.from_user is None:
        if callback.message is not None:
            await callback.message.answer("Ошибка: не удалось определить пользователя Telegram.")
        return

    rule = PENDING_BULK_ENROLL.pop(callback.from_user.id, None)
    if rule is None:
        if callback.message is not None:
            await callback.message.answer("Сначала подтверди автозапись для конкретного занятия.")
        return

    lessons, error = await _fetch_lessons_for_user(callback.from_user.id)
    if error:
        if callback.message is not None:
            await callback.message.answer(error)
        return

    # Match same subject/day/time/type and available slots for current visible schedule.
    candidates: list[dict] = []
    for lesson in lessons:
        if int(lesson.get("section_level") or 0) == 2:
            continue
        if str(lesson.get("section_name") or "") != rule.section_name:
            continue
        if str(lesson.get("time_slot_start") or "") != rule.time_slot_start:
            continue
        if int(lesson.get("type_id") or 0) != int(rule.type_id):
            continue
        raw_date = lesson.get("date")
        if not raw_date:
            continue
        try:
            lesson_date = datetime.fromisoformat(str(raw_date)).date()
        except ValueError:
            continue
        if lesson_date.weekday() != rule.day_code:
            continue
        if int(lesson.get("available") or 0) <= 0:
            continue
        candidates.append(lesson)

    if not candidates:
        if callback.message is not None:
            await callback.message.answer(
                "Сейчас нет доступных занятий под этот шаблон.",
                reply_markup=to_menu_keyboard(),
            )
        return

    if bot_context.repository is None:
        if callback.message is not None:
            await callback.message.answer("Ошибка: хранилище не инициализировано.")
        return
    tokens = bot_context.repository.get_tokens(callback.from_user.id)
    if tokens is None or not tokens.access_token:
        if callback.message is not None:
            await callback.message.answer("Сначала войди в ITMO.ID: /login <itmo_login> <itmo_password>")
        return

    client = MyItmoClient(tokens.access_token)
    success = 0
    failed = 0
    for lesson in candidates:
        lesson_id = int(lesson.get("id") or 0)
        if lesson_id <= 0:
            continue
        try:
            result = await client.sign_for_lesson(lesson_id)
            if result.get("ok"):
                success += 1
            else:
                failed += 1
        except Exception:
            failed += 1

    if callback.message is not None:
        await callback.message.answer(
            f"Готово. Попытался записать на текущие занятия: успешно {success}, ошибок {failed}."
        )


@router.callback_query(lambda c: c.data == "my_sport_open")
async def my_sport_open_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await _cleanup_callback_message(callback)
    if callback.from_user is None:
        if callback.message is not None:
            await callback.message.answer("Ошибка: не удалось определить пользователя Telegram.")
        return
    if bot_context.repository is None:
        if callback.message is not None:
            await callback.message.answer("Ошибка: хранилище не инициализировано.")
        return

    rules = bot_context.repository.list_user_auto_enroll_rules(callback.from_user.id)
    if not rules:
        if callback.message is not None:
            await callback.message.answer(
                "У тебя пока нет активных автозаписей.",
                reply_markup=to_menu_keyboard(),
            )
        return

    items = [(rule.id, f"🏅 {rule.section_name}") for rule in rules]

    if callback.message is not None:
        await callback.message.answer(
            "🏅 Мой спорт\nВыбери предмет, чтобы посмотреть автозапись:",
            reply_markup=my_sport_list_keyboard(items),
        )


@router.callback_query(lambda c: c.data is not None and c.data.startswith("my_sport_pick:"))
async def my_sport_pick_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await _cleanup_callback_message(callback)
    if callback.from_user is None:
        if callback.message is not None:
            await callback.message.answer("Ошибка: не удалось определить пользователя Telegram.")
        return
    if bot_context.repository is None:
        if callback.message is not None:
            await callback.message.answer("Ошибка: хранилище не инициализировано.")
        return

    try:
        rule_id = int(callback.data.split(":", maxsplit=1)[1])
    except Exception:
        if callback.message is not None:
            await callback.message.answer("Некорректный идентификатор автозаписи.")
        return

    rule = bot_context.repository.get_user_auto_enroll_rule(rule_id, callback.from_user.id)
    if rule is None:
        if callback.message is not None:
            await callback.message.answer("Автозапись не найдена или уже отключена.")
        return

    PENDING_CANCEL_ALL_RULE[callback.from_user.id] = rule.id
    if callback.message is not None:
        await callback.message.answer(
            _my_sport_detail_text(rule),
            reply_markup=my_sport_detail_keyboard(rule.id),
        )


@router.callback_query(lambda c: c.data is not None and c.data.startswith("my_sport_disable:"))
async def my_sport_disable_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await _cleanup_callback_message(callback)
    if callback.from_user is None:
        if callback.message is not None:
            await callback.message.answer("Ошибка: не удалось определить пользователя Telegram.")
        return
    if bot_context.repository is None:
        if callback.message is not None:
            await callback.message.answer("Ошибка: хранилище не инициализировано.")
        return

    try:
        rule_id = int(callback.data.split(":", maxsplit=1)[1])
    except Exception:
        if callback.message is not None:
            await callback.message.answer("Некорректный идентификатор автозаписи.")
        return

    bot_context.repository.disable_auto_enroll_rule(rule_id, callback.from_user.id)
    if callback.message is not None:
        await callback.message.answer("Автозапись отключена.")


@router.callback_query(lambda c: c.data is not None and c.data.startswith("my_sport_cancel_bookings:"))
async def my_sport_cancel_bookings_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await _cleanup_callback_message(callback)
    if callback.from_user is None:
        if callback.message is not None:
            await callback.message.answer("Ошибка: не удалось определить пользователя Telegram.")
        return
    if bot_context.repository is None:
        if callback.message is not None:
            await callback.message.answer("Ошибка: хранилище не инициализировано.")
        return

    try:
        rule_id = int(callback.data.split(":", maxsplit=1)[1])
    except Exception:
        if callback.message is not None:
            await callback.message.answer("Некорректный идентификатор автозаписи.")
        return

    rule = bot_context.repository.get_user_auto_enroll_rule(rule_id, callback.from_user.id)
    if rule is None:
        if callback.message is not None:
            await callback.message.answer("Автозапись не найдена или уже отключена.")
        return

    PENDING_CANCEL_ALL_RULE[callback.from_user.id] = rule_id
    if callback.message is not None:
        await callback.message.answer(
            _my_sport_detail_text(rule)
            + "\n\nТочно отменить все текущие записи по этому правилу?",
            reply_markup=my_sport_cancel_confirm_keyboard(),
        )
    return


@router.callback_query(lambda c: c.data == "my_sport_cancel_all_no")
async def my_sport_cancel_all_no_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await _cleanup_callback_message(callback)
    if callback.from_user is not None:
        PENDING_CANCEL_ALL_RULE.pop(callback.from_user.id, None)
    if callback.message is not None:
        await callback.message.answer("Ок, записи не трогал.")


@router.callback_query(lambda c: c.data == "my_sport_cancel_all_yes")
async def my_sport_cancel_all_yes_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await _cleanup_callback_message(callback)
    if callback.from_user is None:
        if callback.message is not None:
            await callback.message.answer("Ошибка: не удалось определить пользователя Telegram.")
        return
    if bot_context.repository is None:
        if callback.message is not None:
            await callback.message.answer("Ошибка: хранилище не инициализировано.")
        return

    rule_id = PENDING_CANCEL_ALL_RULE.pop(callback.from_user.id, None)
    if not rule_id:
        if callback.message is not None:
            await callback.message.answer("Сначала открой автозапись в меню 'Мой спорт'.")
        return

    rule = bot_context.repository.get_user_auto_enroll_rule(rule_id, callback.from_user.id)
    if rule is None:
        if callback.message is not None:
            await callback.message.answer("Автозапись не найдена или уже отключена.")
        return

    lessons, error = await _fetch_lessons_for_user(callback.from_user.id)
    if error:
        if callback.message is not None:
            await callback.message.answer(error)
        return

    cancel_ids: list[int] = []
    for lesson in lessons:
        if int(lesson.get("section_level") or 0) == 2:
            continue
        if str(lesson.get("section_name") or "") != rule.section_name:
            continue
        if str(lesson.get("time_slot_start") or "") != rule.time_slot_start:
            continue
        if int(lesson.get("type_id") or 0) != int(rule.type_id):
            continue
        raw_date = lesson.get("date")
        if not raw_date:
            continue
        try:
            lesson_date = datetime.fromisoformat(str(raw_date)).date()
        except ValueError:
            continue
        if lesson_date.weekday() != rule.day_code:
            continue
        if not bool(lesson.get("signed")):
            continue
        lesson_id = int(lesson.get("id") or 0)
        if lesson_id > 0:
            cancel_ids.append(lesson_id)

    if not cancel_ids:
        if callback.message is not None:
            await callback.message.answer(
                "Сейчас нет активных записей, подходящих под это правило.",
                reply_markup=to_menu_keyboard(),
            )
        return

    tokens = bot_context.repository.get_tokens(callback.from_user.id)
    if tokens is None or not tokens.access_token:
        if callback.message is not None:
            await callback.message.answer("Сначала войди в ITMO.ID: /login <itmo_login> <itmo_password>")
        return

    client = MyItmoClient(tokens.access_token)
    try:
        await client.sign_out_lessons(cancel_ids)
        if callback.message is not None:
            await callback.message.answer(f"Отменил записей: {len(cancel_ids)}.")
    except Exception as exc:
        if callback.message is not None:
            await callback.message.answer(f"Не удалось отменить записи: {exc}")


@router.message()
async def text_search_handler(message: Message) -> None:
    if message.from_user is None:
        return
    user_id = message.from_user.id

    if user_id in AUTH_WAITING_LOGIN:
        if not message.text:
            await message.answer("Отправь логин ITMO.ID текстом.")
            return
        AUTH_WAITING_LOGIN.discard(user_id)
        AUTH_WAITING_PASSWORD[user_id] = message.text.strip()
        await message.answer(
            "Теперь отправь пароль ITMO.ID.\n"
            "Мы не сохраняем пароль — он используется только для получения токенов."
        )
        return

    if user_id in AUTH_WAITING_PASSWORD:
        if not message.text:
            await message.answer("Отправь пароль ITMO.ID текстом.")
            return
        itmo_login = AUTH_WAITING_PASSWORD.pop(user_id)
        itmo_password = message.text.strip()
        await message.answer("Пробую войти в ITMO.ID, подожди...")
        ok, text = await _authenticate_and_save(
            telegram_id=user_id,
            telegram_tag=message.from_user.username or "",
            itmo_login=itmo_login,
            itmo_password=itmo_password,
        )
        await message.answer(text, reply_markup=main_menu_keyboard() if ok else None)
        return

    if user_id in PRIORITY_EDIT_WAITING:
        if bot_context.repository is None:
            await message.answer("Ошибка: хранилище не инициализировано.")
            return
        if not message.text:
            await message.answer("Отправь номера строками, например:\n2\n1\n3")
            return
        original_ids = PRIORITY_EDIT_WAITING[user_id]
        lines = [line.strip() for line in message.text.splitlines() if line.strip()]
        try:
            order = [int(item) for item in lines]
        except ValueError:
            await message.answer("Некорректный формат. Используй только номера по одному в строке.")
            return
        expected = set(range(1, len(original_ids) + 1))
        if len(order) != len(original_ids) or set(order) != expected:
            await message.answer(
                "Неверный порядок. Нужно использовать все номера ровно по одному разу.\n"
                f"Допустимые номера: 1..{len(original_ids)}"
            )
            return
        ordered_rule_ids = [original_ids[i - 1] for i in order]
        bot_context.repository.set_user_rule_priorities(user_id, ordered_rule_ids)
        PRIORITY_EDIT_WAITING.pop(user_id, None)
        await message.answer("Приоритеты обновлены.")
        return

    if message.from_user.id not in SEARCH_BY_NAME_WAITING:
        return
    if not message.text:
        await message.answer("Отправь текст с названием занятия, например: йога")
        return
    SEARCH_BY_NAME_WAITING.discard(message.from_user.id)
    query = message.text.strip()
    lessons, error = await _fetch_lessons_for_user(message.from_user.id)
    if error:
        await message.answer(error)
        return
    available_days = _available_days_for_query(lessons, query)
    if not available_days:
        subjects, catalog_error = await _fetch_subject_catalog_for_user(message.from_user.id)
        if catalog_error:
            await message.answer(catalog_error, reply_markup=to_menu_keyboard())
            return
        q = query.lower()
        matches = [name for name in subjects if q in name.lower()]
        if not matches:
            await message.answer(
                "По этому названию сейчас нет доступных занятий и похожих предметов в каталоге.",
                reply_markup=to_menu_keyboard(),
            )
            return
        PENDING_SUBJECT_MATCHES[message.from_user.id] = matches[:20]
        await message.answer(
            "Сейчас в доступном расписании нет подходящих слотов.\n"
            "Но предмет найден в общем списке. Выбери его, чтобы включить автозапись на будущее:",
            reply_markup=subject_catalog_keyboard(matches),
        )
        return
    SEARCH_DAY_QUERY[message.from_user.id] = query
    await message.answer(
        "Теперь выбери день недели (только доступные):",
        reply_markup=choose_day_keyboard(available_days=available_days),
    )


@router.callback_query(lambda c: c.data == "auth_login")
async def auth_login_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await _cleanup_callback_message(callback)
    if callback.from_user is not None:
        AUTH_WAITING_LOGIN.add(callback.from_user.id)
    if callback.message is not None:
        await callback.message.answer(
            "Введи логин ITMO.ID.\n"
            "Пароль мы не сохраняем — только получаем токены для работы бота."
        )


@router.callback_query(lambda c: c.data == "filters_open")
async def filters_open_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await _cleanup_callback_message(callback)
    if callback.message is not None:
        await callback.message.answer("Раздел фильтров скоро добавлю.")


@router.callback_query(lambda c: c.data == "settings_open")
async def settings_open_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await _cleanup_callback_message(callback)
    if callback.message is not None:
        await callback.message.answer(
            "Настройки my.itmo:",
            reply_markup=settings_keyboard(),
        )


@router.callback_query(lambda c: c.data == "settings_weekly_limit")
async def settings_weekly_limit_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await _cleanup_callback_message(callback)
    if callback.from_user is None:
        if callback.message is not None:
            await callback.message.answer("Ошибка: не удалось определить пользователя Telegram.")
        return
    if bot_context.repository is None:
        if callback.message is not None:
            await callback.message.answer("Ошибка: хранилище не инициализировано.")
        return
    current = bot_context.repository.get_weekly_limit(callback.from_user.id)
    if callback.message is not None:
        current_text = str(current) if current is not None else "не задан"
        await callback.message.answer(
            f"Текущий лимит записей в неделю: {current_text}\n"
            "Если лимит не задан, бот записывает без ограничений.\n"
            "Выбери новый лимит:",
            reply_markup=weekly_limit_keyboard(),
        )


@router.callback_query(lambda c: c.data is not None and c.data.startswith("settings_weekly_limit_set:"))
async def settings_weekly_limit_set_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await _cleanup_callback_message(callback)
    if callback.from_user is None:
        if callback.message is not None:
            await callback.message.answer("Ошибка: не удалось определить пользователя Telegram.")
        return
    if bot_context.repository is None:
        if callback.message is not None:
            await callback.message.answer("Ошибка: хранилище не инициализировано.")
        return
    try:
        limit = int(callback.data.split(":", maxsplit=1)[1])
    except Exception:
        if callback.message is not None:
            await callback.message.answer("Некорректный лимит.")
        return
    bot_context.repository.set_weekly_limit(callback.from_user.id, limit)
    if callback.message is not None:
        await callback.message.answer(f"Готово, лимит записей в неделю: {limit}.")


@router.callback_query(lambda c: c.data == "settings_priorities")
async def settings_priorities_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await _cleanup_callback_message(callback)
    if callback.from_user is None:
        if callback.message is not None:
            await callback.message.answer("Ошибка: не удалось определить пользователя Telegram.")
        return
    if bot_context.repository is None:
        if callback.message is not None:
            await callback.message.answer("Ошибка: хранилище не инициализировано.")
        return
    rules = bot_context.repository.list_user_auto_enroll_rules(callback.from_user.id)
    if not rules:
        if callback.message is not None:
            await callback.message.answer(
                "У тебя нет активных автозаписей для приоритизации.",
                reply_markup=to_menu_keyboard(),
            )
        return
    PRIORITY_EDIT_WAITING[callback.from_user.id] = [rule.id for rule in rules]
    lines = [f"{idx}. {rule.section_name} | {rule.time_slot_start}" for idx, rule in enumerate(rules, start=1)]
    if callback.message is not None:
        await callback.message.answer(
            "Текущий порядок приоритетов (сверху вниз):\n"
            + "\n".join(lines)
            + "\n\n"
            "Отправь новый порядок номерами, каждый номер с новой строки.\n"
            "Пример:\n2\n1\n3"
        )


@router.callback_query(lambda c: c.data == "settings_relink_itmo")
async def settings_relink_itmo_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await _cleanup_callback_message(callback)
    if callback.from_user is not None:
        AUTH_WAITING_LOGIN.add(callback.from_user.id)
        AUTH_WAITING_PASSWORD.pop(callback.from_user.id, None)
    if callback.message is not None:
        await callback.message.answer(
            "Ок, перепривязка my.itmo. Введи логин ITMO.ID.\n"
            "Пароль не сохраняется: он нужен только для получения токенов."
        )


@router.callback_query(lambda c: c.data == "settings_delete_itmo")
async def settings_delete_itmo_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await _cleanup_callback_message(callback)
    if callback.message is not None:
        await callback.message.answer(
            "Удалить логин и токены my.itmo из бота?",
            reply_markup=settings_delete_confirm_keyboard(),
        )


@router.callback_query(lambda c: c.data == "settings_delete_itmo_no")
async def settings_delete_itmo_no_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await _cleanup_callback_message(callback)
    if callback.message is not None:
        await callback.message.answer(
            "Ок, данные my.itmo оставил.",
            reply_markup=to_menu_keyboard(),
        )


@router.callback_query(lambda c: c.data == "settings_delete_itmo_yes")
async def settings_delete_itmo_yes_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await _cleanup_callback_message(callback)
    if callback.from_user is None:
        if callback.message is not None:
            await callback.message.answer("Ошибка: не удалось определить пользователя Telegram.")
        return
    if bot_context.repository is None:
        if callback.message is not None:
            await callback.message.answer("Ошибка: хранилище не инициализировано.")
        return

    bot_context.repository.clear_itmo_binding(callback.from_user.id)
    AUTH_WAITING_PASSWORD.pop(callback.from_user.id, None)
    AUTH_WAITING_LOGIN.add(callback.from_user.id)
    if callback.message is not None:
        await callback.message.answer(
            "Данные my.itmo удалены (логин и токены).\n"
            "Чтобы снова пользоваться ботом, введи логин ITMO.ID."
        )


@router.callback_query(lambda c: c.data == "help_open")
async def help_open_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await _cleanup_callback_message(callback)
    if callback.message is not None:
        await callback.message.answer(
            "Если появились вопросы, то обратитесь по кнопке ниже:",
            reply_markup=help_contact_keyboard(),
        )

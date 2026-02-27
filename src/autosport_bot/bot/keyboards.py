from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def _lesson_type_emoji(lesson: dict) -> str:
    can_sign = lesson.get("can_sign_in") or {}
    reasons = can_sign.get("unavailable_reasons") if isinstance(can_sign, dict) else None
    reasons_text = " ".join(reasons).lower() if isinstance(reasons, list) else ""
    type_id = lesson.get("type_id")
    section_level = lesson.get("section_level")

    # Задолженность
    if "задолж" in reasons_text or type_id == 5:
        return "🔴"

    # Секция (обычно нужен отбор)
    if section_level == 2 or "отбор" in reasons_text:
        return "🔵"

    # Открытые занятия
    if type_id == 1:
        return "🩵"

    # Свободные посещения
    if type_id == 2:
        return "🟢"

    return "⚪"


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏃 Выбрать физру", callback_data="choose_sport")],
            [InlineKeyboardButton(text="🏅 Мой спорт", callback_data="my_sport_open")],
            [InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings_open")],
            [InlineKeyboardButton(text="❓ Помощь", callback_data="help_open")],
        ]
    )


def to_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏠 В меню", callback_data="back_main")],
        ]
    )


def help_contact_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📩 Связаться", url="https://t.me/nitratex1")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")],
        ]
    )


def settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Перепривязать my.itmo", callback_data="settings_relink_itmo")],
            [InlineKeyboardButton(text="🎯 Приоритеты автозаписи", callback_data="settings_priorities")],
            [InlineKeyboardButton(text="📅 Лимит записей в неделю", callback_data="settings_weekly_limit")],
            [InlineKeyboardButton(text="🗑️ Удалить данные my.itmo", callback_data="settings_delete_itmo")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")],
        ]
    )


def settings_delete_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, удалить", callback_data="settings_delete_itmo_yes")],
            [InlineKeyboardButton(text="❌ Нет, отмена", callback_data="settings_delete_itmo_no")],
        ]
    )


def choose_mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Ввести название", callback_data="choose_input_name"),
                InlineKeyboardButton(text="Выбрать из списка", callback_data="choose_list"),
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")],
        ]
    )


def choose_day_keyboard(available_days: list[int] | None = None) -> InlineKeyboardMarkup:
    day_buttons = [
        ("Пн", "choose_day:0"),
        ("Вт", "choose_day:1"),
        ("Ср", "choose_day:2"),
        ("Чт", "choose_day:3"),
        ("Пт", "choose_day:4"),
        ("Сб", "choose_day:5"),
        ("Вс", "choose_day:6"),
    ]
    allowed = set(available_days) if available_days is not None else set(range(7))
    filtered = [btn for idx, btn in enumerate(day_buttons) if idx in allowed]

    rows: list[list[InlineKeyboardButton]] = []
    for idx in range(0, len(filtered), 2):
        left = InlineKeyboardButton(text=filtered[idx][0], callback_data=filtered[idx][1])
        row = [left]
        if idx + 1 < len(filtered):
            right = InlineKeyboardButton(text=filtered[idx + 1][0], callback_data=filtered[idx + 1][1])
            row.append(right)
        rows.append(row)
    rows.append([InlineKeyboardButton(text="Любой", callback_data="choose_day:any")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="choose_sport")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def choose_time_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for hour in range(8, 22):
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"С {hour:02d}:00 до {hour + 1:02d}:00",
                    callback_data=f"choose_time:h{hour:02d}",
                )
            ]
        )
    rows.extend(
        [
            [InlineKeyboardButton(text="Любое время", callback_data="choose_time:any")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="choose_sport")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def auto_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить автозапись", callback_data="auto_confirm"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="auto_cancel"),
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="choose_sport")],
        ]
    )


def auto_bulk_offer_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, записать на все доступные",
                    callback_data="auto_bulk_yes",
                )
            ],
            [
                InlineKeyboardButton(
                    text="➡️ Нет, только автозапись на будущее",
                    callback_data="auto_bulk_no",
                )
            ],
        ]
    )


def my_sport_cancel_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, отменить все",
                    callback_data="my_sport_cancel_all_yes",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Нет, оставить",
                    callback_data="my_sport_cancel_all_no",
                )
            ],
        ]
    )


def back_to_choose_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="choose_sport")],
        ]
    )


def my_sport_list_keyboard(items: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for rule_id, title in items:
        rows.append(
            [
                InlineKeyboardButton(
                    text=title[:64],
                    callback_data=f"my_sport_pick:{rule_id}",
                )
            ]
        )
    if not rows:
        rows = [[InlineKeyboardButton(text="Пусто", callback_data="noop")]]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def my_sport_detail_keyboard(rule_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑️ Отменить все записи",
                    callback_data=f"my_sport_cancel_bookings:{rule_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отключить автозапись",
                    callback_data=f"my_sport_disable:{rule_id}",
                )
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="my_sport_open")],
        ]
    )


def weekly_limit_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="1", callback_data="settings_weekly_limit_set:1"),
                InlineKeyboardButton(text="2", callback_data="settings_weekly_limit_set:2"),
                InlineKeyboardButton(text="3", callback_data="settings_weekly_limit_set:3"),
            ],
            [
                InlineKeyboardButton(text="4", callback_data="settings_weekly_limit_set:4"),
                InlineKeyboardButton(text="5", callback_data="settings_weekly_limit_set:5"),
                InlineKeyboardButton(text="6", callback_data="settings_weekly_limit_set:6"),
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_open")],
        ]
    )


def sport_lessons_keyboard(lessons: list[dict], show_time: bool = False) -> InlineKeyboardMarkup:
    sorted_lessons = sorted(
        lessons,
        key=lambda it: (str(it.get("time_slot_start") or "99:99"), str(it.get("section_name") or "")),
    )
    buttons: list[InlineKeyboardButton] = []
    for lesson in sorted_lessons[:20]:
        section = str(lesson.get("section_name") or "Без названия")
        time_start = str(lesson.get("time_slot_start") or "--:--")
        lesson_id = lesson.get("id")
        if lesson_id is None:
            continue
        if int(lesson.get("section_level") or 0) == 2:
            continue
        emoji = _lesson_type_emoji(lesson)
        label = f"{emoji} {section} | {time_start}" if show_time else f"{emoji} {section}"
        buttons.append(InlineKeyboardButton(text=label[:64], callback_data=f"sport_pick:{lesson_id}"))

    rows: list[list[InlineKeyboardButton]] = [[button] for button in buttons]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="choose_sport")])

    return InlineKeyboardMarkup(
        inline_keyboard=rows or [[InlineKeyboardButton(text="Нет данных", callback_data="noop")]]
    )

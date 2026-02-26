# AutoSport Bot

Telegram-бот для отслеживания свободных мест и автозаписи на занятия в `my.itmo.ru`.

## План по этапам

1. Каркас проекта и запуск Telegram-бота.
2. Реализация авторизации в `my.itmo` (PKCE + refresh token).
3. Получение расписания и фильтрация занятий.
4. Автозапись и уведомления в Telegram.
5. Хранение пользовательских настроек и тесты.

## Быстрый старт

1. Создай окружение:
   - `python3 -m venv .venv`
   - `source .venv/bin/activate`
2. Установи зависимости:
   - `pip install -e .`
3. Заполни `.env` на основе `.env.example`.
4. Запусти:
   - `python -m autosport_bot`

## Что уже сохраняется в БД

Используется SQLite (`DATABASE_PATH`), таблица `users`.

Сохраняемые поля:
- `telegram_id`
- `telegram_tag`
- `itmo_login`
- `access_token`
- `refresh_token`

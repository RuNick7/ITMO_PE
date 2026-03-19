from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher

from autosport_bot.bot import context as bot_context
from autosport_bot.bot.handlers.start import router as start_router
from autosport_bot.core.config import get_settings
from autosport_bot.core.logging import setup_logging
from autosport_bot.remnawave.client import RemnawaveClient
from autosport_bot.scheduler.auto_enroll_worker import AutoEnrollWorker
from autosport_bot.storage.repository import SQLiteRepository


async def _start() -> None:
    setup_logging()
    settings = get_settings()
    bot_context.repository = SQLiteRepository(settings.database_path)

    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher()
    dp.include_router(start_router)
    remnawave_client = RemnawaveClient(settings)
    worker = AutoEnrollWorker(
        repository=bot_context.repository,
        poll_interval_seconds=max(15, settings.poll_interval_seconds),
        remnawave_client=remnawave_client,
    )
    asyncio.create_task(worker.run_forever(bot))

    logging.getLogger(__name__).info("Bot started")
    await dp.start_polling(bot)


def run() -> None:
    asyncio.run(_start())

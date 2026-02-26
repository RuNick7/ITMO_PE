from __future__ import annotations

import asyncio
import logging

from autosport_bot.core.config import Settings

logger = logging.getLogger(__name__)


class EnrollmentPoller:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._running = False

    async def run_forever(self) -> None:
        self._running = True
        logger.info("Enrollment poller started")
        while self._running:
            # TODO: проверка мест + автозапись
            await asyncio.sleep(self._settings.poll_interval_seconds)

    def stop(self) -> None:
        self._running = False

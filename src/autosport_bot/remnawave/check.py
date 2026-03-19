from __future__ import annotations

import asyncio
import json

from autosport_bot.core.config import get_settings
from autosport_bot.remnawave.client import RemnawaveClient


async def _run() -> None:
    settings = get_settings()
    client = RemnawaveClient(settings)
    if not client.is_configured:
        raise RuntimeError(
            "Remnawave is not configured. Set REMNAWAVE_BASE_URL and REMNAWAVE_ACCESS_TOKEN."
        )
    users = await client.get_all_users()
    print(f"Total users from Remnawave: {len(users)}")
    preview = users[:5]
    print(json.dumps(preview, ensure_ascii=False, indent=2))


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()

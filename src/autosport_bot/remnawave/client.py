from __future__ import annotations

from typing import Any

import httpx

from autosport_bot.core.config import Settings

class RemnawaveClient:
    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.remnawave_base_url.rstrip("/")
        self._access_token = settings.remnawave_access_token.strip()

    @property
    def is_configured(self) -> bool:
        return bool(self._base_url and self._access_token)

    async def get_all_users(self, page_size: int = 500) -> list[dict[str, Any]]:
        if not self.is_configured:
            return []
        users: list[dict[str, Any]] = []
        start = 0
        while True:
            payload = await self._request(
                method="GET",
                path="/api/users",
                params={"size": page_size, "start": start},
            )
            response = payload.get("response") or {}
            page = response.get("users") or []
            if not isinstance(page, list):
                break
            users.extend([item for item in page if isinstance(item, dict)])
            if len(page) < page_size:
                break
            start += page_size
        return users

    async def has_user_by_telegram_id(self, telegram_id: int) -> bool:
        if not self.is_configured:
            return False
        payload = await self._request(
            method="GET",
            path=f"/api/users/by-telegram-id/{telegram_id}",
        )
        response = payload.get("response")
        if isinstance(response, list):
            return len(response) > 0
        if isinstance(response, dict):
            return bool(response)
        return False

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.is_configured:
            raise RuntimeError("Remnawave is not configured.")
        headers = {"Authorization": f"Bearer {self._access_token}"}
        url = f"{self._base_url}{path}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                json=json_body,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                return {}
            return payload

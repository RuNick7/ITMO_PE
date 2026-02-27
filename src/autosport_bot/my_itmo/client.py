from __future__ import annotations

from typing import Any

import httpx


class MyItmoClient:
    base_url = "https://my.itmo.ru/api"

    def __init__(self, access_token: str):
        self._headers = {"Authorization": f"Bearer {access_token}"}

    async def get_sport_schedule(
        self,
        date_start: str,
        date_end: str,
        building_id: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"date_start": date_start, "date_end": date_end}
        if building_id is not None:
            params["building_id"] = building_id
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{self.base_url}/sport/sign/schedule",
                params=params,
                headers=self._headers,
            )
            response.raise_for_status()
            return response.json()

    async def sign_for_lessons(self, lesson_ids: list[int]) -> dict[str, Any]:
        if not lesson_ids:
            return {"ok": True, "already_signed": False, "payload": {}}
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{self.base_url}/sport/sign/schedule/lessons",
                json=lesson_ids,
                headers=self._headers,
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError:
                # my.itmo may return 400 even when enrollment already exists.
                if response.status_code == 400:
                    try:
                        payload = response.json()
                    except Exception:
                        payload = {}
                    message = str(payload.get("error_message") or payload.get("message") or "")
                    low = message.lower()
                    if "уже запис" in low or "already" in low:
                        return {"ok": True, "already_signed": True, "message": message}
                raise
            try:
                payload = response.json()
            except Exception:
                payload = {}
            return {"ok": True, "already_signed": False, "payload": payload}

    async def sign_for_lesson(self, lesson_id: int) -> dict[str, Any]:
        return await self.sign_for_lessons([lesson_id])

    async def sign_out_lessons(self, lesson_ids: list[int]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.request(
                method="DELETE",
                url=f"{self.base_url}/sport/sign/schedule/lessons",
                json=lesson_ids,
                headers=self._headers,
            )
            response.raise_for_status()
            try:
                payload = response.json()
            except Exception:
                payload = {}
            return {"ok": True, "payload": payload}

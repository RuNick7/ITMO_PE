from __future__ import annotations

import html
import os
import re
import urllib.parse
from base64 import urlsafe_b64encode
from dataclasses import dataclass
from hashlib import sha256
from time import time

import httpx

from autosport_bot.core.config import Settings


@dataclass
class TokenPair:
    access_token: str
    refresh_token: str
    access_expires_at: int
    refresh_expires_at: int


class ItmoAuthService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._provider = f"https://id.itmo.ru/auth/realms/{settings.itmo_realm}"

    @staticmethod
    def _generate_code_verifier() -> str:
        code_verifier = urlsafe_b64encode(os.urandom(40)).decode("utf-8")
        return re.sub(r"[^a-zA-Z0-9]+", "", code_verifier)

    @staticmethod
    def _code_challenge(code_verifier: str) -> str:
        challenge = urlsafe_b64encode(sha256(code_verifier.encode("utf-8")).digest()).decode("utf-8")
        return challenge.replace("=", "")

    async def login(self, username: str, password: str) -> TokenPair:
        code_verifier = self._generate_code_verifier()
        code_challenge = self._code_challenge(code_verifier)
        auth_url = f"{self._provider}/protocol/openid-connect/auth"
        token_url = f"{self._provider}/protocol/openid-connect/token"
        now = int(time())

        async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
            auth_resp = await client.get(
                auth_url,
                params={
                    "protocol": "oauth2",
                    "response_type": "code",
                    "client_id": self._settings.itmo_client_id,
                    "redirect_uri": self._settings.itmo_redirect_uri,
                    "scope": "openid profile",
                    "state": "autosport_bot",
                    "code_challenge_method": "S256",
                    "code_challenge": code_challenge,
                },
            )
            auth_resp.raise_for_status()

            form_regex = re.compile(r'"loginAction":\s*"(?P<action>[^"]+)"', re.DOTALL)
            match = form_regex.search(auth_resp.text)
            if not match:
                raise ValueError("Не удалось получить loginAction из страницы авторизации.")

            login_action = html.unescape(match.group("action"))
            form_resp = await client.post(
                login_action,
                data={"username": username, "password": password, "rememberMe": "on"},
                cookies=auth_resp.cookies,
            )
            if form_resp.status_code not in (301, 302, 303):
                raise ValueError("Неверный логин или пароль ITMO.ID.")

            location = form_resp.headers.get("Location", "")
            code = urllib.parse.parse_qs(urllib.parse.urlparse(location).query).get("code", [None])[0]
            if not code:
                raise ValueError("Сервер не вернул auth code.")

            token_resp = await client.post(
                token_url,
                data={
                    "grant_type": "authorization_code",
                    "client_id": self._settings.itmo_client_id,
                    "redirect_uri": self._settings.itmo_redirect_uri,
                    "code": code,
                    "code_verifier": code_verifier,
                },
            )
            token_resp.raise_for_status()
            token_payload = token_resp.json()

        return TokenPair(
            access_token=token_payload["access_token"],
            refresh_token=token_payload.get("refresh_token", ""),
            access_expires_at=now + int(token_payload.get("expires_in", 0)),
            refresh_expires_at=now + int(token_payload.get("refresh_expires_in", 0)),
        )

    async def refresh(self, refresh_token: str) -> TokenPair:
        token_url = f"{self._provider}/protocol/openid-connect/token"
        now = int(time())

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                token_url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": self._settings.itmo_client_id,
                },
            )
            response.raise_for_status()
            payload = response.json()

        return TokenPair(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token", refresh_token),
            access_expires_at=now + int(payload.get("expires_in", 0)),
            refresh_expires_at=now + int(payload.get("refresh_expires_in", 0)),
        )

from typing import Protocol

import httpx
from eastmed_shared import Settings, get_settings


class DeskNotifier(Protocol):
    def notify(self, message: str) -> str | None: ...


class NullDeskNotifier:
    def notify(self, message: str) -> str | None:
        del message
        return None


class TelegramDeskNotifier:
    def __init__(self, *, token: str, chat_id: str, timeout_seconds: float = 10.0) -> None:
        self._token = token
        self._chat_id = chat_id
        self._timeout_seconds = timeout_seconds

    def notify(self, message: str) -> str | None:
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        response = httpx.post(
            url,
            json={
                "chat_id": self._chat_id,
                "text": message,
                "disable_web_page_preview": True,
            },
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        result = payload.get("result", {})
        message_id = result.get("message_id")
        return str(message_id) if message_id is not None else None


def build_desk_notifier(settings: Settings | None = None) -> DeskNotifier:
    active = settings or get_settings()
    if active.outbound_enabled and active.telegram_desk_bot_token and active.telegram_desk_chat_id:
        return TelegramDeskNotifier(
            token=active.telegram_desk_bot_token.get_secret_value(),
            chat_id=active.telegram_desk_chat_id,
        )
    return NullDeskNotifier()

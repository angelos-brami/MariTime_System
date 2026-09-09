import httpx
import respx
from eastmed_pipeline.notifications import (
    NullDeskNotifier,
    TelegramDeskNotifier,
    build_desk_notifier,
)
from eastmed_shared import Settings
from pydantic import SecretStr


def telegram_settings(*, outbound_enabled: bool) -> Settings:
    return Settings(
        outbound_enabled=outbound_enabled,
        telegram_desk_bot_token=SecretStr("token"),
        telegram_desk_chat_id="desk",
    )


def test_kill_switch_prevents_telegram_notifier_creation() -> None:
    assert isinstance(
        build_desk_notifier(telegram_settings(outbound_enabled=False)), NullDeskNotifier
    )


@respx.mock
def test_telegram_notifier_returns_provider_message_id() -> None:
    route = respx.post("https://api.telegram.org/bottoken/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})
    )
    notifier = TelegramDeskNotifier(token="token", chat_id="desk")  # noqa: S106
    assert notifier.notify("poller down") == "42"
    assert route.called

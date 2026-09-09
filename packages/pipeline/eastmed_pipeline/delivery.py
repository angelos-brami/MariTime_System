from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

import httpx
from eastmed_schema.enums import DeliveryChannel, DeliveryStatus
from eastmed_schema.models import Alert, CorrectionDelivery, Delivery
from eastmed_shared import Settings, get_settings
from sqlalchemy import or_, select
from sqlalchemy.orm import Session


class DeliveryProviderError(RuntimeError):
    pass


class EmailProvider(Protocol):
    def send(self, *, recipient: str, subject: str, text: str, html_body: str) -> str: ...


class TelegramProvider(Protocol):
    def send(self, *, chat_id: str, text: str) -> str: ...


class WhatsAppProvider(Protocol):
    def send_template(
        self, *, recipient: str, template_name: str, parameters: list[str]
    ) -> str: ...


class PostmarkEmailProvider:
    def __init__(
        self,
        *,
        token: str,
        sender: str,
        message_stream: str,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._token = token
        self._sender = sender
        self._message_stream = message_stream
        self._timeout_seconds = timeout_seconds

    def send(self, *, recipient: str, subject: str, text: str, html_body: str) -> str:
        response = httpx.post(
            "https://api.postmarkapp.com/email",
            headers={
                "accept": "application/json",
                "content-type": "application/json",
                "x-postmark-server-token": self._token,
            },
            json={
                "From": self._sender,
                "To": recipient,
                "Subject": subject,
                "TextBody": text,
                "HtmlBody": html_body,
                "MessageStream": self._message_stream,
                "TrackOpens": False,
                "TrackLinks": "None",
            },
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        message_id = response.json().get("MessageID")
        if not message_id:
            raise DeliveryProviderError("Postmark response did not contain MessageID")
        return str(message_id)


class TelegramCustomerProvider:
    def __init__(self, *, token: str, timeout_seconds: float = 10.0) -> None:
        self._token = token
        self._timeout_seconds = timeout_seconds

    def send(self, *, chat_id: str, text: str) -> str:
        response = httpx.post(
            f"https://api.telegram.org/bot{self._token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        message_id = response.json().get("result", {}).get("message_id")
        if message_id is None:
            raise DeliveryProviderError("Telegram response did not contain message_id")
        return str(message_id)


class Dialog360WhatsAppProvider:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        language: str,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._language = language
        self._timeout_seconds = timeout_seconds

    def send_template(self, *, recipient: str, template_name: str, parameters: list[str]) -> str:
        response = httpx.post(
            f"{self._base_url}/messages",
            headers={
                "D360-API-KEY": self._api_key,
                "content-type": "application/json",
            },
            json={
                "messaging_product": "whatsapp",
                "to": recipient,
                "type": "template",
                "template": {
                    "name": template_name,
                    "language": {
                        "policy": "deterministic",
                        "code": self._language,
                    },
                    "components": [
                        {
                            "type": "body",
                            "parameters": [{"type": "text", "text": value} for value in parameters],
                        }
                    ],
                },
            },
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        messages = response.json().get("messages")
        message_id = messages[0].get("id") if isinstance(messages, list) and messages else None
        if not message_id:
            raise DeliveryProviderError("360dialog response did not contain a WhatsApp message ID")
        return str(message_id)


@dataclass(frozen=True)
class CustomerProviders:
    email: EmailProvider | None
    telegram: TelegramProvider | None
    whatsapp: WhatsAppProvider | None = None


@dataclass(frozen=True)
class RenderedAlert:
    subject: str
    text: str
    html: str
    telegram_text: str


TELEGRAM_MESSAGE_LIMIT = 4096


def _telegram_message(text: str, *, event_url: str) -> str:
    if len(text) <= TELEGRAM_MESSAGE_LIMIT:
        return text
    suffix = f"\n\n[Message shortened. Open the full event record.]\n{event_url}"
    available = TELEGRAM_MESSAGE_LIMIT - len(suffix)
    shortened = text[:available].rstrip()
    if "\n" in shortened:
        shortened = shortened.rsplit("\n", 1)[0].rstrip()
    return f"{shortened}{suffix}"[:TELEGRAM_MESSAGE_LIMIT]


def build_customer_providers(settings: Settings | None = None) -> CustomerProviders:
    active = settings or get_settings()
    email: EmailProvider | None = None
    telegram: TelegramProvider | None = None
    whatsapp: WhatsAppProvider | None = None
    if active.postmark_server_token and active.postmark_from_email:
        email = PostmarkEmailProvider(
            token=active.postmark_server_token.get_secret_value(),
            sender=active.postmark_from_email,
            message_stream=active.postmark_message_stream,
        )
    if active.telegram_customer_bot_token:
        telegram = TelegramCustomerProvider(
            token=active.telegram_customer_bot_token.get_secret_value()
        )
    if active.whatsapp_360dialog_api_key:
        whatsapp = Dialog360WhatsAppProvider(
            api_key=active.whatsapp_360dialog_api_key.get_secret_value(),
            base_url=active.whatsapp_360dialog_base_url,
            language=active.whatsapp_template_language,
        )
    return CustomerProviders(email=email, telegram=telegram, whatsapp=whatsapp)


def _template_value(value: object, *, limit: int = 700) -> str:
    normalized = " ".join(str(value or "None published").split())
    return normalized if len(normalized) <= limit else f"{normalized[: limit - 1].rstrip()}…"


def _alert_whatsapp_parameters(alert: Alert, *, public_base_url: str) -> list[str]:
    message = alert.message_json
    event_url = f"{public_base_url.rstrip('/')}/portal/events/{message['event_slug']}"
    return [
        _template_value(message.get("severity"), limit=8),
        _template_value(message.get("title"), limit=200),
        _template_value(message.get("confirmed")),
        _template_value(message.get("reported")),
        _template_value(message.get("unknown")),
        _template_value(message.get("whats_changed")),
        _template_value(event_url, limit=500),
    ]


def render_alert(alert: Alert, *, public_base_url: str) -> RenderedAlert:
    message = alert.message_json
    severity = int(message["severity"])
    title = str(message["title"])
    subject = f"[S{severity}] East Med alert — {title}"
    sections = [
        ("CONFIRMED", str(message.get("confirmed") or "")),
        ("REPORTED", str(message.get("reported") or "")),
        ("UNKNOWN", str(message.get("unknown") or "")),
        ("WHAT CHANGED", str(message.get("whats_changed") or "")),
    ]
    body_lines = [f"EAST MED DESK · SEVERITY {severity}", title]
    html_sections: list[str] = []
    for label, value in sections:
        if not value:
            continue
        body_lines.extend(["", label, value])
        html_sections.append(
            f"<h2>{html.escape(label)}</h2><p>{html.escape(value).replace(chr(10), '<br>')}</p>"
        )
    if note := message.get("note"):
        body_lines.extend(["", "ANALYST NOTE", str(note)])
        html_sections.append(f"<h2>Analyst note</h2><p>{html.escape(str(note))}</p>")
    event_url = f"{public_base_url.rstrip('/')}/portal/events/{message['event_slug']}"
    body_lines.extend(["", f"Event record: {event_url}"])
    html_body = (
        '<main style="font-family:Arial,sans-serif;max-width:680px">'
        f"<p>East Med Desk · Severity {severity}</p><h1>{html.escape(title)}</h1>"
        f"{''.join(html_sections)}"
        f'<p><a href="{html.escape(event_url)}">Open event record</a></p></main>'
    )
    text = "\n".join(body_lines)
    return RenderedAlert(
        subject=subject,
        text=text,
        html=html_body,
        telegram_text=_telegram_message(text, event_url=event_url),
    )


def deliver_one(
    session: Session,
    *,
    delivery_id: UUID,
    providers: CustomerProviders,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> bool:
    active = settings or get_settings()
    timestamp = now or datetime.now(UTC)
    if not active.outbound_enabled:
        return False
    delivery = session.scalar(select(Delivery).where(Delivery.id == delivery_id).with_for_update())
    if delivery is None:
        raise LookupError("Delivery not found")
    if delivery.status != DeliveryStatus.QUEUED:
        session.rollback()
        return False
    alert = session.get(Alert, delivery.alert_id)
    if alert is None:
        raise LookupError("Delivery references a missing alert")

    rendered = render_alert(alert, public_base_url=active.public_base_url)
    delivery.attempt_count += 1
    delivery.last_attempt_at = timestamp
    try:
        if delivery.channel == DeliveryChannel.EMAIL:
            if providers.email is None:
                raise DeliveryProviderError("Postmark outbound provider is not configured")
            provider_ref = providers.email.send(
                recipient=delivery.recipient,
                subject=rendered.subject,
                text=rendered.text,
                html_body=rendered.html,
            )
        elif delivery.channel == DeliveryChannel.TELEGRAM:
            if providers.telegram is None:
                raise DeliveryProviderError("Telegram outbound provider is not configured")
            provider_ref = providers.telegram.send(
                chat_id=delivery.recipient,
                text=rendered.telegram_text,
            )
        elif delivery.channel == DeliveryChannel.WHATSAPP:
            if providers.whatsapp is None:
                raise DeliveryProviderError("360dialog WhatsApp provider is not configured")
            provider_ref = providers.whatsapp.send_template(
                recipient=delivery.recipient,
                template_name=active.whatsapp_alert_template,
                parameters=_alert_whatsapp_parameters(
                    alert,
                    public_base_url=active.public_base_url,
                ),
            )
        else:
            raise DeliveryProviderError(f"Unsupported delivery channel {delivery.channel.value}")
        delivery.sent_at = timestamp
        if delivery.channel == DeliveryChannel.WHATSAPP:
            delivery.status = DeliveryStatus.SENT
            delivery.delivered_at = None
        else:
            delivery.status = DeliveryStatus.DELIVERED
            delivery.delivered_at = timestamp
        delivery.provider_ref = provider_ref
        delivery.last_error = None
        delivery.next_attempt_at = None
    except Exception as exc:  # delivery jobs must persist retry state for provider failures
        delivery.last_error = f"{type(exc).__name__}: {str(exc)[:1000]}"
        if delivery.attempt_count >= active.delivery_max_attempts:
            delivery.status = DeliveryStatus.FAILED
            delivery.next_attempt_at = None
        else:
            delivery.status = DeliveryStatus.QUEUED
            delay = min(60 * (2 ** (delivery.attempt_count - 1)), 3600)
            delivery.next_attempt_at = timestamp + timedelta(seconds=delay)
    session.commit()
    return delivery.status in {DeliveryStatus.SENT, DeliveryStatus.DELIVERED}


def dispatch_pending_deliveries(
    session: Session,
    *,
    providers: CustomerProviders | None = None,
    settings: Settings | None = None,
    limit: int = 100,
    now: datetime | None = None,
) -> int:
    active = settings or get_settings()
    if not active.outbound_enabled:
        return 0
    timestamp = now or datetime.now(UTC)
    delivery_ids = list(
        session.scalars(
            select(Delivery.id)
            .where(
                Delivery.status == DeliveryStatus.QUEUED,
                or_(
                    Delivery.next_attempt_at.is_(None),
                    Delivery.next_attempt_at <= timestamp,
                ),
            )
            .order_by(Delivery.queued_at)
            .limit(limit)
        ).all()
    )
    active_providers = providers or build_customer_providers(active)
    delivered = 0
    for delivery_id in delivery_ids:
        if deliver_one(
            session,
            delivery_id=delivery_id,
            providers=active_providers,
            settings=active,
            now=timestamp,
        ):
            delivered += 1
    return delivered


def _render_correction_message(
    message: dict[str, object], *, public_base_url: str
) -> RenderedAlert:
    label = str(message["label"])
    title = str(message["title"])
    note = str(message["note"])
    corrected = message["corrected_version"]
    affected = message["affected_version"]
    if not isinstance(corrected, dict) or not isinstance(affected, dict):
        raise DeliveryProviderError("Correction message snapshot is malformed")
    event_url = f"{public_base_url.rstrip('/')}/portal/events/{message['event_slug']}"
    subject = f"[{label}] East Med - {title}"
    text = (
        f"EAST MED DESK - {label}\n{title}\n\n{note}\n\n"
        f"Affected version: v{affected.get('number')} / {affected.get('content_hash')}\n"
        f"Corrected version: v{corrected.get('number')} / {corrected.get('content_hash')}\n\n"
        f"Event record: {event_url}"
    )
    html_body = (
        '<main style="font-family:Arial,sans-serif;max-width:680px">'
        f"<p><strong>{html.escape(label)}</strong></p><h1>{html.escape(title)}</h1>"
        f"<p>{html.escape(note)}</p>"
        f"<p>Affected version: v{html.escape(str(affected.get('number')))}<br>"
        f"Corrected version: v{html.escape(str(corrected.get('number')))}</p>"
        f'<p><a href="{html.escape(event_url)}">Open corrected event record</a></p></main>'
    )
    return RenderedAlert(
        subject=subject,
        text=text,
        html=html_body,
        telegram_text=_telegram_message(text, event_url=event_url),
    )


def _correction_whatsapp_parameters(
    message: dict[str, object], *, public_base_url: str
) -> list[str]:
    affected = message.get("affected_version")
    corrected = message.get("corrected_version")
    if not isinstance(affected, dict) or not isinstance(corrected, dict):
        raise DeliveryProviderError("Correction message snapshot is malformed")
    event_url = f"{public_base_url.rstrip('/')}/portal/events/{message['event_slug']}"
    return [
        _template_value(message.get("label"), limit=32),
        _template_value(message.get("title"), limit=200),
        _template_value(message.get("note")),
        _template_value(affected.get("number"), limit=16),
        _template_value(corrected.get("number"), limit=16),
        _template_value(event_url, limit=500),
    ]


def deliver_one_correction(
    session: Session,
    *,
    delivery_id: UUID,
    providers: CustomerProviders,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> bool:
    active = settings or get_settings()
    timestamp = now or datetime.now(UTC)
    if not active.outbound_enabled:
        return False
    delivery = session.scalar(
        select(CorrectionDelivery).where(CorrectionDelivery.id == delivery_id).with_for_update()
    )
    if delivery is None:
        raise LookupError("Correction delivery not found")
    if delivery.status != DeliveryStatus.QUEUED:
        session.rollback()
        return False
    rendered = _render_correction_message(
        delivery.message_snapshot_json,
        public_base_url=active.public_base_url,
    )
    delivery.attempt_count += 1
    delivery.last_attempt_at = timestamp
    try:
        if delivery.channel == DeliveryChannel.EMAIL:
            if providers.email is None:
                raise DeliveryProviderError("Postmark outbound provider is not configured")
            provider_ref = providers.email.send(
                recipient=delivery.recipient,
                subject=rendered.subject,
                text=rendered.text,
                html_body=rendered.html,
            )
        elif delivery.channel == DeliveryChannel.TELEGRAM:
            if providers.telegram is None:
                raise DeliveryProviderError("Telegram outbound provider is not configured")
            provider_ref = providers.telegram.send(
                chat_id=delivery.recipient,
                text=rendered.telegram_text,
            )
        elif delivery.channel == DeliveryChannel.WHATSAPP:
            if providers.whatsapp is None:
                raise DeliveryProviderError("360dialog WhatsApp provider is not configured")
            provider_ref = providers.whatsapp.send_template(
                recipient=delivery.recipient,
                template_name=active.whatsapp_correction_template,
                parameters=_correction_whatsapp_parameters(
                    delivery.message_snapshot_json,
                    public_base_url=active.public_base_url,
                ),
            )
        else:
            raise DeliveryProviderError(f"Unsupported correction channel {delivery.channel.value}")
        delivery.sent_at = timestamp
        if delivery.channel == DeliveryChannel.WHATSAPP:
            delivery.status = DeliveryStatus.SENT
            delivery.delivered_at = None
        else:
            delivery.status = DeliveryStatus.DELIVERED
            delivery.delivered_at = timestamp
        delivery.provider_ref = provider_ref
        delivery.last_error = None
        delivery.next_attempt_at = None
    except Exception as exc:
        delivery.last_error = f"{type(exc).__name__}: {str(exc)[:1000]}"
        if delivery.attempt_count >= active.delivery_max_attempts:
            delivery.status = DeliveryStatus.FAILED
            delivery.next_attempt_at = None
        else:
            delivery.status = DeliveryStatus.QUEUED
            delay = min(60 * (2 ** (delivery.attempt_count - 1)), 3600)
            delivery.next_attempt_at = timestamp + timedelta(seconds=delay)
    session.commit()
    return delivery.status in {DeliveryStatus.SENT, DeliveryStatus.DELIVERED}


def dispatch_pending_corrections(
    session: Session,
    *,
    providers: CustomerProviders | None = None,
    settings: Settings | None = None,
    limit: int = 100,
    now: datetime | None = None,
) -> int:
    active = settings or get_settings()
    if not active.outbound_enabled:
        return 0
    timestamp = now or datetime.now(UTC)
    ids = list(
        session.scalars(
            select(CorrectionDelivery.id)
            .where(
                CorrectionDelivery.status == DeliveryStatus.QUEUED,
                or_(
                    CorrectionDelivery.next_attempt_at.is_(None),
                    CorrectionDelivery.next_attempt_at <= timestamp,
                ),
            )
            .order_by(CorrectionDelivery.queued_at)
            .limit(limit)
        ).all()
    )
    active_providers = providers or build_customer_providers(active)
    return sum(
        deliver_one_correction(
            session,
            delivery_id=delivery_id,
            providers=active_providers,
            settings=active,
            now=timestamp,
        )
        for delivery_id in ids
    )

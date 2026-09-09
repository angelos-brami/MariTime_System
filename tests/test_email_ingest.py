import base64
import io
from datetime import UTC, datetime

import pytest
from eastmed_api.contracts import PostmarkInboundMessage
from eastmed_api.security import require_postmark_basic
from eastmed_pipeline.email_ingest import _message_datetime, _message_text
from eastmed_shared import Settings
from fastapi import HTTPException
from fastapi.security import HTTPBasicCredentials
from pydantic import SecretStr, ValidationError
from reportlab.pdfgen.canvas import Canvas


def inbound_payload(**overrides: object) -> PostmarkInboundMessage:
    values: dict[str, object] = {
        "MessageID": "message-12345",
        "MessageStream": "inbound",
        "MailboxHash": "source-hash",
        "From": "ops@example.test",
        "Subject": "Port notice",
        "Date": "Sun, 19 Jul 2026 08:00:00 GMT",
        "TextBody": "Full quoted body",
        "StrippedTextReply": "New advisory text",
        "HtmlBody": "",
        "Attachments": [],
    }
    values.update(overrides)
    return PostmarkInboundMessage.model_validate(values)


def test_postmark_payload_aliases_and_reply_preference() -> None:
    payload = inbound_payload()
    assert payload.message_id == "message-12345"
    assert _message_text(payload) == "New advisory text"
    assert _message_datetime(payload.date) == datetime(2026, 7, 19, 8, 0, tzinfo=UTC)


def test_postmark_rejects_wrong_stream_and_empty_content() -> None:
    with pytest.raises(ValidationError):
        inbound_payload(MessageStream="outbound")
    with pytest.raises(ValidationError, match="no usable content"):
        inbound_payload(TextBody="", StrippedTextReply="", HtmlBody="", RawEmail=None)


def test_postmark_validates_real_pdf_attachment_base64_and_length() -> None:
    stream = io.BytesIO()
    canvas = Canvas(stream)
    canvas.drawString(72, 760, "JMIC ADVISORY")
    canvas.save()
    raw = stream.getvalue()
    payload = inbound_payload(
        Attachments=[
            {
                "Name": "advisory.pdf",
                "Content": base64.b64encode(raw).decode("ascii"),
                "ContentType": "application/pdf",
                "ContentLength": len(raw),
            }
        ]
    )
    assert payload.attachments[0].content_length == len(raw)

    with pytest.raises(ValidationError, match="ContentLength"):
        inbound_payload(
            Attachments=[
                {
                    "Name": "advisory.pdf",
                    "Content": base64.b64encode(raw).decode("ascii"),
                    "ContentType": "application/pdf",
                    "ContentLength": len(raw) + 1,
                }
            ]
        )


def test_postmark_basic_auth_uses_configured_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        postmark_inbound_username="postmark",
        postmark_inbound_password=SecretStr("webhook-secret"),
    )
    monkeypatch.setattr("eastmed_api.security.get_settings", lambda: settings)
    require_postmark_basic(
        HTTPBasicCredentials(username="postmark", password="webhook-secret")  # noqa: S106
    )
    with pytest.raises(HTTPException) as error:
        require_postmark_basic(
            HTTPBasicCredentials(username="postmark", password="wrong")  # noqa: S106
        )
    assert error.value.status_code == 403

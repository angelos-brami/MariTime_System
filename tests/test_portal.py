from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from eastmed_api.briefs import BriefWorkflowError, finalize_daily_brief
from eastmed_api.contracts import BriefFinalizeCreate
from eastmed_api.portal import _board_event, portal_event
from eastmed_api.security import PortalPrincipal, require_portal_user
from eastmed_schema.enums import (
    AccountTier,
    BriefStatus,
    Corridor,
    Directness,
    EventType,
    RightsBasis,
    SourceTier,
)
from eastmed_schema.models import Account, DailyBrief, EventVersion, Source, SourceRecord, User
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy.orm import Session


def published_version(*, slug: str = "hormuz-test", version_no: int = 1) -> EventVersion:
    now = datetime.now(UTC)
    event_id = uuid4()
    return EventVersion(
        id=uuid4(),
        event_id=event_id,
        version_no=version_no,
        title="Authority revises transit reporting",
        summary_confirmed="A revised reporting window was published.",
        summary_reported="",
        summary_unknown="Westbound timing remains unknown.",
        whats_changed="Initial verified publication.",
        sentence_claim_map={},
        event_snapshot_json={
            "id": str(event_id),
            "slug": slug,
            "event_type": EventType.NAVIGATION_WARNING.value,
            "corridor": Corridor.HORMUZ_GULF.value,
            "status": "monitoring",
            "severity": 2,
            "ports": [],
            "occurred_start": now.isoformat(),
            "occurred_end": None,
        },
        claim_snapshot_json=[],
        evidence_snapshot_json=[],
        published_at=now,
        published_by="analyst",
        signed_off_by=None,
        policy_version="publication-policy-v1",
        model_versions={},
        content_hash="a" * 64,
    )


def test_board_event_uses_immutable_version_snapshot() -> None:
    version = published_version()
    result = _board_event(version)

    assert result.slug == "hormuz-test"
    assert result.corridor == Corridor.HORMUZ_GULF
    assert result.severity == 2
    assert result.version_no == 1


def test_portal_event_never_exposes_verify_only_excerpt() -> None:
    version = published_version()
    record_id = uuid4()
    lineage_id = uuid4()
    version.evidence_snapshot_json = [
        {
            "source_record_id": str(record_id),
            "lineage_root_id": str(lineage_id),
            "directness": Directness.PRIMARY.value,
            "excerpt": "This must remain private.",
            "rights_decision": {
                "basis": RightsBasis.VERIFY_ONLY_NO_REPUBLISH.value,
                "may_publish_excerpt": False,
            },
        }
    ]
    source = Source(
        id=uuid4(),
        name="Verification source",
        source_type="official",
        tier=SourceTier.A,
        language="en",
        access_method="manual",
        rights_basis=RightsBasis.VERIFY_ONLY_NO_REPUBLISH,
    )
    record = SourceRecord(
        id=record_id,
        source_id=source.id,
        url="https://example.test/source",
        canonical_url="https://example.test/source",
        content_hash="b" * 64,
        extracted_text="private verification content",
        fetched_at=datetime.now(UTC),
        parser_version="test",
        security_scan={},
    )
    session = MagicMock(spec=Session)
    session.scalars.side_effect = [SimpleNamespace(all=lambda: [version]), []]
    session.execute.return_value.all.return_value = [(record, source)]
    principal = PortalPrincipal(
        user_id=uuid4(),
        account_id=uuid4(),
        subject="user_test",
        email="ops@example.test",
        company="Test Shipping",
        role="subscriber",
    )

    result = portal_event(session, principal=principal, slug="hormuz-test")

    assert len(result.sources) == 1
    assert result.sources[0].excerpt is None
    assert result.sources[0].rights_basis == RightsBasis.VERIFY_ONLY_NO_REPUBLISH
    session.commit.assert_called_once()


def test_finalized_brief_pins_only_compiled_versions() -> None:
    version_id = uuid4()
    brief = DailyBrief(
        id=uuid4(),
        brief_date=date(2026, 7, 19),
        title="Draft brief",
        introduction="",
        forward_watch="",
        status=BriefStatus.DRAFT,
        items_json=[
            {
                "event_id": str(uuid4()),
                "event_slug": "hormuz-test",
                "event_version_id": str(version_id),
                "version_no": 1,
                "title": "Published event",
                "event_type": EventType.NAVIGATION_WARNING.value,
                "corridor": Corridor.HORMUZ_GULF.value,
                "status": "monitoring",
                "severity": 2,
                "published_at": datetime.now(UTC).isoformat(),
                "summary_confirmed": "Confirmed.",
                "summary_reported": "",
                "summary_unknown": "",
                "whats_changed": "Initial.",
                "content_hash": "c" * 64,
            }
        ],
        source_version_ids=[str(version_id)],
        corrections_json=[],
        compiled_at=datetime.now(UTC),
        compiled_by="scheduler",
        content_hash="d" * 64,
    )
    session = MagicMock(spec=Session)
    session.scalar.return_value = brief
    payload = BriefFinalizeCreate(
        finalized_by="senior-analyst",
        title="Final corridor watch",
        introduction="Overnight summary.",
        forward_watch="Watch the authority channel.",
        item_version_ids=[version_id],
    )

    result = finalize_daily_brief(session, brief_id=brief.id, payload=payload)

    assert result.status == BriefStatus.FINALIZED
    assert result.source_version_ids == [version_id]
    assert result.finalized_by == "senior-analyst"
    assert len(result.content_hash) == 64


def test_brief_rejects_version_outside_compiled_draft() -> None:
    brief = MagicMock(spec=DailyBrief)
    brief.status = BriefStatus.DRAFT
    brief.items_json = []
    session = MagicMock(spec=Session)
    session.scalar.return_value = brief
    payload = BriefFinalizeCreate(
        finalized_by="analyst",
        title="Daily brief",
        item_version_ids=[uuid4()],
    )

    with pytest.raises(BriefWorkflowError, match="outside its compiled draft"):
        finalize_daily_brief(session, brief_id=uuid4(), payload=payload)


def test_portal_user_requires_active_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    utc_today = datetime.now(UTC).date()
    account = Account(
        id=uuid4(),
        company="Expired Shipping",
        tier=AccountTier.DESK,
        contract_start=utc_today - timedelta(days=10),
        contract_end=utc_today - timedelta(days=1),
    )
    user = User(
        id=uuid4(),
        account_id=account.id,
        email="expired@example.test",
        channels_json={},
        role="subscriber",
        auth_subject="user_expired",
        active=True,
        portal_enabled=True,
    )
    session = MagicMock(spec=Session)
    session.execute.return_value.one_or_none.return_value = (user, account)
    monkeypatch.setattr(
        "eastmed_api.security.get_settings",
        lambda: SimpleNamespace(portal_api_token=SecretStr("p" * 48)),
    )

    with pytest.raises(HTTPException) as caught:
        require_portal_user(
            db=session,
            x_portal_token="p" * 48,
            x_portal_subject="user_expired",
        )

    assert caught.value.status_code == 403

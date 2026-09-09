from datetime import UTC, datetime

import pytest
from eastmed_pipeline.rights import (
    RightsPolicyError,
    authorize_automation,
    authorize_public_excerpt,
)
from eastmed_schema.enums import (
    AccessMethod,
    RightsBasis,
    SourceTier,
    SourceType,
)
from eastmed_schema.models import Source


def make_source(basis: RightsBasis, *, approved: bool = True) -> Source:
    return Source(
        name="Test source",
        source_type=SourceType.OFFICIAL,
        tier=SourceTier.A,
        language="en",
        access_method=AccessMethod.RSS,
        feed_url="https://example.com/feed.xml",
        rights_basis=basis,
        rights_reviewed_by="Counsel" if approved else None,
        automation_approved_at=datetime.now(UTC) if approved else None,
        active=True,
    )


def test_automation_requires_explicit_rights_approval() -> None:
    with pytest.raises(RightsPolicyError, match="not been approved"):
        authorize_automation(make_source(RightsBasis.PUBLIC_ADVISORY, approved=False))


def test_verify_only_source_can_be_ingested_but_not_excerpted() -> None:
    source = make_source(RightsBasis.VERIFY_ONLY_NO_REPUBLISH)
    decision = authorize_automation(source)
    assert decision.may_publish_excerpt is False
    assert decision.may_republish is False
    with pytest.raises(RightsPolicyError, match="cannot be excerpted"):
        authorize_public_excerpt(source)


def test_licensed_source_records_republication_permission() -> None:
    decision = authorize_automation(make_source(RightsBasis.LICENSED))
    assert decision.may_publish_excerpt is True
    assert decision.may_republish is True
    assert decision.attribution_required is False

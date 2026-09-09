from dataclasses import dataclass
from datetime import datetime

from eastmed_schema.enums import RightsBasis
from eastmed_schema.models import Source


class RightsPolicyError(PermissionError):
    pass


@dataclass(frozen=True)
class RightsDecision:
    basis: RightsBasis
    automation_approved_at: datetime
    reviewer: str
    may_publish_excerpt: bool
    may_republish: bool
    attribution_required: bool

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "basis": self.basis.value,
            "automation_approved_at": self.automation_approved_at.isoformat(),
            "reviewer": self.reviewer,
            "may_publish_excerpt": self.may_publish_excerpt,
            "may_republish": self.may_republish,
            "attribution_required": self.attribution_required,
        }


def authorize_automation(source: Source) -> RightsDecision:
    """Refuse all automated ingestion until the source has an explicit rights approval."""
    if not source.active:
        raise RightsPolicyError(f"Source {source.name!r} is inactive")
    if source.automation_approved_at is None or not source.rights_reviewed_by:
        raise RightsPolicyError(
            f"Source {source.name!r} has not been approved for automated ingestion"
        )

    basis = source.rights_basis
    return RightsDecision(
        basis=basis,
        automation_approved_at=source.automation_approved_at,
        reviewer=source.rights_reviewed_by,
        may_publish_excerpt=basis
        in {
            RightsBasis.PUBLIC_ADVISORY,
            RightsBasis.LICENSED,
            RightsBasis.ATTRIBUTE_QUOTE_MIN,
        },
        may_republish=basis is RightsBasis.LICENSED,
        attribution_required=basis is not RightsBasis.LICENSED,
    )


def authorize_public_excerpt(source: Source) -> RightsDecision:
    decision = authorize_automation(source)
    if not decision.may_publish_excerpt:
        raise RightsPolicyError(
            f"Source {source.name!r} may verify claims but cannot be excerpted publicly"
        )
    return decision

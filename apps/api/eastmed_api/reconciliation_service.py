"""Account-scoped reconciliation service layer for the customer API (blueprint 25).

Thin adapters between the HTTP routes and the durable reconciliation functions. Ingestion
authenticates as the account's connector and passes the authenticated ``account_id`` down —
never a tenant read from the payload (blueprint 17). Reads reuse the work-order-8 read model,
which is already non-disclosing (a case that is not the caller's is reported as not found).

The route layer stays thin; the logic that is worth testing lives here and in the pipeline,
tested directly against a database, matching how the rest of the service layer is tested.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from eastmed_pipeline.reconciliation_import import ImportOutcome, ImportStatus, import_record
from sqlalchemy.orm import Session

# Client-error statuses map to an HTTP code and are never committed (they persisted nothing).
# ACCEPTED / IDEMPOTENT / OUT_OF_CONTRACT are durable outcomes that the caller commits.
_CLIENT_ERROR_CODE: dict[ImportStatus, int] = {
    ImportStatus.REJECTED: 422,
    ImportStatus.MEMBERSHIP_DENIED: 403,
    ImportStatus.REVISION_CONFLICT: 409,
}


@dataclass(frozen=True)
class ReconIngestReceipt:
    status: ImportStatus
    capture_id: UUID | None
    expected_job_id: UUID | None
    case_id: UUID | None
    observation_ids: tuple[UUID, ...]
    detail: str | None

    @property
    def is_client_error(self) -> bool:
        return self.status in _CLIENT_ERROR_CODE

    @property
    def http_status(self) -> int:
        return _CLIENT_ERROR_CODE.get(self.status, 200)

    def render(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "capture_id": None if self.capture_id is None else str(self.capture_id),
            "expected_job_id": (
                None if self.expected_job_id is None else str(self.expected_job_id)
            ),
            "case_id": None if self.case_id is None else str(self.case_id),
            "observation_ids": [str(i) for i in self.observation_ids],
            "detail": self.detail,
        }


def _receipt(outcome: ImportOutcome) -> ReconIngestReceipt:
    detail = outcome.detail
    if detail is None and outcome.rejection is not None:
        detail = "record failed contract validation"
    return ReconIngestReceipt(
        status=outcome.status,
        capture_id=outcome.capture_id,
        expected_job_id=outcome.expected_job_id,
        case_id=outcome.case_id,
        observation_ids=outcome.observation_ids,
        detail=detail,
    )


def ingest_reconciliation_record(
    session: Session, *, account_id: UUID, payload: Mapping[str, Any]
) -> ReconIngestReceipt:
    """Import one contracted record for the authenticated account.

    The tenant is the authenticated connector's account, never the payload; a wrong,
    conflicting or unauthorized record is reported with a typed status the route turns into
    the appropriate HTTP code, and nothing about another tenant is disclosed."""
    outcome = import_record(session, account_id=account_id, payload=payload)
    return _receipt(outcome)

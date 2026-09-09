from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from eastmed_schema.enums import DrillStatus, DrillType
from eastmed_schema.models import AuditLog, OperationalDrill
from sqlalchemy.orm import Session


class DrillWorkflowError(ValueError):
    pass


def record_operational_drill(
    session: Session,
    *,
    drill_type: DrillType,
    passed: bool,
    started_at: datetime,
    completed_at: datetime,
    executed_by: str,
    environment: str,
    result: dict[str, Any],
) -> OperationalDrill:
    actor = executed_by.strip()
    if not 2 <= len(actor) <= 255 or actor.casefold().startswith("model:"):
        raise DrillWorkflowError("a named human must execute the operational drill")
    if started_at.tzinfo is None or completed_at.tzinfo is None:
        raise DrillWorkflowError("drill timestamps must include a timezone")
    if completed_at < started_at:
        raise DrillWorkflowError("drill completion cannot precede its start")
    canonical = {
        "drill_type": drill_type.value,
        "passed": passed,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "executed_by": actor,
        "environment": environment.strip(),
        "result": result,
    }
    evidence_hash = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    drill = OperationalDrill(
        drill_type=drill_type,
        status=DrillStatus.PASSED if passed else DrillStatus.FAILED,
        started_at=started_at,
        completed_at=completed_at,
        executed_by=actor,
        environment=environment.strip(),
        result_json=result,
        evidence_hash=evidence_hash,
    )
    session.add(drill)
    session.flush()
    session.add(
        AuditLog(
            actor=actor,
            action="operational_drill.recorded",
            entity="operational_drill",
            entity_id=drill.id,
            payload_json={
                "drill_type": drill_type.value,
                "status": drill.status.value,
                "evidence_hash": evidence_hash,
                "environment": drill.environment,
            },
        )
    )
    session.commit()
    session.refresh(drill)
    return drill

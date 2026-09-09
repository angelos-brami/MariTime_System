from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from typing import Any

from eastmed_api.database import SessionLocal
from eastmed_api.hardening import record_operational_drill
from eastmed_api.security import require_outbound_enabled
from eastmed_pipeline.delivery import (
    dispatch_pending_corrections,
    dispatch_pending_deliveries,
)
from eastmed_pipeline.notifications import NullDeskNotifier, build_desk_notifier
from eastmed_schema.enums import DrillType
from eastmed_shared import get_settings
from fastapi import HTTPException


class DatabaseAccessProbe:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"kill switch accessed the database through {name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--executed-by", default="senior-analyst")
    args = parser.parse_args()
    started = datetime.now(UTC)
    settings = get_settings()
    checks: dict[str, bool] = {"outbound_flag_is_off": not settings.outbound_enabled}
    probe = DatabaseAccessProbe()
    checks["alert_dispatch_stopped_before_db"] = (
        dispatch_pending_deliveries(probe, settings=settings) == 0  # type: ignore[arg-type]
    )
    checks["correction_dispatch_stopped_before_db"] = (
        dispatch_pending_corrections(probe, settings=settings) == 0  # type: ignore[arg-type]
    )
    checks["desk_notifications_disabled"] = isinstance(
        build_desk_notifier(settings), NullDeskNotifier
    )
    try:
        require_outbound_enabled()
    except HTTPException as exc:
        checks["publication_gate_closed"] = exc.status_code == 503
    else:
        checks["publication_gate_closed"] = False
    completed = datetime.now(UTC)
    result: dict[str, Any] = {
        "checks": checks,
        "passed": all(checks.values()),
        "elapsed_seconds": (completed - started).total_seconds(),
    }
    if args.record:
        with SessionLocal() as session:
            drill = record_operational_drill(
                session,
                drill_type=DrillType.KILL_SWITCH,
                passed=result["passed"],
                started_at=started,
                completed_at=completed,
                executed_by=args.executed_by,
                environment=settings.environment,
                result=result,
            )
        result["drill_id"] = str(drill.id)
        result["evidence_hash"] = drill.evidence_hash
    print(json.dumps(result, sort_keys=True, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

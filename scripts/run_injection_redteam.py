from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from eastmed_api.database import SessionLocal
from eastmed_api.hardening import record_operational_drill
from eastmed_pipeline.security_scan import scan_untrusted_text
from eastmed_schema.enums import DrillType
from eastmed_shared import get_settings


def run_cases(path: Path) -> dict[str, Any]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    results: list[dict[str, object]] = []
    for case in cases:
        scan = scan_untrusted_text(str(case["text"]))
        persisted_scan = scan.as_dict()
        expected = bool(case["malicious"])
        quarantined = bool(persisted_scan["quarantined"])
        results.append(
            {
                "id": str(case["id"]),
                "expected_malicious": expected,
                "detected": scan.injection_suspected,
                "quarantined": quarantined,
                "passed": scan.injection_suspected == expected and quarantined == expected,
                "matched_rules": list(scan.matched_rules),
                "text_sha256": hashlib.sha256(str(case["text"]).encode("utf-8")).hexdigest(),
            }
        )
    return {
        "scanner_version": "instruction-scan-v2",
        "total": len(results),
        "passed": sum(bool(item["passed"]) for item in results),
        "failed": sum(not bool(item["passed"]) for item in results),
        "cases": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=Path("config/injection_redteam_cases.json"))
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--executed-by", default="security-analyst")
    args = parser.parse_args()
    started = datetime.now(UTC)
    result = run_cases(args.cases)
    completed = datetime.now(UTC)
    if args.record:
        with SessionLocal() as session:
            drill = record_operational_drill(
                session,
                drill_type=DrillType.INJECTION,
                passed=result["failed"] == 0,
                started_at=started,
                completed_at=completed,
                executed_by=args.executed_by,
                environment=get_settings().environment,
                result=result,
            )
        result["drill_id"] = str(drill.id)
        result["evidence_hash"] = drill.evidence_hash
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    if result["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.run_restore_drill import backup_created_at, publication_payload_hash


def test_backup_recovery_point_comes_from_manifest_value_or_filename() -> None:
    assert backup_created_at(Path("eastmed-20260720T020304Z.dump"), None) == datetime(
        2026, 7, 20, 2, 3, 4, tzinfo=UTC
    )
    assert backup_created_at(Path("renamed.dump"), "2026-07-19T23:45:00+00:00") == datetime(
        2026, 7, 19, 23, 45, tzinfo=UTC
    )
    assert backup_created_at(Path("renamed.dump"), None) is None
    with pytest.raises(ValueError, match="timezone"):
        backup_created_at(Path("renamed.dump"), "2026-07-19T23:45:00")


def test_publication_hash_is_recomputed_from_the_immutable_audit_payload() -> None:
    canonical = {
        "event_id": "event-1",
        "version_no": 2,
        "title": "Published title",
        "claim_ids": ["claim-1"],
    }
    expected = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    assert publication_payload_hash({**canonical, "content_hash": expected}) == expected

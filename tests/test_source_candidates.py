from eastmed_pipeline.source_seed import load_candidates
from eastmed_schema.enums import AccessMethod, RightsBasis, SourceTier, SourceType


def test_tier_a_candidate_registry_is_disabled_by_construction() -> None:
    candidates = load_candidates()
    assert len(candidates) >= 60
    assert len({candidate["name"] for candidate in candidates}) == len(candidates)

    for candidate in candidates:
        assert SourceTier(candidate["tier"]) == SourceTier.A
        SourceType(candidate["source_type"])
        AccessMethod(candidate["access_method"])
        RightsBasis(candidate["rights_basis"])
        assert 60 <= int(candidate["poll_interval_seconds"]) <= 86400
        assert candidate["feed_url"].startswith("https://")
        assert candidate["rights_evidence_url"].startswith("https://")
        assert candidate["rights_status"] == "pending-counsel"
        assert candidate["legal_entity"]
        assert candidate["intended_use"]
        assert "active" not in candidate
        assert "automation_approved_at" not in candidate
        assert "rights_reviewed_by" not in candidate

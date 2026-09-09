from eastmed_pipeline.airtable_import import _bounded_int, _claim_state, _country, _hash_payload
from eastmed_schema.enums import ClaimState


def test_airtable_hash_is_stable_across_key_order() -> None:
    assert _hash_payload({"id": "rec1", "fields": {"a": 1, "b": 2}}) == _hash_payload(
        {"fields": {"b": 2, "a": 1}, "id": "rec1"}
    )


def test_airtable_confidence_labels_map_to_internal_states() -> None:
    assert _claim_state("Confirmed") == ClaimState.CONFIRMED
    assert _claim_state("Corroborated 2 independent") == ClaimState.CORROBORATED_2X
    assert _claim_state("Single source official") == ClaimState.SINGLE_OFFICIAL
    assert _claim_state("Reported") == ClaimState.REPORTED
    assert _claim_state("Disputed") == ClaimState.DISPUTED
    assert _claim_state("something else") == ClaimState.UNVERIFIED


def test_airtable_numeric_and_country_inputs_are_bounded() -> None:
    assert _bounded_int("9", default=1, minimum=1, maximum=4) == 4
    assert _bounded_int("bad", default=1, minimum=1, maximum=4) == 1
    assert _country(" gr ") == "GR"
    assert _country("Greece") is None

import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from eastmed_api.contracts import (
    ClaimExtractionReviewCreate,
    SourceOperationalUpdate,
)
from eastmed_pipeline.claim_extraction import (
    AnthropicClaimExtractionProvider,
    ClaimExtractionError,
    ClaimExtractionOutput,
    ClaimLocation,
    ExtractedClaim,
    ProviderResult,
    _validate_output_grounding,
    evaluate_component,
    process_source_record,
    record_qa,
    review_proposal,
    segment_document,
)
from eastmed_schema import Base
from eastmed_schema.enums import (
    AccessMethod,
    ClaimExtractionDecision,
    ClaimExtractionProposalStatus,
    ClaimExtractionRunStatus,
    RightsBasis,
    SourceTier,
    SourceType,
)
from eastmed_schema.models import (
    ClaimExtractionProposal,
    DeskAlert,
    Source,
    SourceRecord,
)
from eastmed_shared import Settings
from pydantic import SecretStr, ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

TABLES = [
    "sources",
    "source_records",
    "claim_extraction_runs",
    "claim_extraction_proposals",
    "claim_extraction_reviews",
    "claim_extraction_qa",
    "audit_log",
    "desk_alerts",
    "pipeline_evaluations",
]


class StaticProvider:
    def __init__(self, output: ClaimExtractionOutput) -> None:
        self.output = output
        self.calls = 0

    def extract(self, *, document: str, system_prompt: str) -> ProviderResult:
        assert "UNTRUSTED DATA" in system_prompt
        assert document
        self.calls += 1
        raw = self.output.model_dump_json()
        return ProviderResult(
            output=self.output,
            raw_text=raw,
            input_tokens=120,
            output_tokens=40,
        )


class SegmentProvider:
    def __init__(self) -> None:
        self.calls = 0

    def extract(self, *, document: str, system_prompt: str) -> ProviderResult:
        self.calls += 1
        claims: list[ExtractedClaim] = []
        for quote in (
            "FIRST CLAIM: Port Alpha is restricted.",
            "LAST CLAIM: Port Omega has reopened.",
        ):
            if quote in document:
                claims.append(
                    ExtractedClaim(
                        text=quote.removeprefix("FIRST CLAIM: ").removeprefix(
                            "LAST CLAIM: "
                        ),
                        claimant=None,
                        occurred_time=None,
                        location=None,
                        quantities=[],
                        hedging_language=False,
                        source_sentence_quote=quote,
                    )
                )
        output = ClaimExtractionOutput(claims=claims, injection_suspected=False)
        return ProviderResult(
            output=output,
            raw_text=output.model_dump_json(),
            input_tokens=100,
            output_tokens=20,
        )


@pytest.fixture
def extraction_db() -> Generator[Session, None, None]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[Base.metadata.tables[name] for name in TABLES])
    with Session(engine, expire_on_commit=False) as session:
        yield session


@pytest.fixture
def extraction_settings() -> Settings:
    return Settings(
        claim_extraction_enabled=True,
        claim_extraction_system_fingerprint="a" * 64,
        claim_extraction_model="claude-test-sonnet",
        anthropic_api_key=SecretStr("test-anthropic-api-key"),
    )


def add_record(
    session: Session,
    *,
    approved: bool = True,
    scan: dict[str, object] | None = None,
) -> tuple[Source, SourceRecord]:
    now = datetime.now(UTC)
    source = Source(
        name="Test authority",
        source_type=SourceType.OFFICIAL,
        tier=SourceTier.A,
        language="en",
        access_method=AccessMethod.RSS,
        rights_basis=RightsBasis.PUBLIC_ADVISORY,
        rights_reviewed_by="rights-analyst",
        automation_approved_at=now,
        model_processing_approved_at=now if approved else None,
        model_processing_approved_by="model-risk-reviewer" if approved else None,
        active=True,
    )
    session.add(source)
    session.flush()
    text = "The authority reported that Piraeus reopened at 09:00 UTC."
    record = SourceRecord(
        source_id=source.id,
        url="https://example.test/advisory",
        canonical_url="https://example.test/advisory",
        content_hash="a" * 64,
        extracted_text=text,
        title="Piraeus update",
        lang="en",
        fetched_at=now,
        parser_version="test-v1",
        security_scan=scan or {},
    )
    session.add(record)
    session.commit()
    return source, record


def valid_output(*, injection_suspected: bool = False) -> ClaimExtractionOutput:
    return ClaimExtractionOutput(
        claims=[
            ExtractedClaim(
                text="Piraeus reopened at 09:00 UTC.",
                claimant="The authority",
                occurred_time=None,
                location=ClaimLocation(name="Piraeus", unlocode=None),
                quantities=[],
                hedging_language=True,
                source_sentence_quote=(
                    "The authority reported that Piraeus reopened at 09:00 UTC."
                ),
            )
        ],
        injection_suspected=injection_suspected,
    )


def test_output_schema_forbids_confidence_and_inference_fields() -> None:
    payload = valid_output().model_dump(mode="json")
    payload["claims"][0]["confidence"] = 0.91
    with pytest.raises(ValidationError):
        ClaimExtractionOutput.model_validate(payload)


def test_runtime_images_include_the_versioned_prompt_directory() -> None:
    repository = Path(__file__).resolve().parents[1]
    for relative_path in ("apps/api/Dockerfile", "apps/api/Dockerfile.analysis"):
        dockerfile = (repository / relative_path).read_text(encoding="utf-8")
        assert "COPY prompts ./prompts" in dockerfile


def test_source_model_approval_must_be_complete_and_human() -> None:
    with pytest.raises(ValidationError):
        SourceOperationalUpdate(
            active=True,
            model_processing_approved_at=datetime.now(UTC),
        )
    with pytest.raises(ValidationError):
        SourceOperationalUpdate(
            active=True,
            model_processing_approved_at=datetime.now(UTC),
            model_processing_approved_by="model:approver",
        )
    with pytest.raises(ValidationError):
        SourceOperationalUpdate(
            active=True,
            rights_reviewed_by=None,
            model_processing_approved_at=None,
            model_processing_approved_by=None,
        )
    with pytest.raises(ValidationError, match="future-dated"):
        SourceOperationalUpdate(
            active=True,
            model_processing_approved_at=datetime.now(UTC) + timedelta(days=1),
            model_processing_approved_by="human-approver",
        )


def test_review_contract_enforces_human_shadow_decision_rules() -> None:
    with pytest.raises(ValidationError):
        ClaimExtractionReviewCreate(
            decision=ClaimExtractionDecision.REJECT,
            reviewer="analyst",
            reason_codes=[],
            baseline_seconds=100,
            review_seconds=40,
        )


def test_anthropic_adapter_uses_structured_output_without_tools() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "secret"
        assert request.headers["anthropic-version"] == "2023-06-01"
        request_payload = json.loads(request.content)
        assert request_payload["thinking"] == {"type": "disabled"}
        assert "temperature" not in request_payload
        body = json.loads(request.content)
        assert "tools" not in body
        assert body["output_config"]["format"]["type"] == "json_schema"
        assert "<untrusted_document>" in body["messages"][0]["content"]
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": valid_output().model_dump_json()}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 12, "output_tokens": 8},
            },
        )

    provider = AnthropicClaimExtractionProvider(
        api_key="secret",
        model="claude-test",
        max_output_tokens=1000,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = provider.extract(document="Source text", system_prompt="System prompt")
    assert len(result.output.claims) == 1
    assert result.input_tokens == 12


def test_anthropic_adapter_rejects_truncated_output() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "{}"}],
                "stop_reason": "max_tokens",
            },
        )
    )
    provider = AnthropicClaimExtractionProvider(
        api_key="secret",
        model="claude-test",
        max_output_tokens=1000,
        client=httpx.Client(transport=transport),
    )
    with pytest.raises(ClaimExtractionError, match="max_tokens"):
        provider.extract(document="Source text", system_prompt="System prompt")


def test_anthropic_adapter_bounds_streamed_response_bytes() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 2_000_001))
    provider = AnthropicClaimExtractionProvider(
        api_key="secret",
        model="claude-test",
        max_output_tokens=1000,
        client=httpx.Client(transport=transport),
    )
    with pytest.raises(ClaimExtractionError, match="2 MB"):
        provider.extract(document="Source text", system_prompt="System prompt")


def test_processing_creates_shadow_proposal_with_hashes_and_versions(
    extraction_db: Session, extraction_settings: Settings
) -> None:
    _, record = add_record(extraction_db)
    provider = StaticProvider(valid_output())
    run = process_source_record(
        extraction_db,
        source_record_id=record.id,
        provider=provider,
        settings=extraction_settings,
    )
    assert run.status == ClaimExtractionRunStatus.SUCCEEDED
    assert run.mode == "shadow"
    assert run.request_hash and run.response_hash
    assert run.input_tokens == 120
    assert run.coverage_complete is True
    assert run.document_char_count == len(record.extracted_text)
    assert run.processed_char_count == len(record.extracted_text)
    assert run.segment_count == 1
    proposal = extraction_db.scalar(select(ClaimExtractionProposal))
    assert proposal is not None
    assert proposal.status == ClaimExtractionProposalStatus.PENDING
    assert proposal.source_sentence_quote in record.extracted_text
    assert record.extracted_text[proposal.source_start : proposal.source_end] == (
        proposal.source_sentence_quote
    )
    assert proposal.segment_index == 0
    assert provider.calls == 1
    same_run = process_source_record(
        extraction_db,
        source_record_id=record.id,
        provider=provider,
        settings=extraction_settings,
    )
    assert same_run.id == run.id
    assert provider.calls == 1


def test_long_documents_are_fully_segmented_without_silent_truncation(
    extraction_db: Session, extraction_settings: Settings
) -> None:
    _, record = add_record(extraction_db)
    record.extracted_text = (
        "FIRST CLAIM: Port Alpha is restricted.\n\n"
        + ("Background operational context. " * 70)
        + "\n\nLAST CLAIM: Port Omega has reopened."
    )
    extraction_db.commit()
    segmented_settings = extraction_settings.model_copy(
        update={
            "claim_extraction_max_chars": 1_000,
            "claim_extraction_segment_overlap_chars": 100,
            "claim_extraction_max_segments": 10,
        }
    )
    provider = SegmentProvider()

    run = process_source_record(
        extraction_db,
        source_record_id=record.id,
        provider=provider,
        settings=segmented_settings,
    )

    assert run.status == ClaimExtractionRunStatus.SUCCEEDED
    assert run.coverage_complete is True
    assert run.document_char_count == len(record.extracted_text)
    assert run.processed_char_count == len(record.extracted_text)
    assert run.segment_count == provider.calls
    assert run.segment_count > 1
    assert run.input_tokens == provider.calls * 100
    proposals = list(
        extraction_db.scalars(
            select(ClaimExtractionProposal).order_by(
                ClaimExtractionProposal.proposal_index
            )
        ).all()
    )
    assert [proposal.source_sentence_quote for proposal in proposals] == [
        "FIRST CLAIM: Port Alpha is restricted.",
        "LAST CLAIM: Port Omega has reopened.",
    ]
    for proposal in proposals:
        assert record.extracted_text[proposal.source_start : proposal.source_end] == (
            proposal.source_sentence_quote
        )


def test_document_over_segment_limit_is_explicitly_skipped_before_provider(
    extraction_db: Session, extraction_settings: Settings
) -> None:
    _, record = add_record(extraction_db)
    record.extracted_text = "Operational context. " * 120
    extraction_db.commit()
    limited_settings = extraction_settings.model_copy(
        update={
            "claim_extraction_max_chars": 1_000,
            "claim_extraction_segment_overlap_chars": 100,
            "claim_extraction_max_segments": 1,
        }
    )
    provider = SegmentProvider()

    run = process_source_record(
        extraction_db,
        source_record_id=record.id,
        provider=provider,
        settings=limited_settings,
    )

    assert run.status == ClaimExtractionRunStatus.SKIPPED
    assert run.error_type == "DocumentSegmentLimitExceeded"
    assert run.coverage_complete is False
    assert run.document_char_count == len(record.extracted_text)
    assert run.processed_char_count == 0
    assert provider.calls == 0


def test_segmenter_has_no_coverage_gaps() -> None:
    document = "Paragraph one.\n\n" + ("x" * 2_500) + "\n\nParagraph last."
    segments = segment_document(document, max_chars=1_000, overlap_chars=100)

    assert segments[0].start == 0
    assert segments[-1].end == len(document)
    assert all(
        left.end >= right.start
        for left, right in zip(segments, segments[1:], strict=False)
    )
    assert all(segment.text == document[segment.start : segment.end] for segment in segments)


def test_unapproved_and_quarantined_sources_never_reach_provider(
    extraction_db: Session, extraction_settings: Settings
) -> None:
    _, unapproved = add_record(extraction_db, approved=False)
    provider = StaticProvider(valid_output())
    with pytest.raises(ClaimExtractionError, match="not approved"):
        process_source_record(
            extraction_db,
            source_record_id=unapproved.id,
            provider=provider,
            settings=extraction_settings,
        )
    assert provider.calls == 0


def test_invalid_quote_fails_run_without_persisting_proposals(
    extraction_db: Session, extraction_settings: Settings
) -> None:
    _, record = add_record(extraction_db)
    output = valid_output()
    output.claims[0].source_sentence_quote = "This sentence is not in the record."
    run = process_source_record(
        extraction_db,
        source_record_id=record.id,
        provider=StaticProvider(output),
        settings=extraction_settings,
    )
    assert run.status == ClaimExtractionRunStatus.FAILED
    assert run.error_message == "Model returned a source quote not present in the document"
    assert extraction_db.scalar(select(func.count(ClaimExtractionProposal.id))) == 0


def test_inferred_unlocode_fails_grounding_validation(
    extraction_db: Session, extraction_settings: Settings
) -> None:
    _, record = add_record(extraction_db)
    output = valid_output()
    assert output.claims[0].location is not None
    output.claims[0].location.unlocode = "GRPIR"
    run = process_source_record(
        extraction_db,
        source_record_id=record.id,
        provider=StaticProvider(output),
        settings=extraction_settings,
    )
    assert run.status == ClaimExtractionRunStatus.FAILED
    assert run.error_message == "Model returned a UN/LOCODE not supported by its source quote"


def test_grounding_rejects_an_invented_number_even_when_quote_is_exact() -> None:
    output = valid_output()
    output.claims[0].text = "Piraeus reopened at 10:30 UTC."
    with pytest.raises(ClaimExtractionError, match="introduced a number"):
        _validate_output_grounding(
            output,
            document="The authority reported that Piraeus reopened at 09:00 UTC.",
        )


def test_grounding_rejects_unrelated_exact_quote_from_same_document() -> None:
    output = valid_output()
    output.claims[0].claimant = None
    output.claims[0].location = None
    output.claims[0].text = "Piraeus terminal operations remain restricted."
    output.claims[0].source_sentence_quote = "Weather conditions remain calm across the region."
    with pytest.raises(ClaimExtractionError, match="materially supported"):
        _validate_output_grounding(
            output,
            document=(
                "Piraeus terminal operations remain restricted. "
                "Weather conditions remain calm across the region."
            ),
        )


def test_grounding_rejects_removed_negation() -> None:
    output = valid_output()
    output.claims[0].claimant = None
    output.claims[0].location = None
    output.claims[0].text = "The channel is open to traffic."
    output.claims[0].source_sentence_quote = "The channel is not open to traffic."
    with pytest.raises(ClaimExtractionError, match="negation polarity"):
        _validate_output_grounding(output, document=output.claims[0].source_sentence_quote)


def test_model_injection_flag_creates_desk_alert_and_no_proposals(
    extraction_db: Session, extraction_settings: Settings
) -> None:
    _, record = add_record(extraction_db)
    output = valid_output(injection_suspected=True)
    output.claims[0].source_sentence_quote = "Injection flags take precedence over bad claims."
    run = process_source_record(
        extraction_db,
        source_record_id=record.id,
        provider=StaticProvider(output),
        settings=extraction_settings,
    )
    assert run.status == ClaimExtractionRunStatus.SKIPPED
    assert run.injection_suspected is True
    assert extraction_db.scalar(select(func.count(ClaimExtractionProposal.id))) == 0
    assert extraction_db.scalar(select(func.count(DeskAlert.id))) == 1


def test_shadow_review_records_decision_but_does_not_create_claim(
    extraction_db: Session, extraction_settings: Settings
) -> None:
    _, record = add_record(extraction_db)
    run = process_source_record(
        extraction_db,
        source_record_id=record.id,
        provider=StaticProvider(valid_output()),
        settings=extraction_settings,
    )
    proposal = extraction_db.scalar(
        select(ClaimExtractionProposal).where(ClaimExtractionProposal.run_id == run.id)
    )
    assert proposal is not None
    reviewed_at = datetime.now(UTC) - timedelta(days=22)
    review = review_proposal(
        extraction_db,
        proposal_id=proposal.id,
        decision=ClaimExtractionDecision.REJECT,
        reviewer="desk-analyst",
        event_id=None,
        claim_state=None,
        edited_claim=None,
        reason_codes=["not_material"],
        note=None,
        baseline_seconds=120,
        review_seconds=40,
        settings=extraction_settings,
        now=reviewed_at,
    )
    assert review.mode == "shadow"
    assert review.resulting_claim_id is None
    with pytest.raises(ClaimExtractionError, match="different human"):
        record_qa(
            extraction_db,
            review_id=review.id,
            evaluator="desk-analyst",
            error_found=False,
            error_codes=[],
            note=None,
        )
    qa = record_qa(
        extraction_db,
        review_id=review.id,
        evaluator="qa-editor",
        error_found=False,
        error_codes=[],
        note=None,
    )
    assert qa.error_found is False
    evaluation, metrics = evaluate_component(
        extraction_db,
        language="en",
        settings=extraction_settings,
    )
    assert evaluation.graduated is False
    assert metrics.baseline_error_rate is None
    assert metrics.model_version == extraction_settings.claim_extraction_model
    assert metrics.time_reduction_percent == pytest.approx(66.6666, rel=0.001)

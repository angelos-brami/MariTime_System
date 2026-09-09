from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

import httpx
import yaml
from eastmed_schema.enums import (
    ClaimExtractionDecision,
    ClaimExtractionProposalStatus,
    ClaimExtractionRunStatus,
    ClaimState,
    DeskAlertKind,
    DeskAlertStatus,
)
from eastmed_schema.models import (
    AuditLog,
    Claim,
    ClaimExtractionProposal,
    ClaimExtractionQA,
    ClaimExtractionReview,
    ClaimExtractionRun,
    DeskAlert,
    Event,
    PipelineEvaluation,
    Source,
    SourceRecord,
)
from eastmed_shared import Settings, get_settings
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

PROMPT_VERSION = "claim_extraction_v2"
COMPONENT = "claim_extraction"
SEGMENTATION_VERSION = "coverage_segmenter_v1"
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
NUMBER_PATTERN = re.compile(r"(?<!\w)[+-]?\d+(?:[.,:/-]\d+)*(?!\w)")
TOKEN_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)
GROUNDING_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "that",
        "this",
        "with",
        "from",
        "for",
        "was",
        "were",
        "has",
        "have",
        "had",
        "are",
        "but",
        "its",
        "into",
        "στην",
        "στον",
        "και",
        "bir",
        "ile",
        "ve",
        "من",
        "في",
        "على",
    }
)
NEGATION_TERMS = frozenset(
    {
        "not",
        "no",
        "never",
        "without",
        "neither",
        "nor",
        "denied",
        "denies",
        "δεν",
        "χωρίς",
        "ούτε",
        "değil",
        "yok",
        "olmadan",
        "لا",
        "لم",
        "لن",
        "ليس",
        "دون",
    }
)


class ClaimExtractionError(ValueError):
    pass


class ClaimQuantity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1, max_length=100)
    unit: str = Field(min_length=1, max_length=100)
    what: str = Field(min_length=1, max_length=500)


class ClaimLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=500)
    unlocode: str | None = Field(default=None, min_length=5, max_length=5)

    @field_validator("unlocode")
    @classmethod
    def normalize_unlocode(cls, value: str | None) -> str | None:
        return value.upper() if value else None


class ExtractedClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=3, max_length=10_000)
    claimant: str | None = Field(default=None, min_length=1, max_length=255)
    occurred_time: datetime | None = None
    location: ClaimLocation | None = None
    quantities: list[ClaimQuantity] = Field(default_factory=list, max_length=50)
    hedging_language: bool
    source_sentence_quote: str = Field(min_length=1, max_length=5_000)

    @field_validator("occurred_time")
    @classmethod
    def occurred_time_has_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("occurred_time must include a timezone")
        return value

    @field_validator("text", "source_sentence_quote")
    @classmethod
    def no_empty_surrounding_space(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value cannot be blank")
        return stripped


class ClaimExtractionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[ExtractedClaim] = Field(default_factory=list, max_length=100)
    injection_suspected: bool


@dataclass(frozen=True)
class ProviderResult:
    output: ClaimExtractionOutput
    raw_text: str
    input_tokens: int | None
    output_tokens: int | None


@dataclass(frozen=True)
class DocumentSegment:
    index: int
    start: int
    end: int
    text: str


class ClaimExtractionProvider(Protocol):
    def extract(self, *, document: str, system_prompt: str) -> ProviderResult: ...


class AnthropicClaimExtractionProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        max_output_tokens: int,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._max_output_tokens = max_output_tokens
        self._client = client

    def extract(self, *, document: str, system_prompt: str) -> ProviderResult:
        request_json = {
            "model": self._model,
            "max_tokens": self._max_output_tokens,
            "thinking": {"type": "disabled"},
            "system": system_prompt,
            "messages": [
                {
                    "role": "user",
                    "content": f"<untrusted_document>\n{document}\n</untrusted_document>",
                }
            ],
            "output_config": {
                "format": {
                    "type": "json_schema",
                    "schema": ClaimExtractionOutput.model_json_schema(),
                }
            },
        }
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        if self._client is not None:
            response_bytes = self._bounded_post(self._client, headers, request_json)
        else:
            with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
                response_bytes = self._bounded_post(client, headers, request_json)
        try:
            payload = json.loads(response_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ClaimExtractionError("Anthropic returned malformed JSON") from exc
        stop_reason = payload.get("stop_reason")
        if stop_reason in {"max_tokens", "refusal"}:
            raise ClaimExtractionError(f"Anthropic stopped without a usable result: {stop_reason}")
        content = payload.get("content")
        if not isinstance(content, list) or len(content) != 1:
            raise ClaimExtractionError("Anthropic returned an unexpected content envelope")
        block = content[0]
        if not isinstance(block, dict) or block.get("type") != "text":
            raise ClaimExtractionError("Anthropic returned a non-text structured result")
        raw_text = block.get("text")
        if not isinstance(raw_text, str):
            raise ClaimExtractionError("Anthropic returned no structured text")
        try:
            output = ClaimExtractionOutput.model_validate_json(raw_text)
        except ValidationError as exc:
            raise ClaimExtractionError("Anthropic output failed the extraction schema") from exc
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        return ProviderResult(
            output=output,
            raw_text=raw_text,
            input_tokens=_optional_int(usage.get("input_tokens")),
            output_tokens=_optional_int(usage.get("output_tokens")),
        )

    @staticmethod
    def _bounded_post(
        client: httpx.Client,
        headers: dict[str, str],
        request_json: dict[str, Any],
    ) -> bytes:
        with client.stream(
            "POST",
            ANTHROPIC_MESSAGES_URL,
            headers=headers,
            json=request_json,
        ) as response:
            response.raise_for_status()
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > 2_000_000:
                    raise ClaimExtractionError("Anthropic response exceeded the 2 MB safety limit")
            return bytes(body)


class ExtractionRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    prompt_version: str
    mode: str
    minimum_shadow_days: int = Field(ge=60)
    minimum_reviewed_per_language: int = Field(ge=1)
    minimum_qa_per_language: int = Field(ge=1)
    minimum_time_reduction_percent: float = Field(ge=0, le=100)
    baseline_error_rate_by_language: dict[str, float | None]

    @field_validator("mode")
    @classmethod
    def supported_mode(cls, value: str) -> str:
        if value not in {"shadow", "production"}:
            raise ValueError("mode must be shadow or production")
        return value

    @field_validator("baseline_error_rate_by_language")
    @classmethod
    def rates_are_bounded(cls, value: dict[str, float | None]) -> dict[str, float | None]:
        if any(rate is not None and not 0 <= rate <= 1 for rate in value.values()):
            raise ValueError("baseline error rates must be between 0 and 1")
        return value


@dataclass(frozen=True)
class ClaimExtractionMetrics:
    language: str
    prompt_version: str
    model_version: str
    reviewed: int
    qa_reviewed: int
    shadow_days: float
    time_reduction_percent: float
    error_rate: float
    baseline_error_rate: float | None
    graduated: bool


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _repository_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    candidates = (Path.cwd() / path, Path(__file__).resolve().parents[3] / path)
    return next((candidate for candidate in candidates if candidate.is_file()), candidates[0])


def load_prompt(settings: Settings) -> str:
    path = _repository_path(settings.claim_extraction_prompt_path)
    prompt = path.read_text(encoding="utf-8").strip()
    if not prompt:
        raise ClaimExtractionError("claim extraction prompt is empty")
    return prompt


def load_rules(settings: Settings) -> ExtractionRules:
    path = _repository_path(settings.claim_extraction_rules_path)
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return ExtractionRules.model_validate(payload)


def build_claim_extraction_provider(settings: Settings | None = None) -> ClaimExtractionProvider:
    active = settings or get_settings()
    if active.anthropic_api_key is None:
        raise ClaimExtractionError("Anthropic API key is not configured")
    return AnthropicClaimExtractionProvider(
        api_key=active.anthropic_api_key.get_secret_value(),
        model=active.claim_extraction_model,
        max_output_tokens=active.claim_extraction_max_output_tokens,
    )


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def segment_document(
    document: str, *, max_chars: int, overlap_chars: int
) -> list[DocumentSegment]:
    if max_chars <= 0:
        raise ClaimExtractionError("segment size must be positive")
    if overlap_chars < 0 or overlap_chars * 4 > max_chars:
        raise ClaimExtractionError("segment overlap must be between 0% and 25%")
    if not document:
        return []

    segments: list[DocumentSegment] = []
    start = 0
    while start < len(document):
        hard_end = min(start + max_chars, len(document))
        end = hard_end
        if hard_end < len(document):
            minimum_boundary = start + (max_chars // 2)
            paragraph = document.rfind("\n\n", minimum_boundary, hard_end)
            line = document.rfind("\n", minimum_boundary, hard_end)
            sentence = document.rfind(". ", minimum_boundary, hard_end)
            boundary = max(paragraph + 2, line + 1, sentence + 2)
            if boundary > minimum_boundary:
                end = boundary
        segments.append(
            DocumentSegment(
                index=len(segments),
                start=start,
                end=end,
                text=document[start:end],
            )
        )
        if end == len(document):
            break
        next_start = end - overlap_chars
        if next_start <= start:
            raise ClaimExtractionError("segmenter failed to advance")
        start = next_start
    return segments


def _segment_manifest(segments: list[DocumentSegment]) -> list[dict[str, int | str]]:
    return [
        {
            "index": segment.index,
            "start": segment.start,
            "end": segment.end,
            "sha256": _hash(segment.text),
        }
        for segment in segments
    ]


def _total_tokens(values: list[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    return sum(present) if present else None


def _claim_key(claim: ExtractedClaim) -> str:
    return _hash(
        json.dumps(
            claim.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def _source_is_quarantined(record: SourceRecord) -> bool:
    scan = record.security_scan or {}
    return bool(scan.get("quarantined") or scan.get("injection_suspected"))


def eligible_source_records(
    session: Session,
    *,
    model_version: str,
    prompt_version: str = PROMPT_VERSION,
    limit: int = 50,
) -> list[UUID]:
    completed = select(ClaimExtractionRun.source_record_id).where(
        ClaimExtractionRun.prompt_version == prompt_version,
        ClaimExtractionRun.model_version == model_version,
    )
    rows = session.execute(
        select(SourceRecord.id, SourceRecord.security_scan)
        .join(Source, Source.id == SourceRecord.source_id)
        .where(
            Source.active.is_(True),
            Source.model_processing_approved_at.is_not(None),
            Source.model_processing_approved_at <= func.now(),
            Source.model_processing_approved_by.is_not(None),
            SourceRecord.id.not_in(completed),
        )
        .order_by(SourceRecord.fetched_at)
        .limit(limit * 2)
    ).all()
    return [record_id for record_id, scan in rows if not _scan_is_quarantined(scan)][:limit]


def _scan_is_quarantined(scan: dict[str, Any] | None) -> bool:
    value = scan or {}
    return bool(value.get("quarantined") or value.get("injection_suspected"))


def process_source_record(
    session: Session,
    *,
    source_record_id: UUID,
    provider: ClaimExtractionProvider,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> ClaimExtractionRun:
    active = settings or get_settings()
    rules = load_rules(active)
    prompt = load_prompt(active)
    if rules.prompt_version != PROMPT_VERSION:
        raise ClaimExtractionError("rules and implementation prompt versions differ")
    existing = session.scalar(
        select(ClaimExtractionRun).where(
            ClaimExtractionRun.source_record_id == source_record_id,
            ClaimExtractionRun.prompt_version == PROMPT_VERSION,
            ClaimExtractionRun.model_version == active.claim_extraction_model,
        )
    )
    if existing is not None:
        return existing
    row = session.execute(
        select(SourceRecord, Source)
        .join(Source, Source.id == SourceRecord.source_id)
        .where(SourceRecord.id == source_record_id)
    ).one_or_none()
    if row is None:
        raise LookupError("Source record not found")
    record, source = row
    if not source.active:
        raise ClaimExtractionError("Source is inactive")
    current_time = now or datetime.now(UTC)
    if source.model_processing_approved_at is None or not source.model_processing_approved_by:
        raise ClaimExtractionError("Source is not approved for model processing")
    approved_at = source.model_processing_approved_at
    if approved_at.tzinfo is None:
        approved_at = approved_at.replace(tzinfo=UTC)
    if approved_at > current_time:
        raise ClaimExtractionError("Source model-processing approval is not yet effective")
    if _source_is_quarantined(record):
        raise ClaimExtractionError("Quarantined source records cannot be processed")
    document = record.extracted_text
    if not document.strip():
        raise ClaimExtractionError("Source record has no extracted text")
    segments = segment_document(
        document,
        max_chars=active.claim_extraction_max_chars,
        overlap_chars=active.claim_extraction_segment_overlap_chars,
    )
    segment_manifest = _segment_manifest(segments)
    started_at = current_time
    request_hash = _hash(
        json.dumps(
            {
                "document_sha256": _hash(document),
                "document_char_count": len(document),
                "model": active.claim_extraction_model,
                "prompt": prompt,
                "prompt_version": PROMPT_VERSION,
                "segmentation_version": SEGMENTATION_VERSION,
                "segments": segment_manifest,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    run = ClaimExtractionRun(
        source_record_id=record.id,
        prompt_version=PROMPT_VERSION,
        model_version=active.claim_extraction_model,
        language=(record.lang or source.language).casefold(),
        mode=rules.mode,
        status=ClaimExtractionRunStatus.RUNNING,
        started_at=started_at,
        request_hash=request_hash,
        document_char_count=len(document),
        processed_char_count=0,
        segment_count=len(segments),
        segment_manifest_json=segment_manifest,
        coverage_complete=False,
        injection_suspected=False,
    )
    session.add(run)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raced_run = session.scalar(
            select(ClaimExtractionRun).where(
                ClaimExtractionRun.source_record_id == source_record_id,
                ClaimExtractionRun.prompt_version == PROMPT_VERSION,
                ClaimExtractionRun.model_version == active.claim_extraction_model,
            )
        )
        if raced_run is not None:
            return raced_run
        raise
    session.refresh(run)
    if len(segments) > active.claim_extraction_max_segments:
        run.status = ClaimExtractionRunStatus.SKIPPED
        run.finished_at = datetime.now(UTC)
        run.error_type = "DocumentSegmentLimitExceeded"
        run.error_message = (
            f"Document requires {len(segments)} segments; configured maximum is "
            f"{active.claim_extraction_max_segments}"
        )
        session.add(
            AuditLog(
                actor="claim-extraction-worker",
                action="claim_extraction.skipped",
                entity="claim_extraction_run",
                entity_id=run.id,
                payload_json={
                    "reason": run.error_type,
                    "document_char_count": run.document_char_count,
                    "segment_count": run.segment_count,
                    "coverage_complete": False,
                },
            )
        )
        session.commit()
        session.refresh(run)
        return run

    completed_results: list[tuple[DocumentSegment, ProviderResult]] = []
    raw_responses: list[str] = []
    processed_until = 0
    try:
        for segment in segments:
            result = provider.extract(document=segment.text, system_prompt=prompt)
            completed_results.append((segment, result))
            raw_responses.append(result.raw_text)
            processed_until = max(processed_until, segment.end)
            if result.output.injection_suspected:
                break
            _validate_output_grounding(result.output, document=segment.text)

        run.response_hash = _hash(
            json.dumps(raw_responses, ensure_ascii=False, separators=(",", ":"))
        )
        run.input_tokens = _total_tokens(
            [result.input_tokens for _, result in completed_results]
        )
        run.output_tokens = _total_tokens(
            [result.output_tokens for _, result in completed_results]
        )
        run.processed_char_count = processed_until
        run.coverage_complete = processed_until >= len(document)
        run.injection_suspected = any(
            result.output.injection_suspected for _, result in completed_results
        )
        run.finished_at = datetime.now(UTC)
        proposal_count = 0
        if run.injection_suspected:
            run.status = ClaimExtractionRunStatus.SKIPPED
            _add_injection_alert(session, source=source, record=record, run=run)
        else:
            if not run.coverage_complete:
                raise ClaimExtractionError(
                    "Claim extraction ended before document coverage completed"
                )
            proposals: list[tuple[DocumentSegment, ExtractedClaim, int, int]] = []
            seen_claims: set[str] = set()
            for segment, result in completed_results:
                for claim in result.output.claims:
                    claim_key = _claim_key(claim)
                    if claim_key in seen_claims:
                        continue
                    seen_claims.add(claim_key)
                    local_start = segment.text.find(claim.source_sentence_quote)
                    if local_start < 0:
                        raise ClaimExtractionError(
                            "Model returned a source quote not present in the document"
                        )
                    source_start = segment.start + local_start
                    source_end = source_start + len(claim.source_sentence_quote)
                    proposals.append((segment, claim, source_start, source_end))
            if len(proposals) > active.claim_extraction_max_proposals_per_run:
                raise ClaimExtractionError(
                    "Claim extraction exceeded the configured proposal limit"
                )
            for index, (segment, claim, source_start, source_end) in enumerate(proposals):
                session.add(
                    ClaimExtractionProposal(
                        run_id=run.id,
                        source_record_id=record.id,
                        proposal_index=index,
                        text=claim.text,
                        claimant=claim.claimant,
                        occurred_time=claim.occurred_time,
                        location_json=(
                            claim.location.model_dump(mode="json") if claim.location else None
                        ),
                        quantities_json=[
                            quantity.model_dump(mode="json") for quantity in claim.quantities
                        ],
                        hedging_language=claim.hedging_language,
                        source_sentence_quote=claim.source_sentence_quote,
                        source_start=source_start,
                        source_end=source_end,
                        segment_index=segment.index,
                        status=ClaimExtractionProposalStatus.PENDING,
                    )
                )
            proposal_count = len(proposals)
            run.status = ClaimExtractionRunStatus.SUCCEEDED
        session.add(
            AuditLog(
                actor="claim-extraction-worker",
                action="claim_extraction.completed",
                entity="claim_extraction_run",
                entity_id=run.id,
                payload_json={
                    "model_version": run.model_version,
                    "prompt_version": run.prompt_version,
                    "mode": run.mode,
                    "status": run.status.value,
                    "claim_count": proposal_count,
                    "injection_suspected": run.injection_suspected,
                    "request_hash": run.request_hash,
                    "response_hash": run.response_hash,
                    "document_char_count": run.document_char_count,
                    "processed_char_count": run.processed_char_count,
                    "segment_count": run.segment_count,
                    "coverage_complete": run.coverage_complete,
                    "segmentation_version": SEGMENTATION_VERSION,
                },
            )
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        recovered_run = session.get(ClaimExtractionRun, run.id)
        if recovered_run is None:
            raise
        run = recovered_run
        run.status = ClaimExtractionRunStatus.FAILED
        run.finished_at = datetime.now(UTC)
        run.error_type = type(exc).__name__[:255]
        run.error_message = _safe_error_message(exc)
        run.processed_char_count = processed_until
        run.coverage_complete = False
        if raw_responses:
            run.response_hash = _hash(
                json.dumps(raw_responses, ensure_ascii=False, separators=(",", ":"))
            )
        run.input_tokens = _total_tokens(
            [result.input_tokens for _, result in completed_results]
        )
        run.output_tokens = _total_tokens(
            [result.output_tokens for _, result in completed_results]
        )
        session.add(
            AuditLog(
                actor="claim-extraction-worker",
                action="claim_extraction.failed",
                entity="claim_extraction_run",
                entity_id=run.id,
                payload_json={
                    "model_version": run.model_version,
                    "prompt_version": run.prompt_version,
                    "error_type": run.error_type,
                    "document_char_count": run.document_char_count,
                    "processed_char_count": run.processed_char_count,
                    "segment_count": run.segment_count,
                    "coverage_complete": False,
                },
            )
        )
        session.commit()
    session.refresh(run)
    return run


def _add_injection_alert(
    session: Session, *, source: Source, record: SourceRecord, run: ClaimExtractionRun
) -> None:
    session.add(
        DeskAlert(
            source_id=source.id,
            kind=DeskAlertKind.SOURCE_QUARANTINE,
            status=DeskAlertStatus.OPEN,
            detected_at=datetime.now(UTC),
            detail={
                "source_record_id": str(record.id),
                "claim_extraction_run_id": str(run.id),
                "reason": "model_detected_prompt_injection",
            },
        )
    )


def _validate_output_grounding(output: ClaimExtractionOutput, *, document: str) -> None:
    for claim in output.claims:
        if claim.source_sentence_quote not in document:
            raise ClaimExtractionError("Model returned a source quote not present in the document")
        folded_quote = claim.source_sentence_quote.casefold()
        if claim.claimant and claim.claimant.casefold() not in folded_quote:
            raise ClaimExtractionError(
                "Model returned a claimant not supported by its source quote"
            )
        if claim.location:
            if claim.location.name.casefold() not in folded_quote:
                raise ClaimExtractionError(
                    "Model returned a location not supported by its source quote"
                )
            if (
                claim.location.unlocode
                and claim.location.unlocode.casefold() not in folded_quote
            ):
                raise ClaimExtractionError(
                    "Model returned a UN/LOCODE not supported by its source quote"
                )
        claim_numbers = set(NUMBER_PATTERN.findall(claim.text.casefold()))
        quote_numbers = set(NUMBER_PATTERN.findall(folded_quote))
        if not claim_numbers <= quote_numbers:
            raise ClaimExtractionError("Model introduced a number not present in its source quote")
        for quantity in claim.quantities:
            if quantity.value.casefold() not in folded_quote:
                raise ClaimExtractionError(
                    "Model returned a quantity value not supported by its source quote"
                )
            if quantity.unit.casefold() not in folded_quote:
                raise ClaimExtractionError(
                    "Model returned a quantity unit not supported by its source quote"
                )
        claim_tokens = {
            token
            for token in TOKEN_PATTERN.findall(claim.text.casefold())
            if len(token) >= 3 and token not in GROUNDING_STOPWORDS
        }
        quote_tokens = set(TOKEN_PATTERN.findall(folded_quote))
        required_overlap = min(len(claim_tokens), max(2, (len(claim_tokens) + 2) // 3))
        if claim_tokens and len(claim_tokens & quote_tokens) < required_overlap:
            raise ClaimExtractionError(
                "Model claim is not materially supported by its source quote"
            )
        claim_has_negation = bool(claim_tokens & NEGATION_TERMS)
        quote_has_negation = bool(quote_tokens & NEGATION_TERMS)
        if claim_has_negation != quote_has_negation:
            raise ClaimExtractionError(
                "Model changed negation polarity relative to its source quote"
            )


def _safe_error_message(exc: Exception) -> str:
    if isinstance(exc, ClaimExtractionError):
        return str(exc)[:2_000]
    if isinstance(exc, httpx.TimeoutException):
        return "Anthropic request timed out"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"Anthropic request failed with HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.RequestError):
        return "Anthropic request failed before a response was received"
    return "Unexpected claim-extraction failure"


def _normalized_codes(codes: list[str]) -> list[str]:
    if len(codes) > 20:
        raise ClaimExtractionError("No more than 20 reason codes are allowed")
    normalized = [code.strip() for code in codes]
    if any(not code or len(code) > 64 for code in normalized):
        raise ClaimExtractionError(
            "Reason codes must be non-empty and no longer than 64 characters"
        )
    if len(normalized) != len(set(normalized)):
        raise ClaimExtractionError("Reason codes must be unique")
    return sorted(normalized)


def review_proposal(
    session: Session,
    *,
    proposal_id: UUID,
    decision: ClaimExtractionDecision,
    reviewer: str,
    event_id: UUID | None,
    claim_state: ClaimState | None,
    edited_claim: ExtractedClaim | None,
    reason_codes: list[str],
    note: str | None,
    baseline_seconds: int,
    review_seconds: int,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> ClaimExtractionReview:
    reviewer = reviewer.strip()
    if not 2 <= len(reviewer) <= 255:
        raise ClaimExtractionError("Reviewer name must be between 2 and 255 characters")
    if reviewer.casefold().startswith("model:"):
        raise ClaimExtractionError("A model cannot review a claim proposal")
    normalized_reason_codes = _normalized_codes(reason_codes)
    if baseline_seconds <= 0 or review_seconds <= 0:
        raise ClaimExtractionError("Review timings must be positive")
    if (
        decision in {ClaimExtractionDecision.EDIT, ClaimExtractionDecision.REJECT}
        and not normalized_reason_codes
    ):
        raise ClaimExtractionError("Edited and rejected proposals require a reason code")
    if decision in {ClaimExtractionDecision.ACCEPT, ClaimExtractionDecision.EDIT} and (
        event_id is None or claim_state is None
    ):
        raise ClaimExtractionError("Accepted and edited proposals require an event and claim state")
    if decision == ClaimExtractionDecision.EDIT and edited_claim is None:
        raise ClaimExtractionError("Edited proposals require final claim content")
    if decision != ClaimExtractionDecision.EDIT and edited_claim is not None:
        raise ClaimExtractionError("Only edited proposals may supply final claim content")
    proposal = session.scalar(
        select(ClaimExtractionProposal)
        .where(ClaimExtractionProposal.id == proposal_id)
        .with_for_update()
    )
    if proposal is None:
        raise LookupError("Claim extraction proposal not found")
    if proposal.status != ClaimExtractionProposalStatus.PENDING:
        raise ClaimExtractionError(f"Claim proposal is already {proposal.status.value}")
    run = session.get(ClaimExtractionRun, proposal.run_id)
    if run is None or run.status != ClaimExtractionRunStatus.SUCCEEDED:
        raise ClaimExtractionError("Claim proposal does not belong to a successful extraction run")
    if event_id is not None and session.get(Event, event_id) is None:
        raise LookupError("Event not found")
    if edited_claim is not None:
        record = session.get(SourceRecord, proposal.source_record_id)
        if record is None:
            raise ClaimExtractionError("The proposal source record no longer exists")
        if edited_claim.source_sentence_quote not in record.extracted_text:
            raise ClaimExtractionError("Edited source quote is not present in the source record")
    final_claim = (
        edited_claim.model_dump(mode="json")
        if edited_claim
        else {
            "text": proposal.text,
            "claimant": proposal.claimant,
            "occurred_time": proposal.occurred_time.isoformat() if proposal.occurred_time else None,
            "location": proposal.location_json,
            "quantities": proposal.quantities_json,
            "hedging_language": proposal.hedging_language,
            "source_sentence_quote": proposal.source_sentence_quote,
        }
        if decision == ClaimExtractionDecision.ACCEPT
        else None
    )
    proposal.status = {
        ClaimExtractionDecision.ACCEPT: ClaimExtractionProposalStatus.ACCEPTED,
        ClaimExtractionDecision.EDIT: ClaimExtractionProposalStatus.EDITED,
        ClaimExtractionDecision.REJECT: ClaimExtractionProposalStatus.REJECTED,
    }[decision]
    reviewed_at = now or datetime.now(UTC)
    resulting_claim: Claim | None = None
    active = settings or get_settings()
    rules = load_rules(active)
    effective_mode = (
        "production" if run.mode == "production" and rules.mode == "production" else "shadow"
    )
    if decision != ClaimExtractionDecision.REJECT and effective_mode == "production":
        if not _latest_graduation(
            session,
            language=run.language,
            prompt_version=run.prompt_version,
            model_version=run.model_version,
        ):
            raise ClaimExtractionError(
                "This language and prompt have not graduated from shadow mode"
            )
        assert final_claim is not None and event_id is not None and claim_state is not None
        occurred = final_claim.get("occurred_time")
        quantities = final_claim.get("quantities")
        if not isinstance(quantities, list):
            raise ClaimExtractionError("Final claim quantities must be a list")
        resulting_claim = Claim(
            event_id=event_id,
            text=str(final_claim["text"]),
            claimant=(str(final_claim["claimant"]) if final_claim.get("claimant") else None),
            claim_state=claim_state,
            occurred_at=datetime.fromisoformat(occurred) if isinstance(occurred, str) else occurred,
            quantity_json=quantities,
            proposed_by=f"model:{run.model_version}",
            reviewed_by=reviewer,
            reviewed_at=reviewed_at,
            sensitivity_flags=[],
            first_seen_at=reviewed_at,
        )
        session.add(resulting_claim)
        session.flush()
    review = ClaimExtractionReview(
        proposal_id=proposal.id,
        decision=decision,
        event_id=event_id,
        reviewer=reviewer,
        claim_state=claim_state,
        final_claim_json=final_claim,
        reason_codes=normalized_reason_codes,
        note=note,
        baseline_seconds=baseline_seconds,
        review_seconds=review_seconds,
        mode=effective_mode,
        resulting_claim_id=resulting_claim.id if resulting_claim else None,
        reviewed_at=reviewed_at,
    )
    session.add(review)
    session.flush()
    session.add(
        AuditLog(
            actor=reviewer,
            action=f"claim_extraction.proposal_{decision.value}",
            entity="claim_extraction_proposal",
            entity_id=proposal.id,
            payload_json={
                "review_id": str(review.id),
                "reason_codes": review.reason_codes,
                "mode": effective_mode,
                "resulting_claim_id": str(resulting_claim.id) if resulting_claim else None,
            },
        )
    )
    session.commit()
    session.refresh(review)
    return review


def record_qa(
    session: Session,
    *,
    review_id: UUID,
    evaluator: str,
    error_found: bool,
    error_codes: list[str],
    note: str | None,
    now: datetime | None = None,
) -> ClaimExtractionQA:
    evaluator = evaluator.strip()
    if not 2 <= len(evaluator) <= 255:
        raise ClaimExtractionError("QA evaluator name must be between 2 and 255 characters")
    if evaluator.casefold().startswith("model:"):
        raise ClaimExtractionError("A model cannot perform extraction QA")
    normalized_error_codes = _normalized_codes(error_codes)
    if error_found != bool(normalized_error_codes):
        raise ClaimExtractionError("QA errors require codes, and clean QA cannot have error codes")
    review = session.get(ClaimExtractionReview, review_id)
    if review is None:
        raise LookupError("Claim extraction review not found")
    if evaluator.casefold() == review.reviewer.strip().casefold():
        raise ClaimExtractionError("Extraction QA must be performed by a different human")
    if session.scalar(select(ClaimExtractionQA.id).where(ClaimExtractionQA.review_id == review_id)):
        raise ClaimExtractionError("This extraction review already has QA")
    qa = ClaimExtractionQA(
        review_id=review_id,
        evaluator=evaluator,
        error_found=error_found,
        error_codes=normalized_error_codes,
        note=note,
        evaluated_at=now or datetime.now(UTC),
    )
    session.add(qa)
    session.flush()
    session.add(
        AuditLog(
            actor=evaluator,
            action="claim_extraction.qa_recorded",
            entity="claim_extraction_review",
            entity_id=review_id,
            payload_json={
                "qa_id": str(qa.id),
                "error_found": error_found,
                "error_codes": qa.error_codes,
            },
        )
    )
    session.commit()
    session.refresh(qa)
    return qa


def evaluate_component(
    session: Session,
    *,
    language: str,
    prompt_version: str = PROMPT_VERSION,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> tuple[PipelineEvaluation, ClaimExtractionMetrics]:
    active = settings or get_settings()
    rules = load_rules(active)
    model_version = active.claim_extraction_model
    review_rows = (
        session.execute(
            select(ClaimExtractionReview)
            .join(
                ClaimExtractionProposal,
                ClaimExtractionProposal.id == ClaimExtractionReview.proposal_id,
            )
            .join(ClaimExtractionRun, ClaimExtractionRun.id == ClaimExtractionProposal.run_id)
            .where(
                ClaimExtractionRun.language == language.casefold(),
                ClaimExtractionRun.prompt_version == prompt_version,
                ClaimExtractionRun.model_version == model_version,
            )
        )
        .scalars()
        .all()
    )
    if not review_rows:
        raise LookupError("No extraction reviews for this language and prompt")
    review_ids = [review.id for review in review_rows]
    qa_rows = list(
        session.scalars(
            select(ClaimExtractionQA).where(ClaimExtractionQA.review_id.in_(review_ids))
        ).all()
    )
    dates = [review.reviewed_at for review in review_rows]
    shadow_days = (max(dates) - min(dates)).total_seconds() / 86_400
    baseline_total = sum(review.baseline_seconds for review in review_rows)
    review_total = sum(review.review_seconds for review in review_rows)
    time_reduction = max(-1.0, min(1.0, 1 - (review_total / baseline_total)))
    error_rate = sum(qa.error_found for qa in qa_rows) / len(qa_rows) if qa_rows else 0.0
    baseline_error = rules.baseline_error_rate_by_language.get(language.casefold())
    graduated = bool(
        len(review_rows) >= rules.minimum_reviewed_per_language
        and len(qa_rows) >= rules.minimum_qa_per_language
        and shadow_days >= rules.minimum_shadow_days
        and time_reduction * 100 >= rules.minimum_time_reduction_percent
        and baseline_error is not None
        and error_rate <= baseline_error
    )
    evaluated_at = now or datetime.now(UTC)
    evaluation = PipelineEvaluation(
        component=COMPONENT,
        component_version=_evaluation_version(prompt_version, model_version),
        language=language.casefold(),
        metric_name="time_reduction_ratio",
        metric_value=time_reduction,
        sample_size=len(review_rows),
        evaluated_at=evaluated_at,
        graduated=graduated,
    )
    metrics = ClaimExtractionMetrics(
        language=language.casefold(),
        prompt_version=prompt_version,
        model_version=model_version,
        reviewed=len(review_rows),
        qa_reviewed=len(qa_rows),
        shadow_days=shadow_days,
        time_reduction_percent=time_reduction * 100,
        error_rate=error_rate,
        baseline_error_rate=baseline_error,
        graduated=graduated,
    )
    session.add(evaluation)
    session.flush()
    session.add(
        AuditLog(
            actor="claim-extraction-evaluator",
            action="claim_extraction.component_evaluated",
            entity="pipeline_evaluation",
            entity_id=evaluation.id,
            payload_json={**metrics.__dict__, "rules_version": rules.version},
        )
    )
    session.commit()
    session.refresh(evaluation)
    return evaluation, metrics


def weekly_reason_report(
    session: Session, *, language: str | None = None, prompt_version: str = PROMPT_VERSION
) -> dict[str, Any]:
    query = (
        select(
            ClaimExtractionReview.reason_codes,
            ClaimExtractionReview.decision,
            ClaimExtractionRun.language,
        )
        .join(
            ClaimExtractionProposal, ClaimExtractionProposal.id == ClaimExtractionReview.proposal_id
        )
        .join(ClaimExtractionRun, ClaimExtractionRun.id == ClaimExtractionProposal.run_id)
        .where(ClaimExtractionRun.prompt_version == prompt_version)
    )
    if language:
        query = query.where(ClaimExtractionRun.language == language.casefold())
    rows = session.execute(query).all()
    codes: Counter[str] = Counter()
    decisions: Counter[str] = Counter()
    languages: Counter[str] = Counter()
    for reason_codes, decision, row_language in rows:
        codes.update(reason_codes or [])
        decisions[decision.value] += 1
        languages[row_language] += 1
    return {
        "prompt_version": prompt_version,
        "reviewed": len(rows),
        "reason_codes": dict(codes.most_common()),
        "decisions": dict(decisions),
        "languages": dict(languages),
    }


def _evaluation_version(prompt_version: str, model_version: str) -> str:
    model_hash = hashlib.sha256(model_version.encode("utf-8")).hexdigest()[:16]
    return f"{prompt_version}:{model_hash}"[:64]


def _latest_graduation(
    session: Session, *, language: str, prompt_version: str, model_version: str
) -> bool:
    value = session.scalar(
        select(PipelineEvaluation.graduated)
        .where(
            PipelineEvaluation.component == COMPONENT,
            PipelineEvaluation.component_version
            == _evaluation_version(prompt_version, model_version),
            PipelineEvaluation.language == language,
        )
        .order_by(PipelineEvaluation.evaluated_at.desc())
        .limit(1)
    )
    return value is True

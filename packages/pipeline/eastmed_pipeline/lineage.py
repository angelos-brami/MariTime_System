from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from eastmed_schema.enums import (
    LineageProposalStatus,
    LineageReviewDecision,
)
from eastmed_schema.models import (
    AuditLog,
    LineageProposal,
    LineageReview,
    LineageRoot,
    PipelineEvaluation,
    Source,
    SourceRecord,
    SourceRecordEmbedding,
    SourceRecordLineage,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

REGISTRY_COMPONENT_VERSION = "registry-lineage-v1"
CREDIT_COMPONENT_VERSION = "credit-line-v1"
LEXICAL_COMPONENT_VERSION = "lexical-near-dup-v1"
MINHASH_PERMUTATIONS = 64
MINIMUM_TOKEN_COUNT = 20
COSINE_THRESHOLD = 0.92
MINHASH_THRESHOLD = 0.75

TOKEN_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)
CREDIT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Reuters", re.compile(r"\breuters\b", re.IGNORECASE)),
    ("Associated Press", re.compile(r"\b(?:associated press|ap)\b", re.IGNORECASE)),
    (
        "Agence France-Presse",
        re.compile(r"\b(?:agence france[- ]presse|afp)\b", re.IGNORECASE),
    ),
    (
        "Athens-Macedonian News Agency",
        re.compile(r"(?:πηγ[ήη]\s*:\s*)?απε[-–— ]?μπε", re.IGNORECASE),
    ),
    ("Anadolu Agency", re.compile(r"\b(?:anadolu agency|aa)\b", re.IGNORECASE)),
)


class LineageReviewError(ValueError):
    pass


@dataclass(frozen=True)
class TextFeatures:
    tokens: tuple[str, ...]
    counts: Counter[str]
    minhash: tuple[int, ...]


@dataclass(frozen=True)
class NearDuplicateScore:
    cosine: float
    minhash: float

    @property
    def qualifies(self) -> bool:
        return self.cosine >= COSINE_THRESHOLD and self.minhash >= MINHASH_THRESHOLD


def normalize_tokens(text: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return tuple(TOKEN_PATTERN.findall(normalized))


def _shingles(tokens: tuple[str, ...], width: int = 5) -> tuple[str, ...]:
    if not tokens:
        return ()
    if len(tokens) < width:
        return (" ".join(tokens),)
    return tuple(
        " ".join(tokens[index : index + width]) for index in range(len(tokens) - width + 1)
    )


def minhash_signature(tokens: tuple[str, ...]) -> tuple[int, ...]:
    shingles = _shingles(tokens)
    if not shingles:
        return ()
    signature: list[int] = []
    for seed in range(MINHASH_PERMUTATIONS):
        prefix = seed.to_bytes(2, "big")
        signature.append(
            min(
                int.from_bytes(
                    hashlib.blake2b(prefix + item.encode("utf-8"), digest_size=8).digest(),
                    "big",
                )
                for item in shingles
            )
        )
    return tuple(signature)


def build_text_features(text: str) -> TextFeatures:
    tokens = normalize_tokens(text)
    return TextFeatures(tokens=tokens, counts=Counter(tokens), minhash=minhash_signature(tokens))


def cosine_similarity(left: Counter[str], right: Counter[str]) -> float:
    if not left or not right:
        return 0.0
    dot = sum(value * right.get(token, 0) for token, value in left.items())
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


def vector_cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


def minhash_similarity(left: tuple[int, ...], right: tuple[int, ...]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    return sum(a == b for a, b in zip(left, right, strict=True)) / len(left)


def score_near_duplicate(left: TextFeatures, right: TextFeatures) -> NearDuplicateScore:
    return NearDuplicateScore(
        cosine=cosine_similarity(left.counts, right.counts),
        minhash=minhash_similarity(left.minhash, right.minhash),
    )


def parse_credit_lines(text: str) -> tuple[str, ...]:
    return tuple(name for name, pattern in CREDIT_PATTERNS if pattern.search(text))


def _proposal_key(
    *,
    subject_record_id: UUID,
    candidate_record_id: UUID | None,
    origin_source_id: UUID | None,
    origin_name: str | None,
    component_version: str,
) -> str:
    canonical = "|".join(
        (
            str(subject_record_id),
            str(candidate_record_id or ""),
            str(origin_source_id or ""),
            origin_name or "",
            component_version,
        )
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _add_proposal(
    session: Session,
    *,
    subject_record_id: UUID,
    candidate_record_id: UUID | None = None,
    origin_source_id: UUID | None = None,
    origin_name: str | None = None,
    score: float,
    signals: dict[str, Any],
    component_version: str,
) -> bool:
    proposal_key = _proposal_key(
        subject_record_id=subject_record_id,
        candidate_record_id=candidate_record_id,
        origin_source_id=origin_source_id,
        origin_name=origin_name,
        component_version=component_version,
    )
    if session.scalar(
        select(LineageProposal.id).where(LineageProposal.proposal_key == proposal_key)
    ):
        return False
    session.add(
        LineageProposal(
            proposal_key=proposal_key,
            subject_record_id=subject_record_id,
            candidate_record_id=candidate_record_id,
            proposed_origin_source_id=origin_source_id,
            proposed_origin_name=origin_name,
            score=max(0.0, min(1.0, score)),
            signals_json=signals,
            component_version=component_version,
            status=LineageProposalStatus.PENDING,
        )
    )
    return True


def _record_time(record: SourceRecord) -> datetime:
    return record.published_at or record.fetched_at


def _match_origin_source(sources: list[Source], canonical_name: str) -> Source | None:
    needle = canonical_name.casefold()
    for source in sources:
        haystack = " ".join(
            part for part in (source.name, source.legal_entity or "") if part
        ).casefold()
        if needle in haystack or haystack in needle:
            return source
    return None


def generate_lineage_proposals(
    session: Session,
    *,
    now: datetime | None = None,
    window_hours: int = 72,
    record_limit: int = 500,
    embedding_model_version: str | None = None,
) -> int:
    current = now or datetime.now(UTC)
    cutoff = current - timedelta(hours=window_hours)
    timestamp = func.coalesce(SourceRecord.published_at, SourceRecord.fetched_at)
    records = list(
        session.scalars(
            select(SourceRecord)
            .where(timestamp >= cutoff)
            .order_by(timestamp.desc())
            .limit(record_limit)
        ).all()
    )
    records.sort(key=_record_time)
    sources = list(session.scalars(select(Source)).all())
    source_by_id = {source.id: source for source in sources}
    eligible = [
        record
        for record in records
        if not record.security_scan.get("quarantined")
        and not record.security_scan.get("injection_suspected")
    ]
    embedding_by_id: dict[UUID, list[float]] = {}
    if embedding_model_version and eligible:
        embedding_rows = session.scalars(
            select(SourceRecordEmbedding).where(
                SourceRecordEmbedding.model_version == embedding_model_version,
                SourceRecordEmbedding.source_record_id.in_([record.id for record in eligible]),
            )
        ).all()
        embedding_by_id = {
            row.source_record_id: [float(value) for value in row.embedding]
            for row in embedding_rows
        }
    created = 0

    for record in eligible:
        source = source_by_id.get(record.source_id)
        if source and source.syndicates_from_id:
            origin = source_by_id.get(source.syndicates_from_id)
            created += _add_proposal(
                session,
                subject_record_id=record.id,
                origin_source_id=source.syndicates_from_id,
                origin_name=origin.name if origin else None,
                score=1.0,
                signals={"registry_syndicates_from": str(source.syndicates_from_id)},
                component_version=REGISTRY_COMPONENT_VERSION,
            )
        for credit_name in parse_credit_lines(f"{record.title or ''}\n{record.extracted_text}"):
            origin = _match_origin_source(sources, credit_name)
            created += _add_proposal(
                session,
                subject_record_id=record.id,
                origin_source_id=origin.id if origin else None,
                origin_name=credit_name,
                score=0.99,
                signals={"credit_line": credit_name},
                component_version=CREDIT_COMPONENT_VERSION,
            )

    features = {record.id: build_text_features(record.extracted_text) for record in eligible}
    for index, subject in enumerate(eligible):
        subject_features = features[subject.id]
        if len(subject_features.tokens) < MINIMUM_TOKEN_COUNT:
            continue
        candidates: list[tuple[float, SourceRecord, NearDuplicateScore, str, str]] = []
        for candidate in eligible[:index]:
            if candidate.source_id == subject.source_id:
                continue
            if _record_time(subject) - _record_time(candidate) > timedelta(hours=window_hours):
                continue
            candidate_features = features[candidate.id]
            if len(candidate_features.tokens) < MINIMUM_TOKEN_COUNT:
                continue
            length_ratio = min(len(subject_features.tokens), len(candidate_features.tokens)) / max(
                len(subject_features.tokens), len(candidate_features.tokens)
            )
            if length_ratio < 0.7:
                continue
            subject_embedding = embedding_by_id.get(subject.id)
            candidate_embedding = embedding_by_id.get(candidate.id)
            if subject_embedding is not None and candidate_embedding is not None:
                assert embedding_model_version is not None
                score = NearDuplicateScore(
                    cosine=vector_cosine_similarity(subject_embedding, candidate_embedding),
                    minhash=minhash_similarity(
                        subject_features.minhash, candidate_features.minhash
                    ),
                )
                component_version = f"{embedding_model_version}-near-dup-v1"[:64]
                semantic_signal = "embedding_cosine"
            else:
                score = score_near_duplicate(subject_features, candidate_features)
                component_version = LEXICAL_COMPONENT_VERSION
                semantic_signal = "token_cosine"
            if score.qualifies:
                candidates.append(
                    (score.cosine, candidate, score, component_version, semantic_signal)
                )
        for _, candidate, score, component_version, semantic_signal in sorted(
            candidates, reverse=True, key=lambda item: item[0]
        )[:3]:
            created += _add_proposal(
                session,
                subject_record_id=subject.id,
                candidate_record_id=candidate.id,
                score=score.cosine,
                signals={
                    semantic_signal: round(score.cosine, 6),
                    "minhash_similarity": round(score.minhash, 6),
                    "window_hours": window_hours,
                    "shadow_only": True,
                    "embedding_model_version": (
                        embedding_model_version if semantic_signal == "embedding_cosine" else None
                    ),
                },
                component_version=component_version,
            )

    if created:
        session.add(
            AuditLog(
                actor="lineage-shadow-job",
                action="lineage.proposals_generated",
                entity="lineage_proposal_batch",
                payload_json={
                    "created": created,
                    "records_scanned": len(eligible),
                    "window_hours": window_hours,
                },
            )
        )
    session.commit()
    return created


def _lineage_mapping(session: Session, record_id: UUID) -> SourceRecordLineage | None:
    return session.get(SourceRecordLineage, record_id)


def _assign_to_root(
    session: Session,
    *,
    record_id: UUID,
    root_id: UUID,
    proposal_id: UUID,
    reviewer: str,
    reviewed_at: datetime,
) -> None:
    existing = _lineage_mapping(session, record_id)
    if existing:
        if existing.lineage_root_id != root_id:
            raise LineageReviewError("Record already belongs to a different lineage root")
        return
    session.add(
        SourceRecordLineage(
            source_record_id=record_id,
            lineage_root_id=root_id,
            proposal_id=proposal_id,
            assigned_by=reviewer,
            assigned_at=reviewed_at,
        )
    )


def review_lineage_proposal(
    session: Session,
    *,
    proposal_id: UUID,
    decision: LineageReviewDecision,
    reviewer: str,
    reason: str | None = None,
    now: datetime | None = None,
) -> LineageReview:
    if reviewer.casefold().startswith("model:"):
        raise LineageReviewError("A model cannot review a lineage proposal")
    if decision == LineageReviewDecision.REJECT and not reason:
        raise LineageReviewError("Rejected lineage proposals require a reason")
    proposal = session.scalar(
        select(LineageProposal).where(LineageProposal.id == proposal_id).with_for_update()
    )
    if proposal is None:
        raise LookupError("Lineage proposal not found")
    if proposal.status != LineageProposalStatus.PENDING:
        raise LineageReviewError(f"Lineage proposal is already {proposal.status.value}")

    reviewed_at = now or datetime.now(UTC)
    if decision == LineageReviewDecision.ACCEPT:
        subject = session.get(SourceRecord, proposal.subject_record_id)
        candidate = (
            session.get(SourceRecord, proposal.candidate_record_id)
            if proposal.candidate_record_id
            else None
        )
        if subject is None or (proposal.candidate_record_id and candidate is None):
            raise LineageReviewError("A proposed source record no longer exists")
        subject_mapping = _lineage_mapping(session, subject.id)
        candidate_mapping = _lineage_mapping(session, candidate.id) if candidate else None
        if (
            subject_mapping
            and candidate_mapping
            and subject_mapping.lineage_root_id != candidate_mapping.lineage_root_id
        ):
            raise LineageReviewError("Proposal would merge two established lineage roots")

        existing_mapping = subject_mapping or candidate_mapping
        if existing_mapping:
            root_id = existing_mapping.lineage_root_id
        else:
            origin_name = proposal.proposed_origin_name
            description = origin_name or f"Lineage proposed from record {subject.id}"
            origin_record = candidate or subject
            root = LineageRoot(
                description=f"{description} — {subject.title or subject.id}",
                origin_source_id=proposal.proposed_origin_source_id or origin_record.source_id,
                origin_url=candidate.url if candidate else None,
                first_seen_at=min(
                    _record_time(subject),
                    _record_time(candidate) if candidate else _record_time(subject),
                ),
            )
            session.add(root)
            session.flush()
            root_id = root.id

        _assign_to_root(
            session,
            record_id=subject.id,
            root_id=root_id,
            proposal_id=proposal.id,
            reviewer=reviewer,
            reviewed_at=reviewed_at,
        )
        if candidate:
            _assign_to_root(
                session,
                record_id=candidate.id,
                root_id=root_id,
                proposal_id=proposal.id,
                reviewer=reviewer,
                reviewed_at=reviewed_at,
            )
        proposal.status = LineageProposalStatus.ACCEPTED
    else:
        proposal.status = LineageProposalStatus.REJECTED

    review = LineageReview(
        proposal_id=proposal.id,
        decision=decision,
        reviewer=reviewer,
        reason=reason,
        reviewed_at=reviewed_at,
    )
    session.add(review)
    session.add(
        AuditLog(
            actor=reviewer,
            action=f"lineage.proposal_{decision.value}ed",
            entity="lineage_proposal",
            entity_id=proposal.id,
            payload_json={
                "decision": decision.value,
                "reason": reason,
                "component_version": proposal.component_version,
            },
        )
    )
    session.commit()
    session.refresh(review)
    return review


def lineage_component_graduates(*, precision: float, sample_size: int, shadow_days: float) -> bool:
    return sample_size >= 100 and precision >= 0.97 and shadow_days >= 21


def evaluate_lineage_component(
    session: Session,
    *,
    component_version: str,
    now: datetime | None = None,
) -> PipelineEvaluation | None:
    rows = session.execute(
        select(LineageReview.decision, LineageReview.reviewed_at)
        .join(LineageProposal, LineageProposal.id == LineageReview.proposal_id)
        .where(LineageProposal.component_version == component_version)
    ).all()
    if not rows:
        return None
    reviewed_at = [row.reviewed_at for row in rows]
    accepted = sum(row.decision == LineageReviewDecision.ACCEPT for row in rows)
    precision = accepted / len(rows)
    shadow_days = (max(reviewed_at) - min(reviewed_at)).total_seconds() / 86400
    graduated = lineage_component_graduates(
        precision=precision,
        sample_size=len(rows),
        shadow_days=shadow_days,
    )
    evaluation = PipelineEvaluation(
        component="lineage",
        component_version=component_version,
        language="all",
        metric_name="analyst_acceptance_precision",
        metric_value=precision,
        sample_size=len(rows),
        evaluated_at=now or datetime.now(UTC),
        graduated=graduated,
    )
    session.add(evaluation)
    session.flush()
    session.add(
        AuditLog(
            actor="lineage-evaluator",
            action="lineage.component_evaluated",
            entity="pipeline_evaluation",
            entity_id=evaluation.id,
            payload_json={
                "component_version": component_version,
                "precision": precision,
                "sample_size": len(rows),
                "shadow_days": shadow_days,
                "graduated": graduated,
            },
        )
    )
    session.commit()
    session.refresh(evaluation)
    return evaluation

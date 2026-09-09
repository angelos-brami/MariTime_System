from __future__ import annotations

import math
from typing import Any, Protocol

from eastmed_schema.models import AuditLog, SourceRecord, SourceRecordEmbedding
from eastmed_shared import Settings, get_settings
from sqlalchemy import select
from sqlalchemy.orm import Session

EMBEDDING_DIMENSIONS = 768


class EmbeddingProvider(Protocol):
    model_version: str

    def encode(self, texts: list[str]) -> list[list[float]]: ...


class SentenceTransformerEmbeddingProvider:
    def __init__(self, *, model_name: str, batch_size: int = 16) -> None:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "Install the embeddings extra: pip install -e '.[embeddings]'"
            ) from exc
        self.model_version = model_name
        self._batch_size = max(1, batch_size)
        self._model: Any = SentenceTransformer(model_name)

    def encode(self, texts: list[str]) -> list[list[float]]:
        # E5's model card requires the query prefix for symmetric similarity work.
        encoded = self._model.encode(
            [f"query: {text}" for text in texts],
            batch_size=self._batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        values = encoded.tolist() if hasattr(encoded, "tolist") else encoded
        return [[float(item) for item in row] for row in values]


def build_embedding_provider(settings: Settings | None = None) -> EmbeddingProvider:
    active = settings or get_settings()
    return SentenceTransformerEmbeddingProvider(
        model_name=active.embedding_model_name,
        batch_size=active.embedding_batch_size,
    )


def _validate_embedding(vector: list[float]) -> None:
    if len(vector) != EMBEDDING_DIMENSIONS:
        raise ValueError(f"Embedding has {len(vector)} dimensions; expected {EMBEDDING_DIMENSIONS}")
    if not all(math.isfinite(value) for value in vector):
        raise ValueError("Embedding contains non-finite values")


def generate_missing_embeddings(
    session: Session,
    *,
    provider: EmbeddingProvider,
    limit: int = 100,
) -> int:
    existing = select(SourceRecordEmbedding.source_record_id).where(
        SourceRecordEmbedding.model_version == provider.model_version
    )
    records = list(
        session.scalars(
            select(SourceRecord)
            .where(SourceRecord.id.not_in(existing))
            .order_by(SourceRecord.fetched_at)
            .limit(limit)
        ).all()
    )
    eligible = [
        record
        for record in records
        if not record.security_scan.get("quarantined")
        and not record.security_scan.get("injection_suspected")
    ]
    if not eligible:
        return 0
    texts = [f"{record.title or ''}\n{record.extracted_text}"[:20_000] for record in eligible]
    vectors = provider.encode(texts)
    if len(vectors) != len(eligible):
        raise ValueError("Embedding provider returned the wrong number of vectors")
    for record, vector in zip(eligible, vectors, strict=True):
        _validate_embedding(vector)
        session.add(
            SourceRecordEmbedding(
                source_record_id=record.id,
                model_version=provider.model_version,
                embedding=vector,
            )
        )
    session.add(
        AuditLog(
            actor="embedding-worker",
            action="source_records.embedded",
            entity="source_record_embedding_batch",
            payload_json={
                "model_version": provider.model_version,
                "count": len(eligible),
                "dimensions": EMBEDDING_DIMENSIONS,
            },
        )
    )
    session.commit()
    return len(eligible)

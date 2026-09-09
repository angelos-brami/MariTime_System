import pytest
from eastmed_pipeline.embeddings import (
    EMBEDDING_DIMENSIONS,
    SentenceTransformerEmbeddingProvider,
    _validate_embedding,
)
from eastmed_pipeline.lineage import vector_cosine_similarity


class FakeSentenceTransformer:
    def __init__(self) -> None:
        self.inputs: list[str] = []

    def encode(self, texts: list[str], **kwargs: object) -> list[list[float]]:
        del kwargs
        self.inputs = texts
        return [[1.0, 0.0] for _ in texts]


def test_e5_provider_adds_symmetric_query_prefix() -> None:
    provider = object.__new__(SentenceTransformerEmbeddingProvider)
    provider.model_version = "test-e5"
    provider._batch_size = 2
    fake_model = FakeSentenceTransformer()
    provider._model = fake_model
    assert provider.encode(["λιμένας notice"]) == [[1.0, 0.0]]
    assert fake_model.inputs == ["query: λιμένας notice"]


def test_embedding_validation_enforces_vector_schema() -> None:
    _validate_embedding([0.0] * EMBEDDING_DIMENSIONS)
    with pytest.raises(ValueError, match="dimensions"):
        _validate_embedding([0.0, 1.0])
    invalid: list[float] = [0.0] * EMBEDDING_DIMENSIONS
    invalid[4] = float("nan")
    with pytest.raises(ValueError, match="non-finite"):
        _validate_embedding(invalid)


def test_vector_cosine_similarity() -> None:
    assert vector_cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert vector_cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

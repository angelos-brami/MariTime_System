from pathlib import Path

from eastmed_pipeline.snapshots import FileSnapshotStore


def test_snapshot_store_is_content_addressed_and_idempotent(tmp_path: Path) -> None:
    store = FileSnapshotStore(tmp_path)
    first = store.put(source_id="source-1", payload=b"immutable", suffix="json")
    second = store.put(source_id="source-1", payload=b"immutable", suffix="json")
    assert first == second
    assert (tmp_path / first).read_bytes() == b"immutable"

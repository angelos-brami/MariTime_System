import hashlib
from pathlib import Path
from typing import Protocol


class SnapshotStore(Protocol):
    def put(self, *, source_id: str, payload: bytes, suffix: str) -> str: ...


class FileSnapshotStore:
    """Local development store; production uses the same key shape in R2."""

    def __init__(self, root: Path = Path("data/snapshots")) -> None:
        self.root = root

    def put(self, *, source_id: str, payload: bytes, suffix: str) -> str:
        digest = hashlib.sha256(payload).hexdigest()
        relative = Path(source_id) / digest[:2] / f"{digest}.{suffix.lstrip('.')}"
        destination = self.root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            destination.write_bytes(payload)
        return relative.as_posix()

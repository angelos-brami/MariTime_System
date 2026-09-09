from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from eastmed_shared import get_settings
from eastmed_shared.postgres_cli import postgres_environment


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create an age-encrypted PostgreSQL backup")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--age-recipient",
        required=True,
        help="age public recipient; keep the private identity offline",
    )
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()
    pg_dump = shutil.which("pg_dump")
    age = shutil.which("age")
    if pg_dump is None:
        raise SystemExit("pg_dump is not installed")
    if age is None:
        raise SystemExit("age is not installed")
    settings = get_settings()
    try:
        pg_environment = postgres_environment(args.database_url or settings.database_url)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = output_dir / f"eastmed-{timestamp}.dump.age"
    checksum_path = destination.with_suffix(destination.suffix + ".sha256")
    if destination.exists() or checksum_path.exists():
        raise SystemExit("backup destination already exists")
    started = datetime.now(UTC)
    process_environment = {**os.environ, **pg_environment}
    with tempfile.TemporaryDirectory(prefix="eastmed-backup-", dir=output_dir) as temporary:
        plain_dump = Path(temporary) / "eastmed.dump"
        encrypted = Path(temporary) / "eastmed.dump.age"
        dump_result = subprocess.run(  # noqa: S603
            [
                pg_dump,
                "--format=custom",
                "--compress=9",
                "--no-owner",
                "--file",
                str(plain_dump),
            ],
            env=process_environment,
            capture_output=True,
            text=True,
            timeout=3600,
            check=False,
        )
        if dump_result.returncode != 0:
            raise SystemExit(f"pg_dump failed: {dump_result.stderr[-2000:]}")
        age_result = subprocess.run(  # noqa: S603
            [
                age,
                "--recipient",
                args.age_recipient,
                "--output",
                str(encrypted),
                str(plain_dump),
            ],
            capture_output=True,
            text=True,
            timeout=3600,
            check=False,
        )
        if age_result.returncode != 0:
            raise SystemExit(f"age encryption failed: {age_result.stderr[-2000:]}")
        os.replace(encrypted, destination)
    checksum = sha256(destination)
    checksum_path.write_text(f"{checksum}  {destination.name}\n", encoding="ascii")
    completed = datetime.now(UTC)
    print(
        json.dumps(
            {
                "backup": str(destination),
                "checksum_file": str(checksum_path),
                "sha256": checksum,
                "bytes": destination.stat().st_size,
                "elapsed_seconds": (completed - started).total_seconds(),
                "encrypted": True,
            },
            sort_keys=True,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

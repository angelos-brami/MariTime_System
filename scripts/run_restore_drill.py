from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
from eastmed_api.database import SessionLocal
from eastmed_api.hardening import record_operational_drill
from eastmed_schema.enums import DrillType
from eastmed_shared import get_settings
from eastmed_shared.postgres_cli import postgres_environment

REQUIRED_TABLES = (
    "sources",
    "source_records",
    "events",
    "event_versions",
    "claims",
    "evidence",
    "corrections",
    "correction_deliveries",
    "deliveries",
    "state_knowledge_reports",
    "account_api_keys",
    "whatsapp_webhook_receipts",
    "ttv_log",
    "emev_log",
    "operational_drills",
    "maritime_calendar_events",
    "maritime_calendar_event_versions",
    "ais_position_cache",
    "pipeline_evaluations",
    "subscription_metrics",
    "audit_log",
)


def postgres_url(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def safe_target(value: str, production_url: str) -> str:
    normalized = postgres_url(value)
    parsed = urlsplit(normalized)
    database = parsed.path.strip("/").casefold()
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname or not database:
        raise ValueError("target must be a PostgreSQL database URL")
    if "restore" not in database and "drill" not in database:
        raise ValueError("target database name must contain 'restore' or 'drill'")
    production = urlsplit(postgres_url(production_url))
    if (
        parsed.hostname == production.hostname
        and parsed.port == production.port
        and parsed.path == production.path
    ):
        raise ValueError("restore target must not be the configured application database")
    return normalized


def backup_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def publication_payload_hash(payload: dict[str, object]) -> str:
    canonical = {key: value for key, value in payload.items() if key != "content_hash"}
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def backup_created_at(path: Path, explicit: str | None) -> datetime | None:
    if explicit:
        try:
            parsed = datetime.fromisoformat(explicit.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("backup creation time must be an ISO-8601 timestamp") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("backup creation time must include a timezone")
        return parsed.astimezone(UTC)
    match = re.search(r"eastmed-(\d{8}T\d{6}Z)", path.name)
    if match is None:
        return None
    return datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)


def mutation_is_rejected(cursor: psycopg.Cursor[object], statement: str) -> bool:
    cursor.execute("SAVEPOINT eastmed_immutability_check")
    try:
        cursor.execute(statement)
    except psycopg.Error:
        cursor.execute("ROLLBACK TO SAVEPOINT eastmed_immutability_check")
        cursor.execute("RELEASE SAVEPOINT eastmed_immutability_check")
        return True
    cursor.execute("ROLLBACK TO SAVEPOINT eastmed_immutability_check")
    cursor.execute("RELEASE SAVEPOINT eastmed_immutability_check")
    return False


def redacted_target(value: str) -> str:
    parsed = urlsplit(value)
    host = parsed.hostname or "unknown"
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def pg_restore_invocation(
    executable: str, target_url: str, backup: Path
) -> tuple[list[str], dict[str, str]]:
    environment = postgres_environment(target_url)
    command = [
        executable,
        "--exit-on-error",
        "--clean",
        "--if-exists",
        "--no-owner",
        "--dbname",
        environment["PGDATABASE"],
        str(backup.resolve()),
    ]
    return command, environment


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument(
        "--expected-sha256",
        help="independently recorded SHA-256 of the decrypted dump",
    )
    parser.add_argument(
        "--backup-created-at",
        help="ISO-8601 recovery point; otherwise parsed from eastmed-YYYYMMDDTHHMMSSZ filename",
    )
    parser.add_argument("--target-url", required=True)
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--executed-by", default="platform-operator")
    args = parser.parse_args()
    settings = get_settings()
    if not args.backup.is_file():
        raise SystemExit("backup file does not exist")
    if args.expected_sha256 is not None and not re.fullmatch(
        r"[0-9a-fA-F]{64}", args.expected_sha256
    ):
        raise SystemExit("expected SHA-256 must contain exactly 64 hexadecimal characters")
    try:
        created_at = backup_created_at(args.backup, args.backup_created_at)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    actual_backup_hash = backup_hash(args.backup)
    checksum_verified = (
        actual_backup_hash == args.expected_sha256.casefold()
        if args.expected_sha256 is not None
        else None
    )
    try:
        target_url = safe_target(args.target_url, settings.database_url)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    pg_restore = shutil.which("pg_restore")
    if pg_restore is None:
        raise SystemExit("pg_restore is not installed")

    started = datetime.now(UTC)
    try:
        restore_command, pg_environment = pg_restore_invocation(pg_restore, target_url, args.backup)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    process = subprocess.run(  # noqa: S603
        restore_command,
        env={**os.environ, **pg_environment},
        capture_output=True,
        text=True,
        timeout=3600,
        check=False,
    )
    if process.returncode != 0:
        raise SystemExit(f"pg_restore failed: {process.stderr[-2000:]}")

    counts: dict[str, int] = {}
    with psycopg.connect(target_url) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT extname FROM pg_extension WHERE extname IN ('postgis', 'vector')")
        extensions = sorted(row[0] for row in cursor.fetchall())
        for table in REQUIRED_TABLES:
            cursor.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_tables "
                "WHERE schemaname='public' AND tablename=%s)",
                (table,),
            )
            exists_row = cursor.fetchone()
            if exists_row is None:
                raise SystemExit("restore verification query returned no result")
            if not exists_row[0]:
                raise SystemExit(f"restored database is missing required table {table}")
            cursor.execute(f'SELECT count(*) FROM "{table}"')  # noqa: S608
            count_row = cursor.fetchone()
            if count_row is None:
                raise SystemExit(f"restore count query returned no result for {table}")
            counts[table] = int(count_row[0])
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='audit_log_no_update_delete')"
        )
        audit_row = cursor.fetchone()
        if audit_row is None:
            raise SystemExit("audit trigger verification returned no result")
        audit_trigger_present = bool(audit_row[0])
        audit_update_rejected = mutation_is_rejected(
            cursor,
            "UPDATE audit_log SET payload_json = payload_json "
            "WHERE id = (SELECT id FROM audit_log ORDER BY created_at LIMIT 1)",
        )
        audit_delete_rejected = mutation_is_rejected(
            cursor,
            "DELETE FROM audit_log "
            "WHERE id = (SELECT id FROM audit_log ORDER BY created_at LIMIT 1)",
        )
        cursor.execute(
            "SELECT ev.id, ev.content_hash, al.payload_json "
            "FROM event_versions ev "
            "LEFT JOIN audit_log al ON al.entity = 'event_version' "
            "AND al.entity_id = ev.id AND al.action = 'event_version.published' "
            "ORDER BY ev.published_at DESC LIMIT 100"
        )
        hash_rows = cursor.fetchall()
        invalid_version_hashes = 0
        for _, stored_hash, payload in hash_rows:
            if (
                not isinstance(stored_hash, str)
                or not isinstance(payload, dict)
                or publication_payload_hash(payload) != stored_hash
            ):
                invalid_version_hashes += 1
    completed = datetime.now(UTC)
    result = {
        "target": redacted_target(target_url),
        "backup_sha256": actual_backup_hash,
        "checksum_verified": checksum_verified,
        "backup_created_at": created_at.isoformat() if created_at else None,
        "backup_age_seconds": (
            max(0.0, (started - created_at).total_seconds()) if created_at else None
        ),
        "restore_seconds": (completed - started).total_seconds(),
        "extensions": extensions,
        "row_counts": counts,
        "audit_trigger_present": audit_trigger_present,
        "audit_update_rejected": audit_update_rejected,
        "audit_delete_rejected": audit_delete_rejected,
        "version_hashes_sampled": len(hash_rows),
        "invalid_version_hashes": invalid_version_hashes,
    }
    result["passed"] = (
        {"postgis", "vector"}.issubset(extensions)
        and checksum_verified is True
        and created_at is not None
        and audit_trigger_present
        and audit_update_rejected
        and audit_delete_rejected
        and (counts["event_versions"] == 0 or len(hash_rows) > 0)
        and invalid_version_hashes == 0
    )
    if args.record:
        with SessionLocal() as session:
            drill = record_operational_drill(
                session,
                drill_type=DrillType.RESTORE,
                passed=bool(result["passed"]),
                started_at=started,
                completed_at=completed,
                executed_by=args.executed_by,
                environment=settings.environment,
                result=result,
            )
        result["drill_id"] = str(drill.id)
        result["evidence_hash"] = drill.evidence_hash
    print(json.dumps(result, sort_keys=True, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

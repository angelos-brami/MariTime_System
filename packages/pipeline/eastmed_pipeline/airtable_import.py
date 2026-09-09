from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx
from eastmed_api.database import SessionLocal
from eastmed_schema.enums import (
    AccessMethod,
    ClaimState,
    Corridor,
    Directness,
    EventStatus,
    EventType,
    RightsBasis,
    SourceTier,
    SourceType,
)
from eastmed_schema.models import (
    AuditLog,
    Claim,
    EMEVLog,
    Event,
    Evidence,
    ExternalRecordRef,
    LineageRoot,
    Source,
    SourceRecord,
)
from eastmed_shared import get_settings
from sqlalchemy import select
from sqlalchemy.orm import Session

from eastmed_pipeline.security_scan import scan_untrusted_text
from eastmed_pipeline.triage import ensure_triage_item


class AirtableImportError(RuntimeError):
    pass


@dataclass
class ImportStats:
    created: int = 0
    unchanged: int = 0
    updated: int = 0
    skipped: int = 0

    def add(self, other: ImportStats) -> None:
        self.created += other.created
        self.unchanged += other.unchanged
        self.updated += other.updated
        self.skipped += other.skipped


class AirtableClient:
    def __init__(self, *, token: str, base_id: str, timeout_seconds: float = 30.0) -> None:
        self._base_id = base_id
        self._client = httpx.Client(
            base_url=f"https://api.airtable.com/v0/{base_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout_seconds,
        )

    def close(self) -> None:
        self._client.close()

    def records(self, table_name: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        offset: str | None = None
        while True:
            params = {"pageSize": "100"}
            if offset:
                params["offset"] = offset
            response = self._client.get(f"/{quote(table_name, safe='')}", params=params)
            if response.status_code == 404:
                raise AirtableImportError(f"Airtable table {table_name!r} was not found")
            response.raise_for_status()
            payload = response.json()
            records.extend(payload.get("records", []))
            offset = payload.get("offset")
            if not offset:
                return records


def _hash_payload(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _text(fields: dict[str, Any], *names: str) -> str | None:
    for name in names:
        value = fields.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _links(fields: dict[str, Any], name: str) -> list[str]:
    value = fields.get(name, [])
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)] if value else []


def _datetime(value: Any) -> datetime | None:
    if value in {None, ""}:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)


def _date(value: Any) -> date | None:
    parsed = _datetime(value)
    return parsed.date() if parsed else None


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _country(value: Any) -> str | None:
    normalized = str(value or "").strip().upper()
    return normalized if len(normalized) == 2 else None


def _slug(value: str) -> str:
    normalized = "".join(char.lower() if char.isalnum() else "-" for char in value)
    return "-".join(part for part in normalized.split("-") if part)[:220]


def _enum_value[EnumT: StrEnum](value: Any, enum_type: type[EnumT], *, default: EnumT) -> EnumT:
    normalized = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    for item in enum_type:
        if item.value.lower().replace("-", "_") == normalized or item.name.lower() == normalized:
            return item
    return default


def _claim_state(value: Any) -> ClaimState:
    normalized = str(value or "").strip().lower()
    if normalized.startswith("confirmed"):
        return ClaimState.CONFIRMED
    if "corroborated" in normalized:
        return ClaimState.CORROBORATED_2X
    if "single" in normalized and "official" in normalized:
        return ClaimState.SINGLE_OFFICIAL
    if normalized.startswith("reported"):
        return ClaimState.REPORTED
    if "disputed" in normalized:
        return ClaimState.DISPUTED
    return ClaimState.UNVERIFIED


def _source_type(value: Any) -> SourceType:
    return _enum_value(value, SourceType, default=SourceType.OTHER)


def _rights_basis(value: Any) -> RightsBasis:
    return _enum_value(value, RightsBasis, default=RightsBasis.DISCOVERY_ONLY)


class AirtableImporter:
    TABLES = ("SOURCES", "EVENTS", "CLAIMS", "EVIDENCE", "EMEV-LOG")

    def __init__(self, session: Session, *, base_id: str) -> None:
        self.session = session
        self.base_id = base_id
        self.ids: dict[tuple[str, str], UUID] = {}

    def _existing_ref(self, table: str, external_id: str) -> ExternalRecordRef | None:
        ref = self.session.scalar(
            select(ExternalRecordRef).where(
                ExternalRecordRef.provider == "airtable",
                ExternalRecordRef.container_id == self.base_id,
                ExternalRecordRef.table_name == table,
                ExternalRecordRef.external_id == external_id,
            )
        )
        if ref:
            self.ids[(table, external_id)] = ref.entity_id
        return ref

    def _record_ref(
        self,
        *,
        table: str,
        external_id: str,
        entity: str,
        entity_id: UUID,
        payload_hash: str,
    ) -> None:
        self.ids[(table, external_id)] = entity_id
        existing = self._existing_ref(table, external_id)
        if existing:
            existing.entity = entity
            existing.entity_id = entity_id
            existing.imported_at = datetime.now(UTC)
            existing.payload_hash = payload_hash
        else:
            self.session.add(
                ExternalRecordRef(
                    provider="airtable",
                    container_id=self.base_id,
                    table_name=table,
                    external_id=external_id,
                    entity=entity,
                    entity_id=entity_id,
                    imported_at=datetime.now(UTC),
                    payload_hash=payload_hash,
                )
            )

    def _record_state(
        self, table: str, record: dict[str, Any]
    ) -> tuple[ExternalRecordRef | None, str, bool]:
        external_id = str(record["id"])
        payload_hash = _hash_payload(record)
        ref = self._existing_ref(table, external_id)
        return ref, payload_hash, ref is not None and ref.payload_hash == payload_hash

    def import_sources(self, records: list[dict[str, Any]]) -> ImportStats:
        stats = ImportStats()
        for record in records:
            ref, payload_hash, unchanged = self._record_state("SOURCES", record)
            if unchanged:
                stats.unchanged += 1
                continue
            fields = record.get("fields", {})
            name = _text(fields, "Name")
            if not name:
                stats.skipped += 1
                continue
            tier_text = _text(fields, "Tier") or "E"
            tier = (
                SourceTier(tier_text[-1].upper())
                if tier_text[-1].upper() in "ABCDE"
                else SourceTier.E
            )
            values = {
                "name": name,
                "source_type": _source_type(fields.get("Type")),
                "tier": tier,
                "language": _text(fields, "Language") or "en",
                "country": _country(fields.get("Country")),
                "access_method": _enum_value(
                    fields.get("AccessMethod"), AccessMethod, default=AccessMethod.MANUAL
                ),
                "feed_url": _text(fields, "URL/Feed", "URL", "Feed"),
                "inbound_mailbox_hash": _text(fields, "InboundMailboxHash", "MailboxHash"),
                "rights_basis": _rights_basis(fields.get("RightsBasis")),
                "rights_notes": _text(fields, "RightsNotes", "Notes"),
                "state_affiliation": _text(fields, "StateAffiliation"),
                "notes": _text(fields, "Notes"),
                "last_reviewed_at": _datetime(fields.get("LastReviewed")),
            }
            source = self.session.get(Source, ref.entity_id) if ref else None
            if source:
                for field, value in values.items():
                    setattr(source, field, value)
                stats.updated += 1
            else:
                source = Source(**values, active=False)
                self.session.add(source)
                self.session.flush()
                stats.created += 1
            self._record_ref(
                table="SOURCES",
                external_id=str(record["id"]),
                entity="source",
                entity_id=source.id,
                payload_hash=payload_hash,
            )
        self.session.flush()
        for record in records:
            fields = record.get("fields", {})
            links = _links(fields, "SyndicatesFrom") or _links(fields, "Syndicates From")
            if not links:
                continue
            ref = self._existing_ref("SOURCES", str(record["id"]))
            origin_id = self.ids.get(("SOURCES", links[0]))
            source = self.session.get(Source, ref.entity_id) if ref else None
            if source is not None and origin_id is not None and source.id != origin_id:
                source.syndicates_from_id = origin_id
        self.session.flush()
        return stats

    def import_events(self, records: list[dict[str, Any]]) -> ImportStats:
        stats = ImportStats()
        for record in records:
            ref, payload_hash, unchanged = self._record_state("EVENTS", record)
            if unchanged:
                stats.unchanged += 1
                continue
            fields = record.get("fields", {})
            title = _text(fields, "Title-Factual", "Title")
            if not title:
                stats.skipped += 1
                continue
            values = {
                "event_type": _enum_value(
                    fields.get("Type"), EventType, default=EventType.SECURITY_INCIDENT
                ),
                "corridor": _enum_value(
                    fields.get("Corridor"), Corridor, default=Corridor.EAST_MED
                ),
                "geo_precision": _text(fields, "Port/Area"),
                "status": _enum_value(
                    fields.get("Status"), EventStatus, default=EventStatus.MONITORING
                ),
                "severity": _bounded_int(fields.get("Severity"), default=1, minimum=1, maximum=4),
                "occurred_start": _datetime(fields.get("OccurredStart")),
                "occurred_end": _datetime(fields.get("OccurredEnd")),
            }
            event = self.session.get(Event, ref.entity_id) if ref else None
            if event:
                for field, value in values.items():
                    setattr(event, field, value)
                stats.updated += 1
            else:
                event = Event(
                    slug=f"{_slug(title)}-{str(record['id'])[-6:].lower()}",
                    **values,
                )
                self.session.add(event)
                self.session.flush()
                stats.created += 1
            self._record_ref(
                table="EVENTS",
                external_id=str(record["id"]),
                entity="event",
                entity_id=event.id,
                payload_hash=payload_hash,
            )
        self.session.flush()
        return stats

    def import_claims(self, records: list[dict[str, Any]]) -> ImportStats:
        stats = ImportStats()
        for record in records:
            ref, payload_hash, unchanged = self._record_state("CLAIMS", record)
            if unchanged:
                stats.unchanged += 1
                continue
            fields = record.get("fields", {})
            event_links = _links(fields, "Event")
            event_id = self.ids.get(("EVENTS", event_links[0])) if event_links else None
            claim_text = _text(fields, "ClaimText", "Claim")
            if not event_id or not claim_text:
                stats.skipped += 1
                continue
            values = {
                "event_id": event_id,
                "text": claim_text,
                "claimant": _text(fields, "Claimant"),
                "claim_state": _claim_state(fields.get("Confidence")),
                "occurred_at": _datetime(fields.get("OccurredAt")),
                "reviewed_by": _text(fields, "VerifiedBy"),
                "reviewed_at": _datetime(fields.get("VerifiedAt")),
            }
            claim = self.session.get(Claim, ref.entity_id) if ref else None
            if claim:
                for field, value in values.items():
                    setattr(claim, field, value)
                stats.updated += 1
            else:
                claim = Claim(
                    **values,
                    proposed_by="airtable-import",
                    sensitivity_flags=[],
                    quantity_json=[],
                    first_seen_at=_datetime(fields.get("FirstSeen")) or datetime.now(UTC),
                )
                self.session.add(claim)
                self.session.flush()
                stats.created += 1
            self._record_ref(
                table="CLAIMS",
                external_id=str(record["id"]),
                entity="claim",
                entity_id=claim.id,
                payload_hash=payload_hash,
            )
        self.session.flush()
        return stats

    def import_evidence(self, records: list[dict[str, Any]]) -> ImportStats:
        stats = ImportStats()
        for record in records:
            ref, payload_hash, unchanged = self._record_state("EVIDENCE", record)
            if unchanged:
                stats.unchanged += 1
                continue
            fields = record.get("fields", {})
            source_links = _links(fields, "Source")
            claim_links = _links(fields, "Claim") or _links(fields, "Claims")
            source_id = self.ids.get(("SOURCES", source_links[0])) if source_links else None
            claim_id = self.ids.get(("CLAIMS", claim_links[0])) if claim_links else None
            if not source_id or not claim_id:
                stats.skipped += 1
                continue
            source = self.session.get(Source, source_id)
            if source is None:
                stats.skipped += 1
                continue
            url = _text(fields, "URL") or f"airtable://{self.base_id}/{record['id']}"
            excerpt = _text(fields, "Excerpt") or ""
            captured = _datetime(fields.get("CaptureTime")) or datetime.now(UTC)
            raw_payload = json.dumps(fields, ensure_ascii=False, sort_keys=True, default=str)
            content_hash = hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()
            source_record = self.session.scalar(
                select(SourceRecord).where(
                    SourceRecord.source_id == source_id,
                    SourceRecord.content_hash == content_hash,
                )
            )
            if source_record is None:
                scan = scan_untrusted_text(raw_payload)
                source_record = SourceRecord(
                    source_id=source_id,
                    url=url,
                    canonical_url=url,
                    content_hash=content_hash,
                    raw_ref_r2=_text(fields, "SnapshotLink"),
                    extracted_text=excerpt or raw_payload,
                    lang=source.language,
                    published_at=_datetime(fields.get("OriginalPubTime")),
                    fetched_at=captured,
                    parser_version="airtable-import-v1",
                    security_scan={**scan.as_dict(), "imported": True},
                )
                self.session.add(source_record)
            lineage_description = _text(fields, "LineageRoot") or f"airtable:{record['id']}"
            lineage = self.session.scalar(
                select(LineageRoot).where(LineageRoot.description == lineage_description)
            )
            if lineage is None:
                lineage = LineageRoot(
                    description=lineage_description,
                    origin_source_id=source_id,
                    origin_url=url,
                    first_seen_at=captured,
                )
                self.session.add(lineage)
            self.session.flush()
            ensure_triage_item(self.session, source_record)
            evidence = Evidence(
                source_record_id=source_record.id,
                claim_id=claim_id,
                directness=_enum_value(
                    fields.get("Directness"), Directness, default=Directness.SECONDARY
                ),
                lineage_root_id=lineage.id,
                excerpt=excerpt or None,
                capture_snapshot_r2=_text(fields, "SnapshotLink"),
                rights_decision={
                    "basis": source.rights_basis.value,
                    "imported_from": "airtable",
                },
            )
            self.session.add(evidence)
            self.session.flush()
            self._record_ref(
                table="EVIDENCE",
                external_id=str(record["id"]),
                entity="evidence",
                entity_id=evidence.id,
                payload_hash=payload_hash,
            )
            if ref:
                stats.updated += 1
            else:
                stats.created += 1
        self.session.flush()
        return stats

    def import_emev(self, records: list[dict[str, Any]]) -> ImportStats:
        stats = ImportStats()
        for record in records:
            ref, payload_hash, unchanged = self._record_state("EMEV-LOG", record)
            if unchanged:
                stats.unchanged += 1
                continue
            fields = record.get("fields", {})
            event_links = _links(fields, "Event")
            event_id = self.ids.get(("EVENTS", event_links[0])) if event_links else None
            work_date = _date(fields.get("Date"))
            if not event_id or not work_date:
                stats.skipped += 1
                continue
            values = {
                "event_id": event_id,
                "work_date": work_date,
                "task": _text(fields, "Task") or "triage",
                "minutes": _bounded_int(fields.get("Minutes"), default=0, minimum=0, maximum=1440),
                "analyst": _text(fields, "Analyst") or "unknown",
            }
            log = self.session.get(EMEVLog, ref.entity_id) if ref else None
            if log:
                for field, value in values.items():
                    setattr(log, field, value)
                stats.updated += 1
            else:
                log = EMEVLog(**values)
                self.session.add(log)
                self.session.flush()
                stats.created += 1
            self._record_ref(
                table="EMEV-LOG",
                external_id=str(record["id"]),
                entity="emev_log",
                entity_id=log.id,
                payload_hash=payload_hash,
            )
        self.session.flush()
        return stats

    def import_all(self, payloads: dict[str, list[dict[str, Any]]]) -> ImportStats:
        total = ImportStats()
        total.add(self.import_sources(payloads.get("SOURCES", [])))
        total.add(self.import_events(payloads.get("EVENTS", [])))
        total.add(self.import_claims(payloads.get("CLAIMS", [])))
        total.add(self.import_evidence(payloads.get("EVIDENCE", [])))
        total.add(self.import_emev(payloads.get("EMEV-LOG", [])))
        self.session.add(
            AuditLog(
                actor="airtable-import",
                action="airtable.import_completed",
                entity="airtable_base",
                payload_json={
                    "base_id": self.base_id,
                    "created": total.created,
                    "unchanged": total.unchanged,
                    "updated": total.updated,
                    "skipped": total.skipped,
                },
            )
        )
        return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Import the Phase 1 Airtable newsroom")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    if not settings.airtable_token or not settings.airtable_base_id:
        raise SystemExit("Set EASTMED_AIRTABLE_TOKEN and EASTMED_AIRTABLE_BASE_ID")
    client = AirtableClient(
        token=settings.airtable_token.get_secret_value(),
        base_id=settings.airtable_base_id,
    )
    try:
        payloads = {table: client.records(table) for table in AirtableImporter.TABLES}
    finally:
        client.close()
    with SessionLocal() as session:
        stats = AirtableImporter(session, base_id=settings.airtable_base_id).import_all(payloads)
        if args.dry_run:
            session.rollback()
        else:
            session.commit()
    print(json.dumps(stats.__dict__, sort_keys=True))


if __name__ == "__main__":
    main()

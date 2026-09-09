import argparse
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from eastmed_api.database import SessionLocal
from eastmed_schema.enums import AccessMethod, RightsBasis, SourceTier, SourceType
from eastmed_schema.models import AuditLog, Source
from sqlalchemy import select

DEFAULT_CANDIDATES = Path(__file__).resolve().parents[3] / "config" / "source_candidates.json"


def load_candidates(path: Path = DEFAULT_CANDIDATES) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Source candidate registry must contain a JSON list")
    names: set[str] = set()
    required = {
        "name",
        "legal_entity",
        "source_type",
        "tier",
        "language",
        "country",
        "access_method",
        "feed_url",
        "rights_basis",
        "rights_status",
        "rights_evidence_url",
        "intended_use",
        "poll_interval_seconds",
    }
    for index, candidate in enumerate(payload):
        if not isinstance(candidate, dict):
            raise ValueError(f"Source candidate {index} must be an object")
        missing = required - candidate.keys()
        if missing:
            raise ValueError(f"Source candidate {index} is missing {sorted(missing)}")
        name = candidate["name"]
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError("Source candidate names must be non-empty and unique")
        names.add(name)
        if candidate["rights_status"] != "pending-counsel":
            raise ValueError("Candidate registry cannot assert source-rights approval")
        for url_field in ("feed_url", "rights_evidence_url"):
            value = candidate[url_field]
            parsed = urlsplit(value) if isinstance(value, str) else None
            if parsed is None or parsed.scheme != "https" or not parsed.hostname:
                raise ValueError(f"Source candidate {name!r} has an invalid {url_field}")
    return payload


def seed_candidates(path: Path = DEFAULT_CANDIDATES) -> tuple[int, int]:
    created = 0
    existing = 0
    candidates = load_candidates(path)
    with SessionLocal() as session:
        for candidate in candidates:
            source = session.scalar(select(Source).where(Source.name == candidate["name"]))
            if source:
                existing += 1
                continue
            source = Source(
                name=candidate["name"],
                legal_entity=candidate["legal_entity"],
                source_type=SourceType(candidate["source_type"]),
                tier=SourceTier(candidate["tier"]),
                language=candidate["language"],
                country=candidate.get("country"),
                access_method=AccessMethod(candidate["access_method"]),
                feed_url=candidate.get("feed_url"),
                rights_basis=RightsBasis(candidate["rights_basis"]),
                rights_notes=(
                    "Status: pending counsel. Evidence URL: "
                    f"{candidate['rights_evidence_url']}. Intended use: "
                    f"{candidate['intended_use']}"
                ),
                notes=candidate.get("notes"),
                poll_interval_seconds=int(candidate.get("poll_interval_seconds", 3600)),
                active=False,
            )
            session.add(source)
            session.flush()
            session.add(
                AuditLog(
                    actor="source-seed",
                    action="source.candidate_created",
                    entity="source",
                    entity_id=source.id,
                    payload_json={"name": source.name, "active": False, "approved": False},
                )
            )
            created += 1
        session.commit()
    return created, existing


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed disabled Tier A source candidates")
    parser.add_argument("--file", type=Path, default=DEFAULT_CANDIDATES)
    args = parser.parse_args()
    created, existing = seed_candidates(args.file)
    print(json.dumps({"created": created, "existing": existing}, sort_keys=True))


if __name__ == "__main__":
    main()

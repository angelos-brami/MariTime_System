import hashlib
import json
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any
from uuid import UUID

import feedparser  # type: ignore[import-untyped]
from eastmed_schema.enums import AccessMethod
from eastmed_schema.models import Source, SourceRecord
from eastmed_shared import get_settings
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from eastmed_pipeline.fetch import SafeHttpFetcher
from eastmed_pipeline.rights import authorize_automation
from eastmed_pipeline.security_scan import scan_untrusted_text
from eastmed_pipeline.snapshots import FileSnapshotStore, SnapshotStore
from eastmed_pipeline.triage import ensure_triage_item
from eastmed_pipeline.url_safety import (
    UnsafeFetchURLError,
    canonicalize_reference_url,
    canonicalize_url,
)

PARSER_VERSION = "rss-v1"


class _PlainTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())


def _html_to_text(value: str) -> str:
    parser = _PlainTextParser()
    parser.feed(value)
    return " ".join(" ".join(parser.parts).split()) or value.strip()


def _entry_value(entry: Any, key: str, default: str = "") -> str:
    value = entry.get(key, default)
    return str(value) if value is not None else default


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except (TypeError, ValueError, OverflowError):
        return None


def _canonical_entry(entry: Any, source: Source) -> dict[str, Any]:
    title = _entry_value(entry, "title").strip()
    summary = _entry_value(entry, "summary") or _entry_value(entry, "description")
    raw_link = _entry_value(entry, "link") or source.feed_url or ""
    try:
        link = canonicalize_reference_url(raw_link)
    except UnsafeFetchURLError:
        link = canonicalize_url(source.feed_url) if source.feed_url else ""
    author = _entry_value(entry, "author").strip() or None
    published_raw = _entry_value(entry, "published") or _entry_value(entry, "updated")
    return {
        "title": title,
        "summary": _html_to_text(summary),
        "link": link,
        "author": author,
        "published_raw": published_raw,
    }


def ingest_rss_source(
    session: Session,
    *,
    source_id: UUID,
    fetcher: SafeHttpFetcher | None = None,
    snapshot_store: SnapshotStore | None = None,
) -> int:
    source = session.get(Source, source_id)
    if source is None:
        raise LookupError("Source not found")
    if source.access_method != AccessMethod.RSS or not source.feed_url:
        raise ValueError("Source is not configured as RSS")
    rights = authorize_automation(source)
    settings = get_settings()
    active_fetcher = fetcher or SafeHttpFetcher(allowed_hosts=settings.allowed_fetch_host_set)
    store = snapshot_store or FileSnapshotStore()
    fetched = active_fetcher.fetch(
        source.feed_url, etag=source.etag, last_modified=source.last_modified
    )
    if fetched.not_modified:
        source.last_successful_poll_at = datetime.now(UTC)
        session.commit()
        return 0

    parsed = feedparser.parse(fetched.body)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"RSS parse failed: {parsed.bozo_exception}")

    created = 0
    for entry in parsed.entries:
        item = _canonical_entry(entry, source)
        payload = json.dumps(item, ensure_ascii=False, sort_keys=True).encode("utf-8")
        content_hash = hashlib.sha256(payload).hexdigest()
        exists = session.scalar(
            select(SourceRecord.id).where(
                SourceRecord.source_id == source.id,
                SourceRecord.content_hash == content_hash,
            )
        )
        if exists:
            continue
        snapshot_ref = store.put(source_id=str(source.id), payload=payload, suffix="json")
        scan = scan_untrusted_text(f"{item['title']}\n{item['summary']}")
        record = SourceRecord(
            source_id=source.id,
            url=item["link"] or source.feed_url,
            canonical_url=item["link"] or canonicalize_url(source.feed_url),
            content_hash=content_hash,
            raw_ref_r2=snapshot_ref,
            extracted_text=item["summary"],
            title=item["title"] or None,
            author=item["author"],
            lang=source.language,
            published_at=_parse_datetime(item["published_raw"]),
            parser_version=PARSER_VERSION,
            security_scan={**scan.as_dict(), "rights": rights.as_dict()},
        )
        try:
            with session.begin_nested():
                session.add(record)
                session.flush()
                ensure_triage_item(session, record)
        except IntegrityError:
            continue
        created += 1

    source.etag = fetched.etag
    source.last_modified = fetched.last_modified
    source.last_successful_poll_at = datetime.now(UTC)
    session.commit()
    return created

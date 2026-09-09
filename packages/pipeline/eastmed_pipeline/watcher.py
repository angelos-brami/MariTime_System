from __future__ import annotations

import hashlib
import io
import json
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser
from uuid import UUID

import pdfplumber
import trafilatura
from eastmed_schema.enums import AccessMethod
from eastmed_schema.models import Source, SourceRecord
from eastmed_shared import get_settings
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from eastmed_pipeline.fetch import FetchResult, SafeHttpFetcher
from eastmed_pipeline.rights import authorize_automation
from eastmed_pipeline.security_scan import scan_untrusted_text
from eastmed_pipeline.snapshots import FileSnapshotStore, SnapshotStore
from eastmed_pipeline.triage import ensure_triage_item
from eastmed_pipeline.url_safety import canonicalize_url

ROBOTS_USER_AGENT = "EastMedDesk"
HTML_PARSER_VERSION = "page-watch-v1"
PDF_PARSER_VERSION = "pdf-generic-v1"
JSON_PARSER_VERSION = "json-watch-v1"
MAX_PDF_PAGES = 200
MAX_EXTRACTED_CHARACTERS = 5_000_000


class RobotsPolicyError(PermissionError):
    pass


class DocumentExtractionError(ValueError):
    pass


class HttpFetcher(Protocol):
    def fetch(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        allow_not_found: bool = False,
    ) -> FetchResult: ...


class _TitleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_title = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag.lower() == "title":
            self.in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title and data.strip():
            self.parts.append(data.strip())


def _robots_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))


def enforce_robots(fetcher: HttpFetcher, url: str) -> None:
    robots_url = _robots_url(url)
    result = fetcher.fetch(robots_url, allow_not_found=True)
    if result.status_code == 404:
        return
    policy = RobotFileParser()
    policy.set_url(robots_url)
    policy.parse(result.body.decode("utf-8", errors="replace").splitlines())
    if not policy.can_fetch(ROBOTS_USER_AGENT, url):
        raise RobotsPolicyError(f"robots.txt disallows automated retrieval of {url}")


def extract_html(body: bytes) -> tuple[str | None, str]:
    document = body.decode("utf-8", errors="replace")
    title_parser = _TitleParser()
    title_parser.feed(document)
    title = " ".join(" ".join(title_parser.parts).split()) or None
    extracted = trafilatura.extract(
        document,
        include_comments=False,
        include_tables=True,
        no_fallback=False,
    )
    text = " ".join((extracted or "").split())
    if not text:
        raise DocumentExtractionError("Page extraction produced no usable text")
    return title, text


def extract_json(body: bytes) -> str:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DocumentExtractionError("JSON source returned malformed data") from exc
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if not text or text in {"null", "{}", "[]"}:
        raise DocumentExtractionError("JSON source returned no usable data")
    if len(text) > MAX_EXTRACTED_CHARACTERS:
        raise DocumentExtractionError("JSON source exceeds the extraction limit")
    return text


def extract_pdf(body: bytes) -> str:
    parts: list[str] = []
    with pdfplumber.open(io.BytesIO(body)) as document:
        if len(document.pages) > MAX_PDF_PAGES:
            raise DocumentExtractionError(f"PDF exceeds the {MAX_PDF_PAGES}-page limit")
        for page in document.pages:
            text = page.extract_text(x_tolerance=2, y_tolerance=3)
            if text and text.strip():
                parts.append(text.strip())
            if sum(len(part) for part in parts) > MAX_EXTRACTED_CHARACTERS:
                raise DocumentExtractionError("PDF extracted text exceeds the safety limit")
    normalized = "\n\n".join(parts).strip()
    if not normalized:
        raise DocumentExtractionError("PDF extraction produced no usable text")
    return normalized


def _http_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)


def _is_pdf(result: FetchResult) -> bool:
    content_type_is_pdf = "application/pdf" in result.content_type.lower()
    url_is_pdf = urlsplit(result.url).path.lower().endswith(".pdf")
    return content_type_is_pdf or url_is_pdf


def ingest_watch_source(
    session: Session,
    *,
    source_id: UUID,
    fetcher: HttpFetcher | None = None,
    snapshot_store: SnapshotStore | None = None,
) -> int:
    source = session.get(Source, source_id)
    if source is None:
        raise LookupError("Source not found")
    if source.access_method != AccessMethod.WATCH or not source.feed_url:
        raise ValueError("Source is not configured as a page/PDF watch")

    rights = authorize_automation(source)
    settings = get_settings()
    active_fetcher = fetcher or SafeHttpFetcher(allowed_hosts=settings.allowed_fetch_host_set)
    enforce_robots(active_fetcher, source.feed_url)
    fetched = active_fetcher.fetch(
        source.feed_url, etag=source.etag, last_modified=source.last_modified
    )
    if fetched.not_modified:
        source.last_successful_poll_at = datetime.now(UTC)
        session.commit()
        return 0

    if _is_pdf(fetched):
        title = None
        extracted_text = extract_pdf(fetched.body)
        parser_version = PDF_PARSER_VERSION
        suffix = "pdf"
    elif "application/json" in fetched.content_type.lower():
        title = None
        extracted_text = extract_json(fetched.body)
        parser_version = JSON_PARSER_VERSION
        suffix = "json"
    else:
        allowed_content_types = ("text/html", "application/xhtml+xml", "text/plain")
        if fetched.content_type and not any(
            item in fetched.content_type.lower() for item in allowed_content_types
        ):
            raise DocumentExtractionError(f"Unsupported page content type: {fetched.content_type}")
        title, extracted_text = extract_html(fetched.body)
        parser_version = HTML_PARSER_VERSION
        suffix = "html"

    canonical = {
        "url": canonicalize_url(fetched.url),
        "title": title,
        "text": extracted_text,
    }
    content_hash = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    exists = session.scalar(
        select(SourceRecord.id).where(
            SourceRecord.source_id == source.id,
            SourceRecord.content_hash == content_hash,
        )
    )
    created = 0
    if not exists:
        store = snapshot_store or FileSnapshotStore()
        snapshot_ref = store.put(source_id=str(source.id), payload=fetched.body, suffix=suffix)
        scan = scan_untrusted_text(extracted_text)
        record = SourceRecord(
            source_id=source.id,
            url=fetched.url,
            canonical_url=canonical["url"],
            content_hash=content_hash,
            raw_ref_r2=snapshot_ref,
            extracted_text=extracted_text,
            title=title,
            lang=source.language,
            modified_at=_http_datetime(fetched.last_modified),
            parser_version=parser_version,
            security_scan={**scan.as_dict(), "rights": rights.as_dict()},
        )
        try:
            with session.begin_nested():
                session.add(record)
                session.flush()
                ensure_triage_item(session, record)
            created = 1
        except IntegrityError:
            created = 0

    source.etag = fetched.etag
    source.last_modified = fetched.last_modified
    source.last_successful_poll_at = datetime.now(UTC)
    session.commit()
    return created

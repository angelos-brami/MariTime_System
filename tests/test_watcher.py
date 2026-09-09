from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import cast
from unittest.mock import MagicMock

import pytest
from eastmed_pipeline.fetch import FetchResult
from eastmed_pipeline.rights import RightsPolicyError
from eastmed_pipeline.watcher import (
    RobotsPolicyError,
    enforce_robots,
    extract_html,
    extract_json,
    extract_pdf,
    ingest_watch_source,
)
from eastmed_schema.enums import AccessMethod, RightsBasis, SourceTier, SourceType
from eastmed_schema.models import Source
from reportlab.pdfgen.canvas import Canvas
from sqlalchemy.orm import Session


@dataclass
class FakeFetcher:
    robots: FetchResult
    calls: list[str] = field(default_factory=list)

    def fetch(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        allow_not_found: bool = False,
    ) -> FetchResult:
        del etag, last_modified, allow_not_found
        self.calls.append(url)
        return self.robots


def test_robots_disallow_stops_page_retrieval() -> None:
    fetcher = FakeFetcher(
        FetchResult(
            url="https://example.test/robots.txt",
            body=b"User-agent: EastMedDesk\nDisallow: /restricted",
            content_type="text/plain",
            etag=None,
            last_modified=None,
        )
    )
    with pytest.raises(RobotsPolicyError, match="disallows"):
        enforce_robots(fetcher, "https://example.test/restricted/advisory")
    assert fetcher.calls == ["https://example.test/robots.txt"]


def test_missing_robots_file_allows_page_retrieval() -> None:
    fetcher = FakeFetcher(
        FetchResult(
            url="https://example.test/robots.txt",
            body=b"",
            content_type="text/plain",
            etag=None,
            last_modified=None,
            status_code=404,
        )
    )
    enforce_robots(fetcher, "https://example.test/advisory")


def test_rights_gate_runs_before_robots_network_request() -> None:
    source = Source(
        name="Unapproved watch",
        source_type=SourceType.OFFICIAL,
        tier=SourceTier.A,
        language="en",
        access_method=AccessMethod.WATCH,
        feed_url="https://example.test/advisory",
        rights_basis=RightsBasis.PUBLIC_ADVISORY,
        active=True,
    )
    session = MagicMock()
    session.get.return_value = source
    fetcher = FakeFetcher(
        FetchResult(
            url="https://example.test/robots.txt",
            body=b"",
            content_type="text/plain",
            etag=None,
            last_modified=None,
            status_code=404,
        )
    )
    with pytest.raises(RightsPolicyError, match="not been approved"):
        ingest_watch_source(cast(Session, session), source_id=source.id, fetcher=fetcher)
    assert fetcher.calls == []


def test_html_extractor_returns_title_and_article_text() -> None:
    body = b"""
    <html><head><title>Port navigation warning</title></head>
    <body><article><h1>Navigation warning 17</h1>
    <p>Vessels must keep a safe distance from the marked area until 18:00 UTC.</p>
    </article></body></html>
    """
    title, text = extract_html(body)
    assert title == "Port navigation warning"
    assert "Vessels must keep a safe distance" in text


def test_pdf_extractor_reads_text_from_a_real_pdf() -> None:
    stream = io.BytesIO()
    canvas = Canvas(stream)
    canvas.drawString(72, 760, "NAVIGATION WARNING 17")
    canvas.drawString(72, 740, "Restricted area active until 18:00 UTC")
    canvas.save()
    extracted = extract_pdf(stream.getvalue())
    assert "NAVIGATION WARNING 17" in extracted
    assert "Restricted area active" in extracted


def test_json_extractor_preserves_structured_source_values() -> None:
    extracted = extract_json(
        b'{"features":[{"properties":{"place":"Crete","mag":5.1}}]}'
    )
    assert '"place":"Crete"' in extracted
    assert '"mag":5.1' in extracted


def test_json_extractor_rejects_empty_or_malformed_payloads() -> None:
    with pytest.raises(ValueError, match="no usable data"):
        extract_json(b"[]")
    with pytest.raises(ValueError, match="malformed"):
        extract_json(b"{not-json}")

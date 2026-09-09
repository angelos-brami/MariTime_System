from typing import Any

from eastmed_pipeline.ingest import _canonical_entry
from eastmed_schema.enums import AccessMethod, RightsBasis, SourceTier, SourceType
from eastmed_schema.models import Source


def test_rss_entry_is_normalized_to_plain_text() -> None:
    source = Source(
        name="Example",
        source_type=SourceType.OFFICIAL,
        tier=SourceTier.A,
        language="en",
        access_method=AccessMethod.RSS,
        feed_url="https://example.com/feed.xml",
        rights_basis=RightsBasis.PUBLIC_ADVISORY,
    )
    entry: dict[str, Any] = {
        "title": "  Warning issued  ",
        "summary": "<p><strong>Vessels</strong> should exercise caution.</p>",
        "link": "https://EXAMPLE.com/advisory#top",
        "published": "Sun, 19 Jul 2026 08:00:00 GMT",
    }
    normalized = _canonical_entry(entry, source)
    assert normalized["title"] == "Warning issued"
    assert normalized["summary"] == "Vessels should exercise caution."
    assert normalized["link"] == "https://example.com/advisory"

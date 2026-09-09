import httpx
import pytest
import respx
from eastmed_pipeline import fetch as fetch_module
from eastmed_pipeline.fetch import MAX_RESPONSE_BYTES, FetchError, SafeHttpFetcher
from pytest import MonkeyPatch


@respx.mock
def test_optional_robots_404_is_returned_without_masking_status(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(fetch_module, "validate_fetch_url", lambda url, allowed_hosts: url)
    respx.get("https://example.test/robots.txt").mock(
        return_value=httpx.Response(404, text="not found")
    )
    result = SafeHttpFetcher(allowed_hosts=frozenset()).fetch(
        "https://example.test/robots.txt", allow_not_found=True
    )
    assert result.status_code == 404
    assert result.not_modified is False


@respx.mock
def test_response_body_is_bounded_while_streaming(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(fetch_module, "validate_fetch_url", lambda url, allowed_hosts: url)
    respx.get("https://example.test/large").mock(
        return_value=httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1))
    )

    with pytest.raises(FetchError, match="10 MiB"):
        SafeHttpFetcher(allowed_hosts=frozenset()).fetch("https://example.test/large")

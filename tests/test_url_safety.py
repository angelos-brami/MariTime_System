import socket

import pytest
from eastmed_pipeline.url_safety import (
    UnsafeFetchURLError,
    canonicalize_reference_url,
    canonicalize_url,
    validate_fetch_url,
)


def public_resolver(_: str, __: object) -> list[tuple[object, ...]]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]


def private_resolver(_: str, __: object) -> list[tuple[object, ...]]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]


def test_url_must_be_explicitly_allowlisted() -> None:
    with pytest.raises(UnsafeFetchURLError, match="allowlist"):
        validate_fetch_url(
            "https://example.com/feed", allowed_hosts={"ukmto.org"}, resolver=public_resolver
        )


def test_allowlisted_host_must_resolve_publicly() -> None:
    with pytest.raises(UnsafeFetchURLError, match="non-public"):
        validate_fetch_url(
            "https://example.com/feed", allowed_hosts={"example.com"}, resolver=private_resolver
        )


def test_valid_url_is_canonicalized_and_fragment_removed() -> None:
    result = validate_fetch_url(
        "HTTPS://Example.com:443/feed#latest",
        allowed_hosts={"example.com"},
        resolver=public_resolver,
    )
    assert result == "https://example.com/feed"
    assert canonicalize_url("https://EXAMPLE.com") == "https://example.com/"


def test_external_reference_rejects_script_and_credentials() -> None:
    with pytest.raises(UnsafeFetchURLError):
        canonicalize_reference_url("javascript:alert(1)")
    with pytest.raises(UnsafeFetchURLError):
        canonicalize_reference_url("https://user:secret@example.com/item")


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "https://user:password@example.com/feed",
        "https://example.com:8443/feed",
        "https://example.com:invalid/feed",
    ],
)
def test_dangerous_url_forms_are_rejected(url: str) -> None:
    with pytest.raises(UnsafeFetchURLError):
        validate_fetch_url(url, allowed_hosts={"example.com"}, resolver=public_resolver)

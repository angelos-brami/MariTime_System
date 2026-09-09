from dataclasses import dataclass

import httpx

from eastmed_pipeline.url_safety import validate_fetch_url

MAX_RESPONSE_BYTES = 10 * 1024 * 1024


class FetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class FetchResult:
    url: str
    body: bytes
    content_type: str
    etag: str | None
    last_modified: str | None
    status_code: int = 200
    not_modified: bool = False


class SafeHttpFetcher:
    def __init__(self, *, allowed_hosts: frozenset[str], timeout_seconds: float = 20.0) -> None:
        self.allowed_hosts = allowed_hosts
        self.timeout_seconds = timeout_seconds

    def fetch(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        allow_not_found: bool = False,
    ) -> FetchResult:
        safe_url = validate_fetch_url(url, allowed_hosts=self.allowed_hosts)
        headers = {"User-Agent": "EastMedDesk/0.1 (+rights-reviewed monitoring)"}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        with (
            httpx.Client(
                timeout=self.timeout_seconds,
                follow_redirects=False,
                headers=headers,
            ) as client,
            client.stream("GET", safe_url) as response,
        ):
            content_type = response.headers.get("content-type", "")
            response_etag = response.headers.get("etag")
            response_last_modified = response.headers.get("last-modified")
            if response.status_code == 304:
                return FetchResult(
                    url=safe_url,
                    body=b"",
                    content_type=content_type,
                    etag=response_etag or etag,
                    last_modified=response_last_modified or last_modified,
                    status_code=304,
                    not_modified=True,
                )
            if 300 <= response.status_code < 400:
                raise FetchError(
                    "Redirect refused; review and allowlist the destination explicitly"
                )
            allowed_not_found = response.status_code == 404 and allow_not_found
            if not allowed_not_found:
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise FetchError(f"Source returned HTTP {response.status_code}") from exc
            declared_length = response.headers.get("content-length")
            if declared_length:
                try:
                    if int(declared_length) > MAX_RESPONSE_BYTES:
                        raise FetchError("Source response exceeds the 10 MiB ingestion limit")
                except ValueError:
                    pass
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise FetchError("Source response exceeds the 10 MiB ingestion limit")
            return FetchResult(
                url=safe_url,
                body=bytes(body),
                content_type=content_type,
                etag=response_etag,
                last_modified=response_last_modified,
                status_code=response.status_code,
            )

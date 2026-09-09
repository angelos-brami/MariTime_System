from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import feedparser  # type: ignore[import-untyped]
import httpx
from eastmed_pipeline.source_seed import load_candidates
from eastmed_pipeline.url_safety import UnsafeFetchURLError, validate_fetch_url

USER_AGENT = "EastMedSourceReadiness/1.0 (+rights-review; no republication)"
MAX_AUDIT_BYTES = 2 * 1024 * 1024


def robots_url(source_url: str) -> str:
    parsed = urlsplit(source_url)
    return urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))


async def bounded_get(client: httpx.AsyncClient, url: str) -> tuple[httpx.Response, bytes]:
    async with client.stream("GET", url, headers={"User-Agent": USER_AGENT}) as response:
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > MAX_AUDIT_BYTES:
                raise ValueError("response exceeds the 2 MiB audit limit")
        return response, bytes(body)


async def audit_candidate(
    client: httpx.AsyncClient,
    candidate: dict[str, object],
    allowed_hosts: frozenset[str],
    semaphore: asyncio.Semaphore,
) -> dict[str, object]:
    name = str(candidate["name"])
    url = str(candidate["feed_url"])
    result: dict[str, object] = {
        "name": name,
        "url": url,
        "rights_status": candidate["rights_status"],
        "technically_healthy": False,
        "activatable": False,
    }
    try:
        safe_url = await asyncio.to_thread(
            validate_fetch_url, url, allowed_hosts=allowed_hosts
        )
        async with semaphore:
            robot_response, robot_body = await bounded_get(client, robots_url(safe_url))
            if robot_response.status_code in {404, 410}:
                robots_allowed = True
            elif robot_response.status_code == 200:
                parser = RobotFileParser()
                parser.set_url(robots_url(safe_url))
                parser.parse(robot_body.decode("utf-8", errors="replace").splitlines())
                robots_allowed = parser.can_fetch(USER_AGENT, safe_url)
            else:
                robots_allowed = False
            response, body = await bounded_get(client, safe_url)
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        status_ok = 200 <= response.status_code < 300
        no_redirect = not response.is_redirect and response.url == httpx.URL(safe_url)
        nonempty = bool(body.strip())
        parse_ok = nonempty
        entry_count: int | None = None
        if candidate["access_method"] == "rss" and status_ok and nonempty:
            feed = feedparser.parse(body)
            entry_count = len(feed.entries)
            parse_ok = entry_count > 0 and not bool(getattr(feed, "bozo", False))
        elif candidate["access_method"] == "watch":
            parse_ok = not content_type or content_type in {
                "text/html",
                "application/xhtml+xml",
                "text/plain",
                "application/json",
            }
        healthy = status_ok and no_redirect and nonempty and parse_ok and robots_allowed
        result.update(
            {
                "http_status": response.status_code,
                "content_type": content_type,
                "bytes_sampled": len(body),
                "robots_allowed": robots_allowed,
                "redirect_free": no_redirect,
                "parse_ok": parse_ok,
                "entry_count": entry_count,
                "technically_healthy": healthy,
            }
        )
    except (httpx.HTTPError, OSError, ValueError, UnsafeFetchURLError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


async def run(path: Path) -> dict[str, object]:
    candidates = load_candidates(path)
    allowed_hosts = frozenset(
        host
        for candidate in candidates
        if (host := urlsplit(candidate["feed_url"]).hostname) is not None
    )
    semaphore = asyncio.Semaphore(10)
    timeout = httpx.Timeout(15.0, connect=6.0)
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=False, trust_env=False
    ) as client:
        results = await asyncio.gather(
            *(
                audit_candidate(client, candidate, allowed_hosts, semaphore)
                for candidate in candidates
            )
        )
    healthy = sum(bool(item["technically_healthy"]) for item in results)
    rights_ready = sum(item["rights_status"] != "pending-counsel" for item in results)
    return {
        "schema_version": "1.0",
        "audited_at": datetime.now(UTC).isoformat(),
        "candidate_count": len(results),
        "technically_healthy_count": healthy,
        "rights_ready_count": rights_ready,
        "activation_performed": False,
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit disabled source candidates")
    parser.add_argument(
        "--catalog",
        type=Path,
        default=Path("config/source_candidates.json"),
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-healthy", type=int, default=0)
    args = parser.parse_args()
    report = asyncio.run(run(args.catalog))
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if report["technically_healthy_count"] < args.require_healthy:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

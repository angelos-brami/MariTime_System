from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
from datetime import UTC, datetime
from typing import Any

import httpx
from eastmed_api.database import SessionLocal
from eastmed_api.hardening import record_operational_drill
from eastmed_schema.enums import DrillType
from eastmed_shared import get_settings


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    return ordered[max(math.ceil(len(ordered) * quantile) - 1, 0)]


async def run_load(
    *,
    base_url: str,
    path: str,
    requests: int,
    concurrency: int,
    warmup_requests: int = 20,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    semaphore = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    statuses: dict[int, int] = {}
    warmup_statuses: dict[int, int] = {}

    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=10.0,
        headers=headers,
    ) as client:

        async def warmup_one() -> None:
            try:
                response = await client.get(path)
                status = response.status_code
            except httpx.HTTPError:
                status = 0
            warmup_statuses[status] = warmup_statuses.get(status, 0) + 1

        if warmup_requests:
            await asyncio.gather(*(warmup_one() for _ in range(warmup_requests)))

        async def one() -> None:
            async with semaphore:
                started = asyncio.get_running_loop().time()
                try:
                    response = await client.get(path)
                    status = response.status_code
                except httpx.HTTPError:
                    status = 0
                latencies.append((asyncio.get_running_loop().time() - started) * 1000)
                statuses[status] = statuses.get(status, 0) + 1

        await asyncio.gather(*(one() for _ in range(requests)))
    successful = sum(count for code, count in statuses.items() if 200 <= code < 300)
    return {
        "path": path,
        "warmup_requests": warmup_requests,
        "warmup_status_counts": warmup_statuses,
        "requests": requests,
        "concurrency": concurrency,
        "status_counts": statuses,
        "success_percent": round(successful * 100 / requests, 2),
        "latency_ms": {
            "median": round(percentile(latencies, 0.5), 2),
            "p95": round(percentile(latencies, 0.95), 2),
            "max": round(max(latencies), 2),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--path", default="/health")
    parser.add_argument("--requests", type=int, default=500)
    parser.add_argument("--concurrency", type=int, default=25)
    parser.add_argument(
        "--warmup-requests",
        type=int,
        default=20,
        help=(
            "reported initialization requests excluded from measured latency; use 0 for cold-start"
        ),
    )
    parser.add_argument("--p95-ms", type=float, default=500)
    parser.add_argument(
        "--authorization-env",
        default="EASTMED_LOAD_AUTHORIZATION",
        help="environment variable containing the complete Authorization header value",
    )
    parser.add_argument(
        "--require-auth",
        action="store_true",
        help="fail before the drill when the authorization environment variable is empty",
    )
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--executed-by", default="platform-operator")
    args = parser.parse_args()
    if (
        args.requests < 1
        or not 1 <= args.concurrency <= 500
        or not 0 <= args.warmup_requests <= 500
    ):
        raise SystemExit(
            "requests must be positive, concurrency must be 1..500, "
            "and warmup requests must be 0..500"
        )
    authorization = os.environ.get(args.authorization_env, "").strip()
    if args.require_auth and not authorization:
        raise SystemExit(
            f"authenticated load pass requires the {args.authorization_env} environment variable"
        )
    headers = {"Authorization": authorization} if authorization else None
    started = datetime.now(UTC)
    result = asyncio.run(
        run_load(
            base_url=args.base_url,
            path=args.path,
            requests=args.requests,
            concurrency=args.concurrency,
            warmup_requests=args.warmup_requests,
            headers=headers,
        )
    )
    completed = datetime.now(UTC)
    result["threshold_p95_ms"] = args.p95_ms
    result["authenticated"] = bool(authorization)
    warmup_total = sum(result["warmup_status_counts"].values())
    warmup_successful = sum(
        count for code, count in result["warmup_status_counts"].items() if 200 <= code < 300
    )
    result["passed"] = (
        (warmup_total == 0 or warmup_successful == warmup_total)
        and result["success_percent"] == 100
        and result["latency_ms"]["p95"] <= args.p95_ms
    )
    if args.record:
        settings = get_settings()
        with SessionLocal() as session:
            drill = record_operational_drill(
                session,
                drill_type=DrillType.LOAD,
                passed=bool(result["passed"]),
                started_at=started,
                completed_at=completed,
                executed_by=args.executed_by,
                environment=settings.environment,
                result=result,
            )
        result["drill_id"] = str(drill.id)
        result["evidence_hash"] = drill.evidence_hash
    print(json.dumps(result, sort_keys=True, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

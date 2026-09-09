from __future__ import annotations

import os
import sys
from urllib.parse import quote


def configure_runtime_environment(environment: dict[str, str]) -> dict[str, str]:
    configured = dict(environment)
    database_password = configured.pop("EASTMED_DATABASE_PASSWORD", None)
    if database_password:
        host = configured.get("EASTMED_DATABASE_HOST")
        name = configured.get("EASTMED_DATABASE_NAME", "eastmed")
        user = configured.get("EASTMED_DATABASE_USER")
        port = configured.get("EASTMED_DATABASE_PORT", "5432")
        if not host or not user:
            raise ValueError("database host and user are required with a database password")
        configured["EASTMED_DATABASE_URL"] = (
            "postgresql+psycopg://"
            f"{quote(user, safe='')}:{quote(database_password, safe='')}@"
            f"{host}:{port}/{quote(name, safe='')}?sslmode=require"
        )

    redis_password = configured.pop("EASTMED_REDIS_PASSWORD", None)
    if redis_password:
        host = configured.get("EASTMED_REDIS_HOST")
        port = configured.get("EASTMED_REDIS_PORT", "6379")
        if not host:
            raise ValueError("Redis host is required with a Redis password")
        configured["EASTMED_REDIS_URL"] = (
            f"rediss://:{quote(redis_password, safe='')}@{host}:{port}/0"
        )
    return configured


def main() -> None:
    command = sys.argv[1:]
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise SystemExit("eastmed-container-entrypoint requires a command")
    try:
        configured = configure_runtime_environment(dict(os.environ))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    # Direct argv execution is deliberate: no shell parsing or interpolation is involved.
    os.execvpe(command[0], command, configured)  # noqa: S606


if __name__ == "__main__":
    main()

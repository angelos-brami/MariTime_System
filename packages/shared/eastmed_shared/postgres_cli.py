from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlsplit


def postgres_environment(database_url: str) -> dict[str, str]:
    """Translate a PostgreSQL URL into libpq variables without exposing secrets in argv."""
    parsed = urlsplit(database_url.replace("postgresql+psycopg://", "postgresql://", 1))
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
        raise ValueError("database URL must use PostgreSQL")
    database = parsed.path.strip("/")
    if not database:
        raise ValueError("database URL must include a database name")
    values = {
        "PGHOST": parsed.hostname,
        "PGPORT": str(parsed.port or 5432),
        "PGDATABASE": unquote(database),
    }
    if parsed.username:
        values["PGUSER"] = unquote(parsed.username)
    if parsed.password:
        values["PGPASSWORD"] = unquote(parsed.password)
    sslmode = parse_qs(parsed.query).get("sslmode", [])
    if sslmode:
        values["PGSSLMODE"] = sslmode[0]
    return values

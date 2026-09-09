from __future__ import annotations

import json
import os
from pathlib import Path

import psycopg
from eastmed_shared import get_settings
from eastmed_shared.postgres_cli import postgres_environment
from psycopg import sql

ROLE_SQL = Path("infra/postgres/least_privilege.sql")
LOGIN_ROLES = {
    "api": ("eastmed_api_login", "eastmed_publication"),
    "scheduler": ("eastmed_scheduler_login", "eastmed_scheduler"),
    "ingestion": ("eastmed_ingestion_login", "eastmed_ingestion"),
    "analysis": ("eastmed_analysis_login", "eastmed_analysis"),
    "publication": ("eastmed_publication_login", "eastmed_brief_compiler"),
    "delivery": ("eastmed_delivery_login", "eastmed_delivery"),
    "readonly": ("eastmed_readonly_login", "eastmed_readonly"),
}
MANAGED_GROUP_ROLES = frozenset(group_role for _, group_role in LOGIN_ROLES.values())


def required_passwords(environment: dict[str, str]) -> dict[str, str]:
    passwords: dict[str, str] = {}
    for service in LOGIN_ROLES:
        key = f"EASTMED_DB_ROLE_PASSWORD_{service.upper()}"
        value = environment.get(key, "")
        if len(value) < 32:
            raise ValueError(f"{key} must contain at least 32 characters")
        passwords[service] = value
    if len(set(passwords.values())) != len(passwords):
        raise ValueError("every database login role requires a distinct password")
    return passwords


def provision(database_url: str, passwords: dict[str, str]) -> list[str]:
    if not ROLE_SQL.is_file():
        raise ValueError(f"database privilege policy is missing: {ROLE_SQL}")
    pg_environment = postgres_environment(database_url)
    configured: list[str] = []
    with psycopg.connect(
        host=pg_environment["PGHOST"],
        port=pg_environment["PGPORT"],
        dbname=pg_environment["PGDATABASE"],
        user=pg_environment["PGUSER"],
        password=pg_environment["PGPASSWORD"],
        sslmode="require",
        autocommit=True,
    ) as connection:
        connection.execute(ROLE_SQL.read_text(encoding="utf-8"))
        for service, (login_role, group_role) in LOGIN_ROLES.items():
            exists = connection.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %s)",
                (login_role,),
            ).fetchone()
            if not exists or not exists[0]:
                connection.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE"
                    ).format(sql.Identifier(login_role))
                )
            connection.execute(
                sql.SQL("ALTER ROLE {} PASSWORD %s").format(sql.Identifier(login_role)),
                (passwords[service],),
            )
            for obsolete_group in MANAGED_GROUP_ROLES - {group_role}:
                connection.execute(
                    sql.SQL("REVOKE {} FROM {}").format(
                        sql.Identifier(obsolete_group), sql.Identifier(login_role)
                    )
                )
            connection.execute(
                sql.SQL("GRANT {} TO {}").format(
                    sql.Identifier(group_role), sql.Identifier(login_role)
                )
            )
            configured.append(login_role)
    return configured


def main() -> None:
    try:
        passwords = required_passwords(dict(os.environ))
        configured = provision(get_settings().database_url, passwords)
    except (ValueError, psycopg.Error) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps({"configured_login_roles": configured}, sort_keys=True))


if __name__ == "__main__":
    main()

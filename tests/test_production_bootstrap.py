from __future__ import annotations

import pytest
from eastmed_api.provision_database_roles import LOGIN_ROLES, required_passwords
from eastmed_shared.container_entrypoint import configure_runtime_environment


def test_container_entrypoint_builds_tls_database_and_redis_urls_without_leaking_inputs() -> None:
    configured = configure_runtime_environment(
        {
            "EASTMED_DATABASE_HOST": "db.internal",
            "EASTMED_DATABASE_PORT": "5432",
            "EASTMED_DATABASE_NAME": "eastmed",
            "EASTMED_DATABASE_USER": "eastmed_api_login",
            "EASTMED_DATABASE_PASSWORD": "p@ss/word",
            "EASTMED_REDIS_HOST": "redis.internal",
            "EASTMED_REDIS_PASSWORD": "redis:secret",
        }
    )

    assert "p%40ss%2Fword" in configured["EASTMED_DATABASE_URL"]
    assert configured["EASTMED_DATABASE_URL"].endswith("?sslmode=require")
    assert "redis%3Asecret" in configured["EASTMED_REDIS_URL"]
    assert configured["EASTMED_REDIS_URL"].startswith("rediss://")
    assert "EASTMED_DATABASE_PASSWORD" not in configured
    assert "EASTMED_REDIS_PASSWORD" not in configured


def test_database_role_passwords_are_complete_long_and_distinct() -> None:
    environment = {
        f"EASTMED_DB_ROLE_PASSWORD_{service.upper()}": f"{service}-" + "x" * 40
        for service in LOGIN_ROLES
    }
    assert required_passwords(environment).keys() == LOGIN_ROLES.keys()

    environment["EASTMED_DB_ROLE_PASSWORD_API"] = "short"  # noqa: S105
    with pytest.raises(ValueError, match="at least 32"):
        required_passwords(environment)


def test_background_publication_worker_has_only_draft_compiler_membership() -> None:
    assert LOGIN_ROLES["publication"] == (
        "eastmed_publication_login",
        "eastmed_brief_compiler",
    )
    assert LOGIN_ROLES["api"][1] == "eastmed_publication"

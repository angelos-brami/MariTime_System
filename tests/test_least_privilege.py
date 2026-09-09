from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_worker_roles_do_not_receive_broad_sensitive_updates() -> None:
    sql = (ROOT / "infra" / "postgres" / "least_privilege.sql").read_text(encoding="utf-8")

    assert "GRANT UPDATE ON sources" not in sql
    assert "GRANT UPDATE ON deliveries" not in sql
    assert "GRANT UPDATE ON deliveries, correction_deliveries" not in sql
    assert "GRANT UPDATE (etag, last_modified" in sql
    assert "GRANT UPDATE (sent_at, delivered_at, status" in sql


def test_readonly_and_analysis_roles_do_not_inherit_every_table() -> None:
    sql = (ROOT / "infra" / "postgres" / "least_privilege.sql").read_text(encoding="utf-8")

    assert "GRANT SELECT ON ALL TABLES IN SCHEMA public TO eastmed_readonly" not in sql
    assert "GRANT SELECT ON ALL TABLES IN SCHEMA public TO eastmed_analysis" not in sql
    assert "REVOKE SELECT ON ALL TABLES IN SCHEMA public FROM eastmed_readonly" in sql
    assert (
        "account_api_keys"
        not in sql.split("TO eastmed_readonly", 1)[0].split("GRANT SELECT ON sources", 1)[-1]
    )

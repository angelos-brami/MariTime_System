# R7 — Backup restore drill

1. Restore the latest encrypted backup into an isolated non-production database.
2. Apply migrations and verify extension availability (`postgis`, `vector`).
3. Compare row counts for sources, source records, events, versions, claims, evidence, corrections, deliveries, TTV, EMEV, and audit records.
4. Recompute a sample of event-version hashes and compare with stored values.
5. Verify that the restored audit table still rejects update and delete.
6. Record recovery-point age, restoration duration, failures, and corrective actions.
7. Run weekly while the platform is young; never treat a successful backup as a successful restore.

Use `scripts/backup_database.py` for the encrypted daily artifact and
`scripts/run_restore_drill.py` against a database whose name contains `restore` or
`drill`. Preserve the timestamped backup filename and supply the independently
recorded decrypted-dump SHA-256; otherwise the drill fails instead of estimating a
false recovery point. See R9 for exact commands and safeguards.

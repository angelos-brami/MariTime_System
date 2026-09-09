# R9 — Hardening drills

Run all drills in staging and retain the JSON output with the incident/operations record. Add `--record` only when the staging database is available so the immutable evidence hash is stored.

## Monthly injection drill

```powershell
python scripts/run_injection_redteam.py --record --executed-by security-analyst
```

All malicious multilingual/encoded cases must be quarantined and all benign controls must remain clear. Any failure blocks extraction enablement.

## Monthly kill-switch drill

Set `EASTMED_OUTBOUND_ENABLED=false`, restart services, then run:

```powershell
python scripts/run_kill_switch_drill.py --record --executed-by senior-analyst
```

The drill must prove alert and correction dispatch stop before database access, internal Telegram is null-routed, and publication endpoints return 503.

## Load and latency pass

```powershell
python scripts/run_load_drill.py --base-url https://staging-api.example --path /health --requests 500 --concurrency 25 --p95-ms 500 --record
```

The drill reports and validates 20 initialization requests by default, then excludes
them from steady-state latency. Also run `--warmup-requests 0` after a clean process
restart to track cold-start latency separately; a cold-start regression does not get
hidden inside the steady-state figure.

Run a second authenticated pass against a representative read endpoint. Inject the
complete authorization value into `EASTMED_LOAD_AUTHORIZATION` through the staging
secret manager or CI secret environment, then require it explicitly:

```powershell
python scripts/run_load_drill.py --base-url https://staging-api.example --path /api/v1/data/events --requests 500 --concurrency 25 --p95-ms 500 --require-auth --record
```

The command never accepts or prints the secret. The release gate is 100% successful
responses and p95 below the agreed threshold.

## Weekly encrypted restore drill

Create a daily encrypted backup with an offline `age` recipient:

```powershell
python scripts/backup_database.py --output-dir D:\eastmed-backups --age-recipient age1...
```

Verify the encrypted sidecar checksum, decrypt the selected artifact into the
isolated drill workspace while preserving its timestamped basename, and record an
independent SHA-256 for the decrypted dump. Then run:

```powershell
python scripts/run_restore_drill.py --backup D:\restore-drill\eastmed-20260720T020000Z.dump --expected-sha256 <decrypted-dump-sha256> --target-url postgresql://restore_user:secret@restore-db/eastmed_restore_drill --record
```

The target database name must contain `restore` or `drill` and must differ from the application database. A backup is not accepted until extensions, required tables, audit immutability, hashes, row counts, recovery-point age, and restoration time pass.

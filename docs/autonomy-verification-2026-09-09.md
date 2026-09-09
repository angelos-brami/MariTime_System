# MariTime verification: fleet reconciliation and autonomous operation

Verified 9 September 2026 against the revised blueprint and the current workspace.

**Result: useful reconciliation components exist, but the application is not yet a connected, unattended fleet-analysis service. Do not represent the local console, passing unit tests, or simulated pilot as proof of an AI-operated production service.**

The customer's problem is routine fleet-analysis preparation: reconcile incoming daily reports against the correct vessel/voyage plan, calculate supported differences, expose missing/conflicting information, process corrections, and deliver traceable results without an analyst. Completion means that analytical deliverable. It does not mean automatically fixing the ship or resolving an operational cause inferred from a fuel variance.

## What has actually been built

| Component | Observed implementation | Practical limit |
| --- | --- | --- |
| Contract and import | CSV/JSON contract validation, normalized observations, source revision handling, memberships and expected jobs | Import functions accept decoded mappings. The production service does not expose the new import API or connect scheduled customer exports. |
| Calculations and verification | Decimal calculations, case versions, independent arithmetic checks, provenance and read-back functions | Passing fixtures is evidence for those fixtures. It does not establish broad source correctness or complete production verification. |
| Durable task primitives | Workflow/run/task/attempt tables, claims, epochs, retries and reservations | They are not connected to a running reconciliation stage dispatcher. PostgreSQL concurrency remains unverified. |
| Authority/publication | Dedicated service principals, standing grants, attestations, private publications and outbox records | Publication authority alone does not prove analytical correctness. See the publication-gate blocker below. |
| Customer presentation | Tenant-scoped read-model helpers for fleet reports, cases, timelines and health | They are not exposed through the production FastAPI/Next.js reconciliation routes. |
| Local demonstration | `scripts/recon_viewer.py` seeds fictional vessels in in-memory SQLite and calls the components | This is a developer viewer, explicitly described that way in its source. Its HTTP endpoints are not the authenticated customer product. |
| Agent contracts | Optional explanation model interface and bounded recovery selectors | The reconciliation path has no connected production model adapter or autonomous supervisor. Deterministic calculations appropriately require no model calls. |
| Earlier evaluator correction | `gold_evaluation.py` now scores within each document and validates grounding | This requested change is present; the old cross-document pooling defect is no longer the principal problem. |

The existing event-intelligence desk remains a separate human-operated workflow. Its editorial approvals, alert release and brief finalization have not been replaced by the new reconciliation components. Those controls were not disabled during this verification.

## Reproduced failures corrected during this verification

The original backend suite passed. Eleven additional regression cases failed before correction and pass afterward.

| Failure | Before | Corrected behavior |
| --- | --- | --- |
| Another vessel's plan | A report could use a different vessel's matching-period/grade plan inside the same account | Plan selection checks tenant-owned capture, vessel, voyage and effective interval. |
| Another voyage's plan | A same-vessel but different-voyage plan could satisfy a missing plan | Voyage identity must match the report and the expected job where specified. |
| Missing report | No actual observations produced an empty result with `unresolved=False` | Missing actual data is unresolved and cannot complete. |
| Wrong-voyage arrival | A report could attach to an expected job using only vessel and interval | A specified expected voyage is checked during job matching. |
| Empty/malformed result | Empty lists, non-object entries and unknown statuses could pass verification | These shapes fail verification. |
| Duplicate/omitted output | Repeated variances and omitted required evidence could evade complete-result checking | Duplicate entries and omitted comparable evidence are rejected; zero checked calculations cannot complete. |
| Publication denied | The pilot ledger still added an on-time success when nothing was published | Completion requires confirmed publication; denial remains unresolved. |
| Missed deadline | A late result was always labeled `CORRECT_ON_TIME` | A confirmed result after the declared due time is `CORRECT_LATE` and excluded from on-time completion. |

Additional corrections:

- Equal competing revisions are not resolved by incidental database order. Competing plan source lineages without a precedence policy remain unresolved.
- Pilot accounting includes late and unsupported outcomes instead of losing them from the accounting check.
- The replay's ten-second stage clock is explicitly simulated. It cannot satisfy the real processing-latency gate. The harness now returns `NOT_READY` when that runtime prerequisite is unmeasured.
- Production telemetry no longer hardcodes zero human interventions. It returns unknown until a durable intervention recorder is connected.
- Case-version age and the time between versions are no longer reported as preparation/correction latency. Those values remain unknown until the correct timestamps exist.
- The developer viewer now prominently says **LOCAL SIMULATION**, uses **Run synthetic replay**, and labels simulated timing. It does not upgrade its claims merely because its functions execute.

Files changed: `reconciliation_case.py`, `reconciliation_import.py`, `reconciliation_pilot.py`, `reconciliation_reliability.py`, shared reconciliation `verifier.py`, `pilot.py`, `telemetry.py`, the relevant regression tests, and the local viewer's labels. No deployed system, customer communications, source permissions or credentials were changed.

## Blockers before the promised service can run unattended

These remain open. The corrections above do not constitute an implementation of all remaining product work.

| Priority | Finding and evidence | Required completion evidence |
| --- | --- | --- |
| P0 | **No connected runtime.** `scheduler.py`, `jobs.py` and `worker.py` contain no reconciliation execution path. The expected-job ledger and staged functions are currently driven directly by tests/demo code. | A scheduled input reaches the durable stage dispatcher, runs in the prescribed order across process restarts, and produces a customer-visible outcome without a button press. |
| P0 | **No production customer/API path.** FastAPI's inspected route inventory exposes only the legacy operations dashboard, desk alerts and pollers. The proposed reconciliation imports/cases/daily-reports/health interfaces are absent. Next.js has no reconciliation integration. | Authenticated service ingestion and account-scoped customer read routes connected to the existing helpers, plus a real private fleet view. Wrong-account requests fail without disclosure. |
| P0 | **Publication does not require persisted analytical verification.** `propose_publication` supplies default evidence labels (`required_inputs_complete`, `accepted_facts`). Its policy check and subsequent hash read-back can operate without calling the analytical verifier; publication protocol tests use empty result fixtures. | Publication must load a persisted independent verdict bound to the exact source, expected-job, result and formula hashes. Empty, stale, unresolved or unverified results cannot acquire a completion/publication entitlement. A status-only unresolved notice has a separate contract. |
| P0 | **Concurrency protections are incomplete.** `reserve_cost` reads totals and updates a ledger without locking or an atomic conditional update. Stage commit/heartbeat paths check epoch/state but do not fully enforce expiry and lock ownership across transactions; expired-task recovery can claim beyond the attempt ceiling. These are code-review findings, not reproduced multi-worker PostgreSQL tests. | Actual PostgreSQL race tests for simultaneous reservations, lease expiry, correction versus dispatch, duplicate claims and crash recovery; bounded attempts/deadlines and truthful spend accounting. Add the global monthly budget, not only account/run caps. |
| P1 | **Automatic customer informing is not connected.** A private publication row and a read-model helper exist; a scheduled customer report/update feed does not. The legacy email/Telegram alert path requires human release and serves a different product. | Automatic private report availability, unresolved-state updates, correction notices and service-health changes, each tied to a stable logical publication identity and visible delivery state. External channels require their own explicit account/destination authority. |
| P1 | **Several runbooks still hand off to an operator.** `dispatch_operator_runbook` returns `REQUIRES_OPERATOR` for worker restart, model failover and rollback. Queue rebuilding only scans due tasks; it does not enqueue them. | Tested machine executors under bounded capabilities, or an explicitly unavailable outcome after finite recovery. No hidden support queue counted as autonomous completion. |
| P1 | **Source quarantine overstates its effect.** `run_quarantine_source` changes case statuses and pending publications but does not persist/enforce a source acquisition block. | A durable source stop checked by import, retrieval, planning and publication; correct invalidation of all affected pending work. |
| P1 | **Optional narrative is not semantically verified.** `_sentence_supported` checks reference IDs and allowed numeric tokens; a fabricated cause containing no new number can pass. No production reconciliation model adapter is wired. | Keep deterministic report text as the reliable default. Qualify a separate semantic verifier or allow only approved templates; withhold unsupported prose. Never make optional narration a prerequisite for a valid report. |
| P1 | **Operational evidence is incomplete.** No durable intervention ledger is connected. Arrival-to-completion/correction timestamps are insufficient. The pilot uses a simulated clock, directly provisions fixture memberships, and executes within a caller-managed test transaction. | Real committed transaction boundaries, independent read-back from another transaction/process, complete arrival/deadline/intervention logs and a frozen prospective workload. |
| P1 | **Intake and correction coverage need further qualification.** Rejected malformed inputs return before durable capture. Current selection works on observation groups; complete report revision replacement, removed grades, overlapping periods, declared source precedence and all supported unit bases need end-to-end adversarial fixtures. | Persist every contracted attempt and typed rejection; test correction semantics against independent reference results. Do not silently retain a removed field from an older report. |
| P1 | **Machine database isolation is incomplete.** New tables have tenant ownership constraints, but reconciliation row-security policies and dedicated least-privilege runtime grants were not found in the new migrations/provisioning path. | Restricted service roles and tenant context with PostgreSQL isolation tests. Existing broad publication-role privileges are not the proposed machine authority boundary. |
| P1 | **Qualification remains unproven.** There is no observed 30-day unattended trial, independent 300-case precision evidence or continuing paid-pilot evidence in this verification. | Meet the blueprint's actual workload, quality, latency, control, cost and commercial gates; do not derive them from demo counters. |

## The operating order to complete

1. Provision the customer's permitted source, fleet, schedule, service identities and standing private-publication authority once. Freeze expected jobs before arrivals.
2. Automatically acquire or accept scheduled CSV/JSON exports, persist every intake outcome, and create durable tasks without manual import actions.
3. Validate identity, schema, revision, interval, unit and fuel-grade completeness. Wrong, missing or conflicting data enters a typed unresolved/waiting state.
4. Select the applicable plan and actual report versions; calculate supported differences deterministically.
5. Independently verify required fields, arithmetic, scope, receipt and completeness. Persist the verdict bound to that result version.
6. Optionally explain verified results. Withhold narrative when unsupported or unavailable; valid deterministic analysis remains deliverable.
7. Obtain policy-bound machine authority and publish the exact verified private report. Confirm it from durable committed state.
8. Update the customer's daily report, vessel details, unresolved cases and correction timeline automatically. Delivery state must distinguish internal storage from actual customer availability.
9. On failure, execute only the supported bounded recovery operation. Retry within cost/attempt/deadline limits, then expose an honest unresolved/disabled outcome. Missing data never becomes an invented value.
10. On correction, invalidate stale work, recompute affected cases and fleet totals, publish a superseding version and preserve the original deadline score and history.

The next implementation priority is to close the publication/identity/concurrency controls, then connect a single scheduled structured-input-to-private-report path. Adding more named agents or a chat panel will not close these gaps.

## Verification performed and limits

- Full backend suite after corrections: **462 passed**. Machine-readable results: `tmp/autonomy-verification-tests.xml`.
- Ruff for `apps/api packages tests`: passed. Mypy: passed for 88 source files.
- Alembic source head: `20260908_0016`. This confirms the migration graph, not an applied production database.
- Frontend ESLint and TypeScript checks: passed.
- Production Next.js build: failed with JavaScript heap out-of-memory. At inspection the machine had about 200 MB free physical memory; the build also warned about workspace-root inference. No successful production build is claimed.
- Whole-repository Ruff is not clean: 312 findings, including temporary PDF authoring utilities; excluding `tmp` leaves 29 long-line findings in the existing developer viewer. Application/package/test lint is separately clean. These remaining formatting findings were not hidden by weakening rules.
- Local demo replay: 5 expected vessels; 4 reconciled and 1 unresolved for missing plan. Verified deltas: +1.60, 0.00, -1.60 and +1.70 tonnes for their respective plans. The clean replay panel is now `NOT_READY`: its original deadlines make its simulated completions late and its timing is not measured runtime evidence.
- Local default API/PostgreSQL/Redis ports (8000/5432/6379) were not listening. Port 3000 was listening. Docker CLI was absent from PATH and the usual Windows installation path. This does not establish the state of a remote deployment; none was inspected or changed.
- The reconciliation database tests use SQLite. They do not demonstrate PostgreSQL row security, locking, transaction races, queue-loss recovery or production durability.

**Decision:** retain the autonomous product target, but label the present implementation as tested components plus a synthetic developer console. Do not claim that it already runs entirely through AI agents or automatically informs customers end to end. The open work above is necessary to make that claim true.

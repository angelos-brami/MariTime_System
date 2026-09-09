# Claim-extraction shadow workflow

S8 assists analysts; it does not automate editorial judgment. The feature defaults off and the
configured mode in `config/claim_extraction_rules.yaml` is `shadow`.

## Processing boundary

1. A source must be active, rights-approved, and separately approved for model processing by a
   named human. Set or revoke both `model_processing_approved_at` and
   `model_processing_approved_by` through `PATCH /api/v1/sources/{source_id}/operations`.
   Revocation requires an explicit human operator and is separately audited.
2. Records already quarantined by the deterministic security scan are ineligible.
3. The analysis worker divides the complete document into deterministic, position-preserving,
   overlapping segments, wraps each segment as untrusted data, and calls the configured
   Anthropic Messages API with `claim_extraction_v2`, complete-document overlapping segments,
   exact source offsets, and a strict JSON schema. No tools are
   supplied, so the model cannot browse or call external systems.
4. Output may contain only explicit claims, attribution, an exact occurred time when explicitly
   present, location, quantities, hedging, and a verbatim source sentence. Confidence, severity,
   truth state, and publication labels are absent from the schema.
5. The service validates the response envelope and schema, refuses truncated/refusal responses,
   and independently verifies every quote against its exact segment. Exact overlap duplicates are
   removed, while retained proposals store their original source start/end offsets. A model-reported
   injection flag opens a quarantine desk alert and creates no proposals.
6. Runs log prompt/model versions, full-document and segment hashes, character coverage, segment
   count, aggregate token counts, mode, and terminal status. A document that exceeds the configured
   segment budget is explicitly `skipped` before any provider call; it is never silently truncated.
   Source text and model output are not copied into error logs.

## Analyst and QA workflow

Use `/console/extraction` or these desk-token endpoints:

- `GET /api/v1/claim-extraction/proposals?status=pending`
- `POST /api/v1/claim-extraction/proposals/{proposal_id}/review`
- `POST /api/v1/claim-extraction/reviews/{review_id}/qa`
- `GET /api/v1/claim-extraction/weekly-report`
- `POST /api/v1/claim-extraction/evaluations/{language}`

Accept and edit decisions require the analyst to select the event and claim state. Edits and
rejections require at least one reason code. Edited grounding quotes must still occur verbatim in
the source. Review timing captures both the Phase-1/manual baseline and assisted duration. A
second named human records QA and any error codes. Reviews and QA are immutable.

In shadow mode, accept/edit/reject records the decision only: no canonical `claims` row is
created. Even after a future switch to `production`, claim creation remains blocked unless the
latest evaluation for that language and prompt is graduated.

## Graduation gate

Evaluation is isolated by language, prompt version, and model-version fingerprint. Changing the
model resets the applicable graduation history even if the prompt text is unchanged. All
conditions must hold:

- at least 60 calendar days represented in reviewed samples;
- at least 30 reviewed proposals and 30 independently QA-reviewed proposals;
- at least 50% measured analyst-time reduction;
- QA error rate no higher than the real manual-control error rate.

The baseline error values intentionally default to `null`; null always blocks graduation. Supply
only measured Phase-1/manual control values. Do not estimate them. Weekly reason-code reports are
the input to prompt iteration; a changed prompt must receive a new version and its own shadow
evaluation.

## Enabling the worker

Configure:

```dotenv
EASTMED_ANTHROPIC_API_KEY=...
EASTMED_CLAIM_EXTRACTION_MODEL=claude-sonnet-5
EASTMED_CLAIM_EXTRACTION_ENABLED=true
```

Run `eastmed-analysis-worker` and `eastmed-scheduler`. The scheduler enqueues only eligible,
unprocessed source records. Disable the feature immediately by setting
`EASTMED_CLAIM_EXTRACTION_ENABLED=false`; already recorded proposals remain available for review
and audit.

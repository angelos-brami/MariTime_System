# Multilingual gold-set review protocol

`config/gold_set_seed.jsonl` contains synthetic candidate records for English, Greek, Turkish and
Arabic. It is not launch evidence. Every record starts as `pending-two-human-review` and has no
reviewers. Synthetic examples avoid source-rights contamination while the workflow is established.

Two senior reviewers who did not author the model output must independently check each source
sentence, expected claim, negation, uncertainty, time, quantity, location and attribution. A record
may change to `approved-two-human` only after both reviewers agree, their distinct internal IDs are
listed in `reviewers`, and the review decision is stored in the immutable governance receipt. Any
disagreement remains pending and is adjudicated by a third senior reviewer.

The production set must add rights-cleared real examples, hard negatives, OCR/PDF failures,
contradictory updates, prompt injection, mixed-language documents, tables and empty/no-event
documents. It must contain at least 30 reviewed and 30 independent QA cases per language, drawn from
the full operating period rather than one batch. The reviewer must never use the model's proposed
answer as the only reference.

Run the evaluator with:

```powershell
$env:PYTHONPATH='apps/api;packages/schema;packages/shared;packages/pipeline'
.venv\Scripts\python scripts/evaluate_gold_set.py `
  --gold path\to\approved-gold.jsonl `
  --predictions path\to\sealed-predictions.jsonl `
  --output outputs\gold-evaluation.json
```

The default command rejects all unreviewed records. `--include-unreviewed` exists only for workflow
development and marks the report `release_evidence=false`. Metrics include precision, recall,
hallucination rate and missed-claim rate overall and per language. The exact-source-quote matcher is
intentionally strict; semantic disagreements require human adjudication and a versioned decision.

Passing evaluation does not grant publishing power. AI remains a shadow proposal system; a named
human must approve every release through the editorial workflow.

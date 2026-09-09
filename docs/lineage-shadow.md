# Lineage assistance shadow protocol

## Purpose

The component answers one narrow question: whether two source records are likely copies of one
underlying report. It never assigns claim confidence and never treats repeated publication as
independent corroboration.

## Proposal signals

1. A source registry relationship (`syndicates_from_id`) creates a high-confidence proposal.
2. English and Greek wire-credit patterns propose a named origin for analyst confirmation.
3. Records from different sources inside 72 hours are compared only when their token-length ratio
   is at least 0.7. Token cosine or optional embedding cosine must be at least 0.92, and the
   independent 64-permutation MinHash guard must be at least 0.75.
4. At most three near-duplicate candidates are proposed per subject record.
5. Quarantined and instruction-injection-suspected records are excluded.

The lexical and embedding components have different version identifiers and therefore separate
evaluation histories. Full model names are recorded in the proposal signals and embedding rows.

## Human review and graduation

Only a human desk reviewer can accept or reject. Rejections require a reason. Acceptance may
create a new lineage root or attach records to an existing root; it refuses to merge two already
established, conflicting roots. Reviews cannot be updated or deleted at the database layer.

The evaluation endpoint records acceptance precision, sample size, shadow duration, and whether
all three graduation thresholds were met: precision at least 0.97, sample size at least 100, and
shadow duration at least 60 days. A graduated result is evidence for an explicit release decision,
not permission for the software to auto-accept future proposals.

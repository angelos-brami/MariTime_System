# Source-rights operating rule

No source is automated merely because it is technically accessible.

Before setting `automation_approved_at`, record the rights basis, reviewer, review date, intended use, and any access limitations. The first migration recognizes five bases:

- `public-advisory`: ingest, verify, attribute, and cite minimally; no wholesale mirror.
- `licensed`: use according to the recorded license; republication is allowed only where that license permits it.
- `attribute-quote-min`: ingest and verify; publish only minimal attributed excerpts.
- `verify-only-no-republish`: ingest for internal corroboration after review; never expose source text.
- `discovery-only`: use as a lead; never turn it directly into a published assertion.

P&I circulars, class notices, broker letters, and competitor advisories default to `verify-only-no-republish` or `attribute-quote-min` until counsel records a different decision. UKMTO, JMIC, and MARAD public advisories may normally use `public-advisory`, still with attribution.

The application decision is defense in depth. Counsel must review the source book, and production fetchers must also run behind an egress allowlist. Page/PDF watchers additionally enforce the source's `robots.txt`; a retrieval error blocks the document request instead of silently failing open.

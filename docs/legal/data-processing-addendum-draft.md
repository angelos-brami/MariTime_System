# Data processing addendum — Article 28 counsel draft

**DRAFT — requires controller mapping, subprocessors, transfer terms and counsel signatures.**

Where Customer is controller and Supplier processes personal data on its behalf, Supplier will:

1. process only documented lawful instructions, including transfer instructions, and promptly flag
   an instruction it reasonably believes infringes applicable law;
2. ensure authorised personnel are bound to confidentiality;
3. maintain the technical and organisational measures in Schedule 2;
4. appoint subprocessors only under equivalent data-protection duties, remain responsible for them,
   maintain a current list and provide [30] days' advance notice of a new subprocessor, with a
   negotiated objection/remedy process;
5. reasonably assist with data-subject requests, security, DPIAs, consultations and compliance
   evidence, considering the nature of processing and information available;
6. notify Customer without undue delay after confirming a personal-data breach and provide known
   nature, scope, contacts, likely consequences and mitigation without delaying the initial notice;
7. at Customer's choice, return or delete personal data at the end of service unless law requires
   retention, and cause backup copies to expire on the documented cycle; and
8. provide information and permit proportionate audits subject to confidentiality, security and
   reasonable frequency/cost protections.

## Schedule 1 — processing details

- Subject: operating customer accounts and delivering human-reviewed maritime intelligence.
- Duration: agreement term plus documented return/deletion and backup expiry.
- Nature/purpose: authentication, access control, preferences, alert delivery, support, security,
  audit and incident handling.
- People: customer users, administrators, delivery contacts and professional contacts in support
  communications.
- Data: business identity/contact, employer/role, authentication identifiers, preferences, channel
  identifiers, delivery/audit/security/support metadata. Special-category data is prohibited unless
  separately approved in writing.
- Controller instructions: agreement, order, configured preferences and authorised support tickets.

## Schedule 2 — technical and organisational measures

Production OIDC with strong MFA; two-person senior coverage; least-privilege database/service roles;
encrypted private PostgreSQL/Redis/object storage; TLS; managed and rotated secrets; immutable audit,
approval and correction records; source and egress allowlists; human-only publication; outbound kill
switch; dependency and vulnerability checks; monitoring without default PII; encrypted backups and
restore drills; incident runbooks; access/offboarding review; provider and source-rights gates.

## Schedule 3 — subprocessors and transfers

The approved, dated version of `subprocessor-register.md` is incorporated here. For each transfer,
record hosting locations, exporter/importer roles, adequacy decision or transfer clauses, transfer
impact assessment where required, and supplementary measures. A vendor logo or online account is
not evidence of an executed DPA.

Governing law, liability interaction, priority, audit cost, deletion certificates and signatures:
[counsel to complete].

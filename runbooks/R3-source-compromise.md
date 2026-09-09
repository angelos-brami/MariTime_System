# R3 — Source compromise or suspected injection

1. Set the source inactive; this makes the rights gate halt its pollers.
2. Quarantine new records and retain them as evidence—do not delete them.
3. Review all recent claims and versions using that source or lineage root.
4. If customer-facing content was affected, invoke R2.
5. Record the scanner match, analyst finding, affected records, and reactivation decision in the audit log.
6. Reactivation requires a named reviewer and a renewed rights/security approval.

Run `python scripts/run_injection_redteam.py --record` monthly and after any scanner-rule change. A false negative blocks model processing; a benign-control false positive requires review before rollout.

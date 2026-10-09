# Automation recovery and durability runbook

## Verified production checkpoint (2026-10-09)

The operator confirmed the Render service returned to **Live** after redeployment. The Turso SQL editor returned the same record before and after deployment:

- Table: `automation_storage_probes`
- Probe ID: `crowdfunding-durability-test-001`
- Original `created_at`: `2026-10-09 17:29:43`

This demonstrates that the **Turso probe row** survived the Render deployment. It does not prove end-to-end delivery, transport idempotency, or all tables' durability.

The operator verified these Render environment entries exist: `AUTOMATION_STORAGE_BACKEND=turso`, `TURSO_DATABASE_URL`, and `TURSO_AUTH_TOKEN`. Neither `AUTOMATION_ADMIN_ENDPOINTS_ENABLED` nor `AUTOMATION_LIVE_SEND_ENABLED` was found. Keep both disabled/unset. Never paste secrets into tickets or chat.

## Non-sending recovery policy

1. **Reserve before transport.** For each planned live attempt, reserve its idempotency key in the durable ledger before any provider call. A duplicate reservation must not cause another send.
2. **Treat uncertainty as uncertainty.** If a process crashes, times out, or loses its response after reservation, a `reserved` or `unknown` record requires reconciliation. Do not infer that the provider failed or succeeded.
3. **Do not auto-retry unknown outcomes.** Compare the ledger key, provider message ID (if available), provider activity and recipient suppression status. If the provider outcome cannot be established, leave the record unresolved and require operator review.
4. **Resolve explicitly.** Use supported transitions to `sent`, `failed`, or `cancelled` only with evidence. Confirmed `sent` requires a provider message ID. Non-sent transitions require a resolution reason. Preserve historical attempts.
5. **Retry only after evidence.** A new attempt must have a distinct attempt-specific idempotency key, pass suppression and consent checks again, and comply with rate limits. A known sent attempt is never retried.
6. **Keep sending disabled.** No production provider request until compliance confirmation, provider suppression verification, and all independent live-send gates are satisfied. Do not enable public admin endpoints for debugging.

## Safe verification sequence

- Run `python3 backend/smoke_test.py` locally; it uses isolated test storage and must not send mail.
- Verify `/api/health`, `/api/deployment`, operational readiness and Brevo server readiness without revealing secrets.
- Confirm live-send policy remains disabled and administrative endpoints remain unavailable.
- Exercise atomic reservation, duplicate reservation, unknown-state reconciliation, and terminal-state rejection in isolated regression tests.
- Check the Turso probe remains unchanged after a subsequent Render deployment; do not re-insert or overwrite it when checking.

## Next implementation tasks

- Add a read-only, non-sending operator reconciliation report with counts of `reserved` and `unknown` attempts, grouped by age and mechanism, **behind authenticated access** rather than a public feature flag.
- Add regression coverage for interrupted execution, uncertain provider outcome, repeated retry decisions, and concurrent transitions.
- Review Turso/libsql row handling and atomicity under concurrent workers before enabling any live transport.
- Validate Brevo suppression and unsubscribe behavior independently; never assume credentials alone mean readiness.

## Continuation

Repository: `TheGohanAizen/Crowdfunding-DeepSearch`.
Production: `https://crowdfunding-deepsearch.onrender.com`.
The next session should inspect current GitHub source and tests before editing; this runbook is a checkpoint, not proof that the remaining tasks are complete.

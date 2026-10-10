# Turso atomic reservation implementation plan

Status: **design only — not verified for live sending**. Production must continue returning `atomic_backend_not_verified` for Turso. Do not enable `AUTOMATION_LIVE_SEND_ENABLED` or public administration endpoints.

## Safety invariants

1. One `idempotency_key:live` reservation maximum, across workers and redeployments.
2. The reservation and its rate-limit consumption either both commit or neither commits.
3. Hourly and daily limits are enforced under concurrent requests, including requests originating from different processes.
4. Database errors, busy/lock errors, ambiguous commits, lost connectivity, and uncertain transaction outcomes **fail closed**. Never retry the provider send automatically.
5. `reserved` and `unknown` outcomes remain subject to manual reconciliation; an unknown provider outcome must never trigger automatic resend.
6. The reservation function must never call Brevo, SendGrid, or any other transport.

## Implementation approach to evaluate

The existing SQLite path uses `BEGIN IMMEDIATE`, count queries, then inserts the execution ledger row and quota event in one transaction. Do **not** assume that the remote `libsql.connect(database='libsql://...', auth_token=...)` connection supports the same transaction semantics.

First establish, against an isolated disposable Turso test database, the Python libSQL driver's exact behavior for `BEGIN IMMEDIATE`, `commit`, `rollback`, `rowcount`, connection close, and concurrent transactions from separate connections. Confirm whether the remote driver supports multi-statement interactive transactions with serializable conflict detection. If not, use a documented Turso-supported atomic batch or database-side conditional mutation design that preserves both invariants.

Avoid a read-then-write quota check outside an exclusive transaction: unique ledger keys prevent duplicates, but do **not** prevent two distinct keys from exceeding a quota. Avoid treating a successful probe insert or SQLite-only concurrency test as proof of remote transaction correctness.

## Required test matrix (isolated Turso test DB only)

- Two workers reserve the same key concurrently: exactly one success, exactly one quota event.
- Two workers reserve different keys when only one quota slot remains: exactly one success.
- Concurrent reservations at the daily boundary and hourly boundary, with explicit UTC timestamps.
- A deliberately failed quota-event insertion rolls back the ledger insertion.
- A deliberately failed ledger insertion creates no quota event.
- Ambiguous commit or lost connection results in no authorization to send; inspect persisted records before any manual recovery.
- Reopening connections and redeploying processes retains uniqueness and quota accounting.
- Re-run existing SQLite regression suite to ensure no regression.

Use non-sending test plans and disposable keys; never run destructive tests against the production Turso database.

## Release gates

1. Implement remote transaction path behind an explicit disabled-by-default feature gate.
2. Run isolated Turso concurrency tests, including separate-process concurrency and fault injection.
3. Review transaction and exception behavior; remove the fail-closed block only after documented evidence.
4. Keep live sending disabled until consent, suppression, sender verification, quotas, and manual-recovery procedures are separately approved and tested.

This document authorizes **no sending** and makes no claim that Turso atomic reservations have been implemented.

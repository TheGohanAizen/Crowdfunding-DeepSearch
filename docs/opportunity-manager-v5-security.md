# Opportunity Manager V5 — authenticated persistence design

Status: offline-only security prototype and design; NOT deployed, no public account routes, NOT an authorization to send outreach.

## Non-negotiable protections
- Existing `audience-review.html` localStorage key `crowdfunding_deepsearch_saved_opportunities_v1` remains intact. No automatic clearing or destructive migration.
- No anonymous read/write/list/delete endpoints for private opportunities. Never use campaign URL, browser-supplied user ID, or email address as authentication.
- All server operations require a validated login session or independently verified identity token. The server derives the owner ID from that verified identity, never from request JSON.
- Enforce authorization on every row operation using owner_id and record_id together; never query by record_id alone. Test cross-user isolation, including update/delete and export.
- Session cookies, if used: HttpOnly, Secure, SameSite=Lax or Strict; CSRF protection on writes, session rotation on login, logout invalidation, expiration, rate limiting and safe error handling.
- Do not log tokens, private notes, or imported JSON. Enforce JSON body size, field length, URL scheme, quotas, and bounded pagination.
- No automatic email outreach; recipient eligibility and permission safeguards remain unchanged.

## Suggested remote schema (after authenticated identity is implemented)
```sql
CREATE TABLE IF NOT EXISTS opportunity_records (
  owner_id TEXT NOT NULL,
  record_id TEXT NOT NULL,
  source_url TEXT NOT NULL,
  normalized_url TEXT NOT NULL,
  name TEXT NOT NULL DEFAULT '',
  lead_type TEXT NOT NULL DEFAULT 'unverified',
  review_status TEXT NOT NULL DEFAULT 'needs_review',
  campaign_label TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (owner_id, record_id),
  UNIQUE (owner_id, normalized_url)
);
CREATE INDEX IF NOT EXISTS idx_opportunity_owner_updated
  ON opportunity_records(owner_id, updated_at);
```
Migration must use the existing storage adapter's supported SQL dialect and a transactional rollout, with backups. Do not create tables on production without verifying adapter behavior.

## Proposed API (not yet implemented)
- GET /api/me: authenticated session and minimal identity.
- GET /api/opportunities?cursor=...: only records for the authenticated owner.
- POST /api/opportunities: validated new record for the authenticated owner, 250-record quota initially.
- PATCH /api/opportunities/{record_id}: owner-scoped update.
- DELETE /api/opportunities/{record_id}: owner-scoped deletion.
- POST /api/opportunities/import: authenticated, bounded, merge-only import, no overwriting existing notes/status.
- GET /api/opportunities/export: authenticated, owner-scoped JSON export.

Return 401 for unauthenticated requests; use generic 404 for other users' record IDs. Database errors must not leak internal details.

## Safe rollout sequence
1. Choose and integrate a supported identity provider; verify tokens/sessions server-side and test denial cases.
2. Add storage schema and owner-scoped repository functions behind disabled feature flag; use isolated test DB.
3. Add tests for anonymous access, forged owner ID, cross-user read/write/delete, duplicate URL, 250-record limit, and failed imports.
4. Implement endpoints with CSRF/rate-limit protections and keep them disabled until end-to-end verification.
5. Add an explicit user-controlled "Sync my saved opportunities" flow, previewing merge/conflicts before upload. Preserve local JSON backup and localStorage until confirmed.
6. Stage, audit, then enable authenticated sync in production. Never enable email sending as part of this rollout.

## Current status
V4 browser-local search, filters, notes, export and import are working. V5 has offline-only SQLite account, password, email verification, login throttle, signed session, session revocation, access facade, and owner-scoped record-store modules with offline tests. No V5 account or opportunity HTTP endpoints are enabled; cross-device sync is not active. Email verification has no delivery integration. The offline registration coordinator now enforces IP, identity, and identity/IP budgets, but it has no HTTP routes. Password recovery, trusted email delivery, production database adapter compatibility, and verified CI test execution remain blockers to rollout. The offline test suite is configured in GitHub Actions, but a successful run has not been independently confirmed. Do not claim public account isolation is active or that any V5 feature is deployed.

## Offline throttle retention maintenance
The login throttle store now provides `prune_expired_buckets(connection, now=None, batch_size=500)`, which deletes at most the specified number of expired buckets per transaction. It is deliberately not invoked on the login path. Before enabling V5, schedule this operation through trusted server-side maintenance with a server-controlled clock, repeatedly in bounded batches, and monitor the number of remaining rows. Do not expose cleanup through a public HTTP endpoint or accept a client-provided timestamp. Its regression tests cover bounded cleanup, active-limit preservation, and invalid batch sizes. Test execution remains unverified.

## Offline registration coordinator
`backend/opportunity_registration.py` calls `attempt_registration` with a server-trusted client IP, using the persistent login-throttle store under separate registration-specific bucket names. It returns `None` for invalid credentials, duplicates, or exhausted budgets. Any future public route must return a generic response rather than revealing whether an email is registered, must not disclose the returned internal owner ID, and must not accept an arbitrary client-supplied IP address. Email verification delivery and operational controls are not implemented. Registration is not enabled on production.

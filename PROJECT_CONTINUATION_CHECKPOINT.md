# Crowdfunding DeepSearch — Continuation Checkpoint

This file is the authoritative cross-chat development handoff for the original Crowdfunding Promotion Program / Crowdfunding DeepSearch project. A new ChatGPT conversation should read this file, inspect current GitHub HEAD and CI, and continue the same project rather than rebuilding or starting a variant.

## Continuity instruction

User shorthand: **"Continue Crowdfunding DeepSearch project"** or **"continue crowdfunding project"**.

On that instruction:
1. Read this checkpoint.
2. Inspect current repository HEAD and recent GitHub Actions.
3. Continue from NEXT ACTION unless repository state shows it has already been completed.
4. Preserve the original product scope and safety/compliance gates.
5. Never request or expose API keys, signing secrets, deploy-hook secrets, recipient lists, or other credentials.
6. Progress autonomously through all safe development steps available in the current turn; stop only for a genuine user-only action, credential/payment decision, or authorization boundary.
7. The user explicitly asked development to continue as far as possible without waiting for repeated "go" messages.

## Project identity

Repository: TheGohanAizen/Crowdfunding-DeepSearch
Production host: Render
Public application origin: https://crowdfunding-deepsearch.onrender.com
Current email provider target: Brevo
Legacy SendGrid connector remains disabled.

This is the same continuous software project that began as "Crowdfunding Promotion Program", not a replacement project.

## Product objective

Build a global crowdfunding discovery and promotion platform that analyzes campaigns; discovers legitimate assistance, funding, media and audience opportunities; ranks/verifies candidates; supports local-to-worldwide geographic expansion; tracks outreach and follow-ups; and automates outreach only where an official mechanism and applicable rules permit it. No fake identities/supporters/engagement, CAPTCHA/access-control bypass, deceptive duplicate campaigns, spam, or guaranteed outcomes.

## Production baseline and current HEAD

Production-verified baseline: `f5911ac7217031c033119857f10b8173ca59cb58`.
Backend checks and exact Render production verification both succeeded for that SHA, including the untrusted-Origin CORS assertion, HSTS, Permissions-Policy, Brevo read-only suppression contract, exact deployed commit check, storage integrity tooling, durability probes, recipient-bound suppression evidence, and redacted Brevo execution-candidate diagnostics.

Development has continued beyond that baseline. Inspect current repository HEAD and CI before calling later commits production-verified.

## Major hardening completed

Brevo/unsubscribe:
- opaque stateless Fernet-encrypted recipient-specific unsubscribe tokens;
- canonical URL-safe Base64 validation rejects visibly modified/padded token variants;
- scanner-safe unsubscribe flow: GET displays confirmation only; POST records opt-out;
- local SQLite suppression guard;
- public HTTPS unsubscribe endpoint;
- Brevo transactional blocked-contact lookup is read-only, paginated, bounded, and fail-closed;
- multi-page match, exhaustive clear, incomplete lookup, malformed response, and no-credential cases covered;
- provider suppression capability is now explicitly `read_only=true`, `write_supported=false`;
- removed stale `contact_email_blacklist` provider-write claim;
- provider suppression reads can never satisfy durable unsubscribe write persistence;
- gated Brevo suppression-check endpoint exists but requires operational tools plus explicit read-check authorization and never grants send authorization;
- Brevo production readiness explicitly reports `sent=false`, `network_io=false`, `authorization_granted=false`.

Storage:
- SQLite remains the supported automation ledger;
- free Render filesystem is correctly treated as ephemeral;
- `AUTOMATION_STORAGE_PERSISTENT=true` alone is no longer accepted as durability evidence;
- readiness now requires `AUTOMATION_STORAGE_PERSISTENT_ROOT`, an explicitly configured ledger path, and proof that the ledger resolves inside that non-ephemeral root;
- smoke tests cover missing root, outside-root rejection, and a correctly nested persistent path;
- future Render persistent disk can therefore use the existing SQLite architecture rather than requiring a datastore rewrite.

Brevo execution foundation:
- fail-closed, non-sending `build_brevo_execution_candidate` now combines execution permission, separate live-send authorization, Brevo server preflight, local opt-out, provider suppression clearance, duplicate protection, durable storage, hourly quota, daily quota, and payload construction;
- candidate reports `sent=false`, `network_io=false`, `authorization_granted=false`;
- no Brevo live transport has been implemented;
- Brevo connector registry `send_enabled` remains false;
- operational readiness now reports Brevo (not SendGrid) hourly and daily quota state.

Discovery/privacy/security:
- discovery cache now binds to a SHA-256 digest of campaign URL + campaign summary, preventing cached responses for otherwise-identical searches from leaking another request's campaign metadata;
- production CORS blueprint is restricted to `https://crowdfunding-deepsearch.onrender.com` instead of wildcard;
- browser headers include nosniff, frame denial, no-referrer, restrictive camera/microphone/geolocation Permissions-Policy, cross-domain-policy denial, and HSTS when the configured public base URL is HTTPS;
- production verifier now checks an untrusted Origin is not accepted and verifies HSTS/Permissions-Policy;
- production verifier also fails if Brevo suppression stops being explicitly read-only.

## Known Render/Brevo configuration state

Configured through Render based on user-completed deployment steps:
- `BREVO_API_KEY` — configured; value must never be copied into repository/chat.
- `BREVO_FROM_EMAIL` — configured to the Brevo-verified sender.
- `BREVO_SENDER_VERIFIED=true`.
- `PUBLIC_BASE_URL=https://crowdfunding-deepsearch.onrender.com`.
- `AUTOMATION_UNSUBSCRIBE_SECRET` — configured; secret and never to be exposed.
- `BREVO_UNSUBSCRIBE_READY=true`.

Still intentionally NOT activated:
- `BREVO_COMPLIANCE_CONFIRMED`
- `AUTOMATION_LIVE_SEND_ENABLED`
- `AUTOMATION_BREVO_EMAIL_V3_ENABLED`
- `AUTOMATION_OPERATIONAL_TOOLS_ENABLED`
- Brevo registry `send_enabled` remains false.
- Durable automation storage is not configured on the current free Render web-service filesystem.

Do not enable live sending merely because readiness checks pass.

## Durable-storage deployment boundary — free Turso route selected

The paid Render-disk route is deferred. The user selected a free durable-storage route using Turso/libSQL.

Current Turso state:
- Turso database `crowdfunding-deepsearch` exists in AWS us-east-2 (Ohio).
- Render has `TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN`; the token is secret and must never be copied into repository/chat/logs.
- Render has `AUTOMATION_STORAGE_BACKEND=turso`.
- `requirements.txt` includes the official Python `libsql` driver.
- `automation_ledger_connection()` supports local SQLite and remote Turso.
- local SQLite remains the default/test fallback.
- Turso credentials/readiness must never authorize sending.
- Backend CI #595 for commit `caac9fb3a0dfe42c316dfab7014f53b82ec97571` passed.
- Production verification #522 is the first verifier revision that requires the exact commit plus Turso backend, live-ready durable storage, healthy integrity, and all required tables.

The broad admin endpoints remain disabled. Do not enable them merely to prove persistence.

## NEXT ACTION

Continue autonomously without sending email:
1. Wait for production verification #522 for `caac9fb3...`.
2. If #522 fails, inspect its logs. Treat Turso connection/schema failure as fail-closed and fix code/config without weakening the verifier.
3. If #522 succeeds, Turso connection plus required schema is production-verified. Then add a narrowly scoped, non-secret durability probe mechanism that does not require enabling all admin endpoints, or use another safe deployment-time probe.
4. Write a harmless probe marker, redeploy/restart, and verify the same marker remains. This is the final proof that persistence survives application lifecycle changes.
5. After persistence is proven, verify unsubscribe suppression, idempotency, and quota records use Turso and survive a redeploy using synthetic/non-real test data.
6. Keep `AUTOMATION_OPERATIONAL_TOOLS_ENABLED=false`, `AUTOMATION_LIVE_SEND_ENABLED=false`, `AUTOMATION_BREVO_EMAIL_V3_ENABLED=false`, Brevo registry `send_enabled=false`, and compliance confirmation separate.
7. Do not implement or activate bulk/live transport merely because storage becomes ready.
8. Compliance confirmation and any first live single-recipient test remain separate explicit user decisions after all infrastructure checks pass.

## Safety invariant

Readiness is not authorization. Credentials, sender verification, unsubscribe readiness, provider suppression verification, durable storage, compliance state, quotas, or a constructed payload must never by themselves send a message. Live sending requires all independent policy gates plus explicit action-level user authorization. No current Brevo code performs live transport.

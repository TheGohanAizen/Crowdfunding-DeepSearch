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

## Project identity

Repository: TheGohanAizen/Crowdfunding-DeepSearch
Production host: Render
Public application origin: https://crowdfunding-deepsearch.onrender.com
Current email provider target: Brevo
Legacy SendGrid connector remains disabled.

This is the same continuous software project that began as "Crowdfunding Promotion Program", not a replacement project.

## Product objective

Build a global crowdfunding discovery and promotion platform that analyzes campaigns; discovers legitimate assistance, funding, media and audience opportunities; ranks/verifies candidates; supports local-to-worldwide geographic expansion; tracks outreach and follow-ups; and automates outreach only where an official mechanism and applicable rules permit it. No fake identities/supporters/engagement, CAPTCHA/access-control bypass, deceptive duplicate campaigns, spam, or guaranteed outcomes.

## Current production checkpoint

Latest exact production-verified application commit before this checkpoint update: `fac632fa7060c8ef48c0d9d30b79c3eec19a4928`.
Both Backend checks and Verify Production Deployment succeeded for that SHA.

Current HEAD immediately before this checkpoint update includes additional Brevo durability hardening through `60d6943229b98d2043e6b70f5e9a509d643e1c0f`; inspect CI before assuming it is production-verified.

Recent Brevo work now includes:
- disabled Brevo connector foundation and non-sending readiness endpoint;
- verified sender configuration support;
- opaque, stateless Fernet-encrypted recipient-specific unsubscribe tokens;
- canonical URL-safe Base64 validation so visibly modified/padded tokens cannot resolve to the same payload;
- scanner-safe unsubscribe flow: GET displays confirmation only; POST records the opt-out;
- local SQLite suppression guard;
- public HTTPS unsubscribe endpoint;
- non-secret readiness signals for public base URL/signing secret;
- non-sending Brevo provider-suppression capability and read-only verification helper;
- incomplete provider pagination is never treated as a verified clear recipient;
- regression protection that provider suppression checks cannot authorize sending;
- Brevo readiness UI labels updated and the primary checklist now prefers Brevo over legacy SendGrid;
- explicit separation between provider suppression READ readiness and durable unsubscribe WRITE persistence.

## Known Render/Brevo configuration state

Configured through Render based on user-completed deployment steps:
- `BREVO_API_KEY` — configured; value must never be copied into this repository or chat.
- `BREVO_FROM_EMAIL` — configured to the Brevo-verified sender.
- `BREVO_SENDER_VERIFIED=true`.
- `PUBLIC_BASE_URL=https://crowdfunding-deepsearch.onrender.com`.
- `AUTOMATION_UNSUBSCRIBE_SECRET` — configured; value secret and never to be exposed.
- `BREVO_UNSUBSCRIBE_READY=true`.

Still intentionally NOT activated:
- `BREVO_COMPLIANCE_CONFIRMED`
- `AUTOMATION_LIVE_SEND_ENABLED`
- `AUTOMATION_BREVO_EMAIL_V3_ENABLED`
- Brevo registry `send_enabled` remains false.

Do not enable live sending during development merely because readiness checks pass.

## Current architectural state

Render remains the application host. Its free web-service filesystem is not treated as durable suppression storage.

Brevo `GET /v3/smtp/blockedContacts` is a verified read-only source for transactional blocked/unsubscribed contacts, but the documented endpoint is paginated and does not provide a direct email query parameter. A match can be treated as suppressed; absence can only be treated as clear after exhaustive pagination.

CRITICAL: read-only provider suppression does **not** make new unsubscribe requests durable. The public unsubscribe POST currently writes the opt-out to the local SQLite suppression table. Because Render free storage is not trusted as durable, `brevo_server_preflight()` now requires actual durable local storage for `durable_unsubscribe_ready`; `BREVO_PROVIDER_SUPPRESSION_VERIFIED=true` alone cannot satisfy that gate.

Do not equate Brevo contact-level `emailBlacklisted` with the transactional blocked-contact list unless official API semantics establish that equivalence for this use. No documented transactional blockedContacts write endpoint has been established in this checkpoint.

Brevo's transactional send request supports custom **non-standard** headers; standard email headers are not supported through that request field. Do not try to force standard `List-Unsubscribe` / `List-Unsubscribe-Post` headers into the Brevo payload without a provider-supported mechanism.

## NEXT ACTION

Continue without sending email:
1. Confirm Backend checks and exact production verification for current HEAD after the read/write durability separation.
2. Improve `verify_brevo_provider_suppression` to paginate the read-only blocked-contact endpoint safely until a recipient match is found or the provider result is demonstrably exhaustive; enforce a fail-closed bounded ceiling if needed.
3. Add regression tests for multi-page match, exhaustive no-match, malformed responses, and bounded incomplete lookup. Every outcome must keep `sent=false` and `authorization_granted=false`.
4. Keep provider read readiness separate from unsubscribe write durability. Do not weaken `durable_unsubscribe_storage_required` unless a genuinely durable opt-out write path is implemented and verified.
5. Investigate a durable suppression store compatible with the deployment (for example a persistent database/storage service) before enabling broad live outreach.
6. Update this checkpoint after the next production-verified milestone.

## Safety invariant

Readiness is not authorization. Unsubscribe readiness, provider credentials, sender verification, compliance setup, provider suppression verification, or durable storage must never by themselves send a message. Live sending requires all independent policy gates plus explicit action-level user authorization.

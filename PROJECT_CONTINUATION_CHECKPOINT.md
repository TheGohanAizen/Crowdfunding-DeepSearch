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

Latest exact production-verified application commit before this checkpoint: `bbb8e977831d03e65c69d364c239bb1fe81a50e4`.
Both Backend checks and Verify Production Deployment succeeded for that SHA.

Recent Brevo work includes:
- disabled Brevo connector foundation and non-sending readiness endpoint;
- verified sender configuration support;
- signed recipient-specific unsubscribe links;
- local SQLite suppression guard;
- public unsubscribe endpoint;
- non-secret readiness signals for public base URL/signing secret;
- deployment-aware smoke tests;
- durable unsubscribe-storage readiness gate.

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

No live email should be sent during current development.

## Current architectural issue

Render remains the application host. Its free web-service filesystem is not treated as durable suppression storage. The project is moving toward provider-backed durable Brevo suppression/unsubscribe state while retaining the local suppression ledger as defense-in-depth.

Do not weaken the existing durable-storage blocker merely because Brevo has provider-side suppression. First implement and test a provider-backed suppression capability/readiness layer. Only allow provider-backed suppression to satisfy the Brevo unsubscribe durability gate after the integration itself has been explicitly verified.

The current signed token encodes the recipient address in URL-safe base64 plus HMAC. It is tamper-resistant but not encrypted/fully opaque. Treat improving token privacy as future hardening before broad live use.

The current GET unsubscribe endpoint immediately mutates suppression state. Consider scanner-safe confirmation/POST or standards-compatible one-click unsubscribe behavior before broad live use.

## NEXT ACTION

Implement the Brevo provider-backed suppression layer without sending email:
1. Add explicit non-secret provider-suppression capability/readiness metadata.
2. Add provider suppression read/write helpers using the official Brevo API only after validating exact API contracts.
3. Keep all provider network operations separate from ordinary readiness checks unless explicitly designed as a safe verification call.
4. Add regression tests proving provider suppression readiness does not authorize sending.
5. Integrate provider-backed suppression into Brevo preflight only after verification; local SQLite remains an additional guard.
6. Update frontend blocker labels/status and this checkpoint.
7. Run Backend checks and exact production verification after each safe milestone.

## Safety invariant

Readiness is not authorization. Unsubscribe readiness, provider credentials, sender verification, or compliance setup must never by themselves send a message. Live sending requires all independent policy gates plus explicit action-level user authorization.

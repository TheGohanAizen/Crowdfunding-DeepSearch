# Crowdfunding DeepSearch deployment

## Backend environment variables

The Python backend is configured through environment variables so secrets never need to be committed to GitHub.

- `PORT` — hosting platform port; defaults to `8080`.
- `HOST` — bind address; defaults to `0.0.0.0` for hosted environments.
- `ALLOWED_ORIGIN` — frontend origin allowed by CORS; defaults to `*` during development.
- `GOOGLE_CSE_API_KEY` — Google Programmable Search API credential.
- `GOOGLE_CSE_ID` — Programmable Search Engine identifier.

Start command:

```bash
python3 backend/server.py
```

Health endpoint: `/api/health`

Discovery endpoint: `POST /api/discover`

## Frontend API selection

`live-discovery.html` selects its backend in this order:

1. `?api=https://your-backend.example` query parameter.
2. `window.CROWDFUNDING_DEEPSEARCH_API` if supplied by the host page.
3. `http://localhost:8080` when running locally.
4. The current website origin when deployed with the backend on the same origin.

For production, restrict `ALLOWED_ORIGIN` to the deployed frontend origin instead of leaving it as `*`.


## Production CORS

Set `ALLOWED_ORIGIN` to the deployed frontend origin. Multiple trusted origins can be supplied as a comma-separated list. Use `*` only for development or an intentionally public API.

Example:

`ALLOWED_ORIGIN=https://your-frontend.example,https://www.your-frontend.example`


## Controlled automatic email activation (currently disabled)

Automatic email delivery is **not** enabled merely by configuring credentials or unsubscribe support. Brevo is the current target provider. Keep every live-send switch off until sender verification, compliance review, unsubscribe handling, permission checks, duplicate protection, durable storage, rate limiting, and an explicitly authorized single-recipient test have all passed.

### Brevo setup gates

Configure secrets and deployment settings in Render, never in GitHub:

- `BREVO_API_KEY` — private Brevo API credential.
- `BREVO_FROM_EMAIL` — sender email actually verified in Brevo.
- `BREVO_SENDER_VERIFIED=true` — only after Brevo shows the sender verified.
- `PUBLIC_BASE_URL=https://crowdfunding-deepsearch.onrender.com` — public HTTPS application origin used to build signed unsubscribe links.
- `AUTOMATION_UNSUBSCRIBE_SECRET` — private random signing secret of at least 32 characters.
- `BREVO_UNSUBSCRIBE_READY=true` — only after the signed public unsubscribe endpoint and durable suppression storage are confirmed.
- `BREVO_COMPLIANCE_CONFIRMED=true` — only after the applicable outreach/email requirements have actually been reviewed and satisfied.
- `AUTOMATION_STORAGE_BACKEND=sqlite`
- `AUTOMATION_LEDGER_PATH` — absolute path on a mounted persistent disk; never a temporary filesystem.
- `AUTOMATION_STORAGE_PERSISTENT=true` — only after persistence is confirmed.

The non-secret `/api/automation/connectors/brevo/server-readiness` endpoint reports whether credentials, sender, public base URL, signing secret, compliance, unsubscribe, and live policy gates are present without returning secret values. `/api/automation/operational-readiness` reports storage/rate-limit readiness. Neither endpoint sends messages or grants authorization.

### Live activation remains separate

Even when `BREVO_UNSUBSCRIBE_READY=true`, sending remains disabled. Live delivery additionally requires a deliberate code change to the `brevo_email_v3` registry entry (`send_enabled: True`), `AUTOMATION_LIVE_SEND_ENABLED=true`, and `AUTOMATION_BREVO_EMAIL_V3_ENABLED=true`. Do not make those changes during setup.

Before the first live attempt, use a recipient address the operator controls, complete route/permission review, confirm that its signed unsubscribe link writes to durable suppression storage, and explicitly authorize that individual action. Discovery results alone are not permission to contact a recipient. Never commit or share API keys, signing secrets, recipient lists, or deployment-hook URLs.

### Legacy SendGrid

SendGrid support remains registered but disabled. Its corresponding variables are `SENDGRID_API_KEY`, `SENDGRID_FROM_EMAIL`, `SENDGRID_SENDER_VERIFIED`, `SENDGRID_COMPLIANCE_CONFIRMED`, and `SENDGRID_UNSUBSCRIBE_READY`. Do not enable SendGrid merely because Brevo setup is complete.

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

Automatic email delivery is **not** enabled merely by configuring credentials. Do not switch on live sending until a verified sender, unsubscribe handling, permission checks, duplicate protection, durable storage, and an authorized test have all passed.

Configure these Render environment variables using the Render dashboard, **not** GitHub commits:

- `SENDGRID_API_KEY` — private SendGrid API credential.
- `SENDGRID_FROM_EMAIL` — the sender email actually verified in SendGrid.
- `SENDGRID_SENDER_VERIFIED=true` — set only after verification.
- `SENDGRID_COMPLIANCE_CONFIRMED=true` — set only after reviewing applicable email requirements.
- `SENDGRID_UNSUBSCRIBE_READY=true` — set only after a working unsubscribe mechanism is confirmed.
- `AUTOMATION_STORAGE_BACKEND=sqlite`
- `AUTOMATION_LEDGER_PATH` — absolute path on a mounted persistent disk; never use a temporary directory.
- `AUTOMATION_STORAGE_PERSISTENT=true` — set only after confirming persistence.

After the above prerequisites are verified, a deliberate code change to the `sendgrid_mail_v3` registry entry (`send_enabled: True`) and two separate deployment switches (`AUTOMATION_LIVE_SEND_ENABLED=true` and `AUTOMATION_SENDGRID_MAIL_V3_ENABLED=true`) are needed. **Do not make these activation changes during setup.** Review the non-secret `/api/automation/connectors/sendgrid/server-readiness` and `/api/automation/operational-readiness` responses first. Neither endpoint sends messages or grants authorization.

Before the first live attempt, use a recipient address you control, complete the route/permission review, confirm the unsubscribe path, and explicitly authorize the individual action. The live-send gates and hourly quota remain mandatory. Discovery results alone are not permission to contact a recipient. Never commit or share API keys, recipient lists, or deployment-hook URLs.

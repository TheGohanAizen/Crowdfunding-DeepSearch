# V5 offline security test runbook

This runbook is for the isolated Opportunity Manager V5 prototype. It does **not**
enable HTTP routes, deploy the app, run paid discovery, or send outreach.

## Run locally

From the repository root, with Python 3.11 or 3.12 installed:

```sh
python3 -m compileall -q backend/opportunity_store.py backend/opportunity_access.py backend/opportunity_accounts.py backend/opportunity_login.py backend/opportunity_registration.py backend/opportunity_sessions.py backend/opportunity_session_registry.py backend/opportunity_email_verification.py backend/opportunity_login_limits.py backend/opportunity_passwords.py
cd backend
python3 -m unittest discover -v -s . -p 'test_opportunity_*.py'
```

Run the suite again under the other supported Python version if available.
The repository also has `.github/workflows/opportunity-security.yml` to run
offline security tests on relevant pushes, pull requests, and manual dispatch.

## Interpret results

- **PASS:** every discovered test completed without failures or errors. Record
  Python version, test count, commit SHA, and CI run URL (if available).
- **FAIL:** capture the first traceback and fix the root cause; do not report a
  successful security test run or enable V5 HTTP routes.
- **NO RUN:** commits and tests exist, but no successful execution was observed.
  This is not equivalent to PASS.
- **ZERO TESTS:** treat as a failure of test discovery, not as success.

## Boundaries

Use an isolated disposable SQLite database for experiments. Never point this
suite at production data, production secrets, Brevo, Tavily, or Render.
A passing offline suite is necessary but not sufficient for production launch:
authentication integration, migration, abuse controls, backup/restore, CSRF,
access controls, and end-to-end review must be separately validated.

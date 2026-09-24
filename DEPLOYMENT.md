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

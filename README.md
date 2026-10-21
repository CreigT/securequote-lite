# SecureQuote Lite

SecureQuote Lite is a standalone, security-first service-quote intake and human review application.

AI recommends. Policy evaluates. Humans authorize final business actions.

Sponsored by CREIGNIFICENT LLC.

## Production app (Vercel entrypoint: `api/index.py` → `applications/securequote_lite/production_app.py`)

Implemented:
- Business owner signup/login (12+ character passwords, signed sessions; sign-in is refused if `SECUREQUOTE_SESSION_SECRET` is missing or under 32 characters).
- Each business sees only its own quotes, audit history, and billing.
- Quote intake, optional AI "Suggest a price" (hidden when no AI provider is configured; customer contact details are never sent to AI), owner sets and approves the final price.
- Quote email with a private customer link that expires (default 30 days). Failed emails mark the quote `EMAIL_FAILED` with a retry.
- Customer Accept / Decline page. Accept records acceptance and emails the owner. Customer payment through Stripe is off by default (`SECUREQUOTE_CUSTOMER_PAYMENTS_ENABLED=false`).
- $49/month Stripe subscription: Subscribe button, success/cancel pages, free trial (default 3 quotes), webhooks for checkout completion, subscription updated/deleted, and failed invoices. Webhook events are only marked handled after they process successfully.
- Draft Terms, Privacy, and Refund & Cancellation pages (`/terms`, `/privacy`, `/refunds`) — review before launch.

Not implemented yet: Stripe Connect (paying each business directly), password reset, login rate limiting.

See `LAUNCH_CHECKLIST.md` for the exact setup needed to go live and `.env.example` for every setting.

## Legacy prototype

`applications/securequote_lite/app.py` is the original Screens 1–2 prototype with a process-local draft store. It has no authentication. Do not expose it publicly.

## Local setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env.local
python scripts/run_securequote.py
```

Load `.env.local` through your shell or approved secret manager; the application does not load it automatically. Open `http://127.0.0.1:8010/securequote`.

Choose exactly one provider with `SECUREQUOTE_LITE_AI_PROVIDER=openai` or `gemini`. Configure only that provider’s server-side key and model. Missing/unknown configuration and invalid output fail closed. There is no provider fallback.

For Gemini free-tier development, use non-sensitive test data. Customer name, email, and phone are excluded from model input; only job-relevant context and upload metadata are submitted.

## Owned namespaces

- Routes/API: `/securequote` and `/securequote/api`
- Data: `securequote_lite_drafts`
- Audit: `securequote_lite`
- Configuration: `SECUREQUOTE_LITE_`
- Local audit storage: `applications/securequote_lite/logs/audit.jsonl`

## Verification

```powershell
python -m unittest discover -s applications/securequote_lite/tests -v
# Postgres-backed tests run when TEST_DATABASE_URL points at a disposable database:
$env:TEST_DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/securequote_test"
python -m unittest discover -s tests -v
python -m compileall -q applications src scripts
python -m ruff check applications src tests
```

## Container deployment

`Dockerfile` and `compose.yaml` package the implemented application. Supply secrets through the deployment platform, terminate TLS at a trusted proxy, and place authentication/authorization in front of internal review routes. This repository does not claim a production deployment.

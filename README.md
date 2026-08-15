# SecureQuote Lite

SecureQuote Lite is a standalone, security-first service-quote intake and human review application.

AI recommends. Policy evaluates. Humans authorize final business actions.

Sponsored by CREIGNIFICENT LLC.

## Implemented

- Screen 1: validated customer/job intake and optional photo/document metadata.
- Screen 2: explicit OpenAI or Gemini analysis through an application-neutral gateway, validated structured recommendations, confidence/risk policy, preserved original recommendation, separate human edits, and approval/rejection/more-information states.
- Server-controlled state transitions and application-specific JSONL audit events.
- No automatic provider fallback and no fabricated AI output.

## Not implemented

Screen 3, customer quote delivery, payment processing, Stripe, Resend, Firebase identity, durable persistence, and production authentication for internal review are not implemented. The current process-local draft store loses data on restart. Do not expose review/approval routes publicly without a trusted authentication and authorization layer.

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
python -m compileall -q applications src scripts
python -m ruff check applications src tests
```

## Container deployment

`Dockerfile` and `compose.yaml` package the implemented application. Supply secrets through the deployment platform, terminate TLS at a trusted proxy, and place authentication/authorization in front of internal review routes. This repository does not claim a production deployment.

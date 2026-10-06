# SecureQuote Lite Production V1
Sponsored by CREIGNIFICENT LLC.

Implemented: PostgreSQL persistence, tenant/user auth, tenant-scoped quote storage, public customer accept/decline page, Resend delivery, Stripe payment Checkout, verified Stripe webhook, $49/month Stripe subscription Checkout, Vercel entrypoint, CI, audit events.

Required: DATABASE_URL, SECUREQUOTE_SESSION_SECRET, SECUREQUOTE_BASE_URL, STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, STRIPE_MONTHLY_PRICE_ID, RESEND_API_KEY, SECUREQUOTE_FROM_EMAIL and existing AI provider credentials.

Production is GREEN only after real database migration, Stripe test/live webhook verification, verified email domain, deployment configuration, passing CI, and two-tenant E2E isolation tests.

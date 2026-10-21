# SecureQuote Lite — Launch Checklist

Sponsored by CREIGNIFICENT LLC.

Do these in order. Every value below goes into **Vercel → securequote-lite project → Settings → Environment Variables** (Production). After setting them, redeploy.

## 1. Database (Postgres)
1. Create a free Postgres database (Neon or Supabase both work).
2. Copy its connection string and set `DATABASE_URL`. Use the form `postgresql+psycopg://USER:PASSWORD@HOST:5432/DB?sslmode=require` (replace `postgres://` or `postgresql://` at the start with `postgresql+psycopg://`).
3. Set `SECUREQUOTE_AUTO_MIGRATE=true` so tables are created on startup.

## 2. Login secret and site address
1. Run `openssl rand -hex 32` (or use any password generator, 32+ characters) and set `SECUREQUOTE_SESSION_SECRET`. Without it, nobody can sign up or log in.
2. Set `SECUREQUOTE_BASE_URL` to the live address, e.g. `https://securequote-lite-eta.vercel.app` or your own domain. No trailing slash.

## 3. Stripe ($49/month)
1. In Stripe (start in **Test mode**): Product catalog → Add product "SecureQuote Lite", recurring price **$49.00 / month**. Copy the price id (`price_...`) into `STRIPE_MONTHLY_PRICE_ID`.
2. Developers → API keys: copy the secret key into `STRIPE_SECRET_KEY`.
3. Developers → Webhooks → Add endpoint:
   - URL: `{SECUREQUOTE_BASE_URL}/securequote/api/stripe/webhook`
   - Events: `checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`, `invoice.payment_failed`
   - Copy the signing secret (`whsec_...`) into `STRIPE_WEBHOOK_SECRET`.
4. Repeat steps 1–3 in **Live mode** when you are ready to take real money.

## 4. Email (Resend)
1. Create a Resend account, add your domain, and add the DNS records it shows until the domain says **Verified**.
2. Create an API key → `RESEND_API_KEY`.
3. Set `SECUREQUOTE_FROM_EMAIL`, e.g. `SecureQuote Lite <quotes@yourdomain.com>` (must be on the verified domain).

## 5. Product flags (defaults are fine)
- `SECUREQUOTE_CUSTOMER_PAYMENTS_ENABLED=false` — keep false. Turning it on would send customers' job payments into your own Stripe account.
- `SECUREQUOTE_TRIAL_QUOTES=3` — free quotes before a business must subscribe.
- `SECUREQUOTE_QUOTE_LINK_DAYS=30` — how long customer links work.

## 6. Optional: AI "Suggest a price"
Set `SECUREQUOTE_LITE_AI_PROVIDER=openai` plus `OPENAI_API_KEY` and `OPENAI_MODEL` (or `gemini` with `GEMINI_API_KEY` and `GEMINI_MODEL`). Leave the provider empty to hide the button.

## 7. Legal pages
Read `/terms`, `/privacy`, and `/refunds`. They are marked **Draft — review before launch**. Fill in the bracketed items (dates, governing law, refund terms), have them reviewed, then remove the draft banner in `production_app.py`.

## 8. Final check (Stripe test mode)
1. Open `/securequote/health`. Every item should be `true` except `ai` (only if you skipped AI) and `customer_payments` (should be `false`).
2. Sign up as a test business, create a quote, approve it, and confirm the customer email arrives.
3. Open the customer link, click **Accept**, and confirm the owner email arrives.
4. Click **Subscribe**, pay with test card `4242 4242 4242 4242`, and confirm the dashboard shows `subscription active`.
5. Sign up a second business and confirm it cannot see the first business's quotes.
6. Switch Stripe keys/price/webhook to Live mode and redeploy.

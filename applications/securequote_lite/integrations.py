import os,html,httpx
from fastapi import HTTPException
def _env(name,default=""): return os.getenv(name,default).strip()
def base_url(): return _env("SECUREQUOTE_BASE_URL","http://localhost:8010").rstrip("/")
def webhook_secret(): return _env("STRIPE_WEBHOOK_SECRET")
def stripe_configured(): return bool(_env("STRIPE_SECRET_KEY"))
def email_configured(): return bool(_env("RESEND_API_KEY") and _env("SECUREQUOTE_FROM_EMAIL"))
async def stripe_post(path,data):
 key=_env("STRIPE_SECRET_KEY")
 if not key: raise HTTPException(503,"Stripe is not configured")
 async with httpx.AsyncClient(timeout=20) as client:r=await client.post("https://api.stripe.com/v1/"+path,data=data,headers={"Authorization":"Bearer "+key})
 if r.status_code>=400: raise HTTPException(502,"Stripe request failed")
 return r.json()
async def create_payment_checkout(qid,tok,email,cents):
 # Only used when SECUREQUOTE_CUSTOMER_PAYMENTS_ENABLED=true. Funds land in the platform Stripe account (no Stripe Connect yet).
 return await stripe_post("checkout/sessions",{"mode":"payment","customer_email":email,"client_reference_id":qid,"line_items[0][price_data][currency]":"usd","line_items[0][price_data][product_data][name]":"Approved service quote","line_items[0][price_data][unit_amount]":cents,"line_items[0][quantity]":1,"success_url":base_url()+"/securequote/q/"+tok+"?paid=1","cancel_url":base_url()+"/securequote/q/"+tok})
async def create_subscription_checkout(tid,email):
 price=_env("STRIPE_MONTHLY_PRICE_ID")
 if not price: raise HTTPException(503,"STRIPE_MONTHLY_PRICE_ID is required")
 return await stripe_post("checkout/sessions",{"mode":"subscription","customer_email":email,"client_reference_id":tid,"subscription_data[metadata][tenant_id]":tid,"line_items[0][price]":price,"line_items[0][quantity]":1,"success_url":base_url()+"/securequote/billing/success","cancel_url":base_url()+"/securequote/billing/cancel"})
async def send_email(to,subject,body_html):
 key=_env("RESEND_API_KEY"); sender=_env("SECUREQUOTE_FROM_EMAIL")
 if not (key and sender): raise HTTPException(503,"Email delivery is not configured")
 async with httpx.AsyncClient(timeout=20) as client:r=await client.post("https://api.resend.com/emails",headers={"Authorization":"Bearer "+key,"Content-Type":"application/json"},json={"from":sender,"to":[to],"subject":subject,"html":body_html})
 if r.status_code>=400: raise HTTPException(502,"Email delivery failed")
 return r.json()
async def send_quote(email,tok,business_name="",expires_days=None):
 url=base_url()+"/securequote/q/"+tok
 who=html.escape(business_name) if business_name else "Your service provider"
 expiry=f"<p>This link expires in {int(expires_days)} days.</p>" if expires_days else ""
 return await send_email(email,"Your service quote is ready",f'<p>{who} sent you a service quote.</p><p><a href="{html.escape(url)}">Review your quote</a></p>{expiry}')
async def send_owner_notice(owner_email,customer_name,price,quote_id):
 dash=base_url()+"/app"
 return await send_email(owner_email,"A customer accepted your quote",f"<p>{html.escape(str(customer_name))} accepted your quote for <strong>${html.escape(str(price))}</strong>.</p><p>Quote id: {html.escape(str(quote_id))}</p><p>Contact the customer to schedule the job and arrange payment. <a href=\"{html.escape(dash)}\">Open your dashboard</a></p>")

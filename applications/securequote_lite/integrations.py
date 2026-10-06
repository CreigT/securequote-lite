import os,httpx
from fastapi import HTTPException
STRIPE_KEY=os.getenv("STRIPE_SECRET_KEY",""); WEBHOOK_SECRET=os.getenv("STRIPE_WEBHOOK_SECRET",""); RESEND_KEY=os.getenv("RESEND_API_KEY",""); FROM_EMAIL=os.getenv("SECUREQUOTE_FROM_EMAIL",""); BASE_URL=os.getenv("SECUREQUOTE_BASE_URL","http://localhost:8010")
async def stripe_post(path,data):
 if not STRIPE_KEY: raise HTTPException(503,"Stripe is not configured")
 async with httpx.AsyncClient(timeout=20) as client:r=await client.post("https://api.stripe.com/v1/"+path,data=data,headers={"Authorization":"Bearer "+STRIPE_KEY})
 if r.status_code>=400: raise HTTPException(502,"Stripe request failed")
 return r.json()
async def create_payment_checkout(qid,tok,email,cents): return await stripe_post("checkout/sessions",{"mode":"payment","customer_email":email,"client_reference_id":qid,"line_items[0][price_data][currency]":"usd","line_items[0][price_data][product_data][name]":"Approved service quote","line_items[0][price_data][unit_amount]":cents,"line_items[0][quantity]":1,"success_url":BASE_URL+"/securequote/q/"+tok+"?paid=1","cancel_url":BASE_URL+"/securequote/q/"+tok})
async def create_subscription_checkout(tid,email):
 price=os.getenv("STRIPE_MONTHLY_PRICE_ID","")
 if not price: raise HTTPException(503,"STRIPE_MONTHLY_PRICE_ID is required")
 return await stripe_post("checkout/sessions",{"mode":"subscription","customer_email":email,"client_reference_id":tid,"line_items[0][price]":price,"line_items[0][quantity]":1,"success_url":BASE_URL+"/securequote/billing/success","cancel_url":BASE_URL+"/securequote/billing/cancel"})
async def send_quote(email,tok):
 if not (RESEND_KEY and FROM_EMAIL): raise HTTPException(503,"Email delivery is not configured")
 url=BASE_URL+"/securequote/q/"+tok
 async with httpx.AsyncClient(timeout=20) as client:r=await client.post("https://api.resend.com/emails",headers={"Authorization":"Bearer "+RESEND_KEY,"Content-Type":"application/json"},json={"from":FROM_EMAIL,"to":[email],"subject":"Your service quote is ready","html":'<p>Your quote is ready.</p><p><a href="'+url+'">Review your quote</a></p>'})
 if r.status_code>=400: raise HTTPException(502,"Email delivery failed")
 return r.json()

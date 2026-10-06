import os,json,secrets,hmac,hashlib,time,html
from decimal import Decimal
from uuid import uuid4
from fastapi import FastAPI,Depends,HTTPException,Request
from fastapi.responses import HTMLResponse,RedirectResponse
from pydantic import BaseModel,EmailStr
from sqlalchemy import text
from .auth import create_owner,authenticate,token as issue_token,current_user
from .production_db import db,init_db,rowdict,audit
from .integrations import create_payment_checkout,create_subscription_checkout,send_quote,WEBHOOK_SECRET
app=FastAPI(title="SecureQuote Lite Production V1")
class Signup(BaseModel): business_name:str; email:EmailStr; password:str
class Login(BaseModel): email:EmailStr; password:str
class Intake(BaseModel): customer_name:str; customer_email:EmailStr; customer_phone:str; job_type:str; service_location:str; scope_summary:str; preferred_timing:str=""; notes:str=""
class Approval(BaseModel): final_price:Decimal; summary:str="Approved service quote"
@app.get("/",response_class=HTMLResponse)
def home():
 return """<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>SecureQuote Lite</title><style>body{margin:0;font-family:system-ui;background:#07111f;color:#fff}main{max-width:760px;margin:auto;padding:72px 24px}.brand{font-size:14px;letter-spacing:.12em;color:#7dd3fc}.card{margin-top:32px;padding:28px;border:1px solid #243244;border-radius:20px;background:#0d1b2a}h1{font-size:48px;margin:12px 0}p{color:#b8c4d4;line-height:1.6}.pill{display:inline-block;padding:9px 13px;border-radius:999px;background:#12334b;color:#7dd3fc}a{color:#7dd3fc}.foot{margin-top:48px;font-size:13px;color:#718096}</style></head><body><main><div class="brand">SECUREQUOTE LITE</div><h1>Quote securely. Approve confidently. Get paid.</h1><p>AI-assisted quoting for service businesses with human approval, customer acceptance, payment verification and an auditable workflow.</p><div class="card"><span class="pill">Production V1</span><h2>SecureQuote Lite is online.</h2><p>The application API is running. Business onboarding, persistent storage, payments and email require the configured production services.</p><p><a href="/securequote/health">View system health</a></p></div><div class="foot">Sponsored by CREIGNIFICENT LLC.</div></main></body></html>"""
@app.on_event("startup")
def startup():
 if os.getenv("SECUREQUOTE_AUTO_MIGRATE","false").lower()=="true": init_db()
@app.get("/securequote/health")
def health():return {"status":"available","database":bool(os.getenv("DATABASE_URL")),"stripe":bool(os.getenv("STRIPE_SECRET_KEY")),"email":bool(os.getenv("RESEND_API_KEY"))}
@app.post("/securequote/api/auth/signup")
def signup(v:Signup):
 u=create_owner(v.business_name,v.email,v.password); audit(u["tenant_id"],"TENANT_CREATED",actor=u["email"]); return {"access_token":issue_token(u),"tenant_id":u["tenant_id"]}
@app.post("/securequote/api/auth/login")
def login(v:Login):
 u=authenticate(v.email,v.password); return {"access_token":issue_token(u),"tenant_id":str(u["tenant_id"])}
@app.post("/securequote/api/quotes")
def create_quote(v:Intake,u=Depends(current_user)):
 qid=str(uuid4()); p=v.model_dump(mode="json")
 with db() as c:c.execute(text("INSERT INTO quotes(id,tenant_id,customer_name,customer_email,customer_phone,payload) VALUES(:id,:t,:n,:e,:p,CAST(:j AS JSONB))"),{"id":qid,"t":u["tid"],"n":v.customer_name,"e":v.customer_email,"p":v.customer_phone,"j":json.dumps(p)})
 audit(u["tid"],"QUOTE_CREATED",qid,u["email"]); return {"id":qid,"state":"NEW"}
@app.get("/securequote/api/quotes")
def list_quotes(u=Depends(current_user)):
 with db() as c:rows=c.execute(text("SELECT id,customer_name,customer_email,state,final_price,payment_status,created_at FROM quotes WHERE tenant_id=:t ORDER BY created_at DESC"),{"t":u["tid"]}).mappings().all()
 return [dict(r) for r in rows]
@app.post("/securequote/api/quotes/{qid}/approve")
async def approve(qid:str,v:Approval,u=Depends(current_user)):
 if v.final_price<=0: raise HTTPException(422,"Price must be positive")
 public=secrets.token_urlsafe(32)
 with db() as c:r=c.execute(text("UPDATE quotes SET state='APPROVED',final_price=:p,human_version=CAST(:h AS JSONB),public_token=:tok,updated_at=now() WHERE id=:id AND tenant_id=:t RETURNING customer_email"),{"p":v.final_price,"h":json.dumps(v.model_dump(mode="json")),"tok":public,"id":qid,"t":u["tid"]}).first()
 if not r: raise HTTPException(404,"Quote not found")
 audit(u["tid"],"QUOTE_APPROVED",qid,u["email"],{"final_price":str(v.final_price)}); await send_quote(r[0],public); audit(u["tid"],"QUOTE_EMAILED",qid,u["email"]); return {"state":"APPROVED","public_token":public}
@app.get("/securequote/q/{tok}",response_class=HTMLResponse)
def public_quote(tok:str):
 with db() as c:q=rowdict(c.execute(text("SELECT customer_name,final_price,state,payment_status FROM quotes WHERE public_token=:p"),{"p":tok}).first())
 if not q: raise HTTPException(404,"Quote not found")
 return '<!doctype html><meta name="viewport" content="width=device-width"><title>SecureQuote</title><main><h1>Your service quote</h1><p>'+html.escape(str(q["customer_name"]))+', your approved quote is <strong>$'+html.escape(str(q["final_price"]))+'</strong>.</p><p>Status: '+html.escape(str(q["state"]))+' · Payment: '+html.escape(str(q["payment_status"]))+'</p><form method="post" action="/securequote/q/'+tok+'/accept"><button>Accept & Pay</button></form><form method="post" action="/securequote/q/'+tok+'/decline"><button>Decline</button></form></main>'
@app.post("/securequote/q/{tok}/accept")
async def accept(tok:str):
 with db() as c:
  q=rowdict(c.execute(text("SELECT id,tenant_id,customer_email,final_price,state FROM quotes WHERE public_token=:p FOR UPDATE"),{"p":tok}).first())
  if not q: raise HTTPException(404,"Quote not found")
  if q["state"] not in ("APPROVED","ACCEPTED"): raise HTTPException(409,"Quote cannot be accepted")
  c.execute(text("UPDATE quotes SET state='ACCEPTED',updated_at=now() WHERE id=:id"),{"id":q["id"]})
 session=await create_payment_checkout(str(q["id"]),tok,q["customer_email"],int(Decimal(q["final_price"])*100))
 with db() as c:c.execute(text("UPDATE quotes SET stripe_session_id=:s WHERE id=:id"),{"s":session["id"],"id":q["id"]})
 audit(str(q["tenant_id"]),"QUOTE_ACCEPTED",str(q["id"]),data={"stripe_session_id":session["id"]}); return RedirectResponse(session["url"],303)
@app.post("/securequote/q/{tok}/decline")
def decline(tok:str):
 with db() as c:q=rowdict(c.execute(text("UPDATE quotes SET state='DECLINED',updated_at=now() WHERE public_token=:p AND state='APPROVED' RETURNING id,tenant_id"),{"p":tok}).first())
 if not q: raise HTTPException(409,"Quote cannot be declined")
 audit(str(q["tenant_id"]),"QUOTE_DECLINED",str(q["id"])); return {"state":"DECLINED"}
@app.post("/securequote/api/billing/checkout")
async def billing(u=Depends(current_user)):
 s=await create_subscription_checkout(u["tid"],u["email"]); return {"checkout_url":s["url"]}
@app.post("/securequote/api/stripe/webhook")
async def webhook(request:Request):
 raw=await request.body(); sig=request.headers.get("stripe-signature","")
 if not WEBHOOK_SECRET: raise HTTPException(503,"Webhook secret missing")
 parts=dict(x.split("=",1) for x in sig.split(",") if "=" in x); ts=parts.get("t"); v1=parts.get("v1")
 if not ts or not v1 or abs(time.time()-int(ts))>300: raise HTTPException(400,"Invalid Stripe signature")
 expected=hmac.new(WEBHOOK_SECRET.encode(),ts.encode()+b"."+raw,hashlib.sha256).hexdigest()
 if not hmac.compare_digest(expected,v1): raise HTTPException(400,"Invalid Stripe signature")
 event=json.loads(raw); obj=event.get("data",{}).get("object",{})
 if event.get("type")=="checkout.session.completed":
  ref=obj.get("client_reference_id"); mode=obj.get("mode")
  with db() as c:
   if mode=="payment":
    q=rowdict(c.execute(text("UPDATE quotes SET payment_status='paid',state='PAID',updated_at=now() WHERE id=:id AND stripe_session_id=:s RETURNING tenant_id"),{"id":ref,"s":obj.get("id")}).first())
    if q:audit(str(q["tenant_id"]),"PAYMENT_VERIFIED",ref,data={"session_id":obj.get("id")})
   elif mode=="subscription":c.execute(text("UPDATE tenants SET subscription_status='active',stripe_customer_id=:c WHERE id=:id"),{"id":ref,"c":obj.get("customer")}); audit(ref,"SUBSCRIPTION_ACTIVATED",data={"customer":obj.get("customer")})
 return {"received":True}

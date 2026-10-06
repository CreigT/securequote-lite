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

PAGE_CSS = """
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#07111f;color:#f4f7fb;line-height:1.55}
a{color:#7dd3fc;text-decoration:none}
a:hover{text-decoration:underline}
header,main,footer{max-width:980px;margin:auto;padding:0 20px}
header{display:flex;justify-content:space-between;align-items:center;padding-top:22px;padding-bottom:12px;gap:12px}
.brand{letter-spacing:.14em;font-size:13px;color:#7dd3fc;font-weight:700}
nav{display:flex;gap:10px;flex-wrap:wrap}
.btn,.ghost{display:inline-block;padding:12px 16px;border-radius:12px;font-weight:650;border:0;cursor:pointer;font-size:16px}
.btn{background:#38bdf8;color:#041018}
.ghost{background:transparent;color:#dbe7f3;border:1px solid #31445c}
h1{font-size:clamp(32px,6vw,52px);line-height:1.08;margin:12px 0 14px}
h2{font-size:28px;margin:0 0 10px}
h3{margin:0 0 6px}
.lede{font-size:18px;color:#c5d2e2;max-width:720px}
.flow{display:flex;flex-wrap:wrap;gap:8px;margin:22px 0 8px}
.flow span{background:#10263a;border:1px solid #244864;border-radius:999px;padding:8px 12px;color:#d7ecff;font-size:14px}
section{margin:28px 0;padding:22px;border:1px solid #243244;border-radius:18px;background:#0d1b2a}
.grid{display:grid;grid-template-columns:1fr;gap:14px}
@media(min-width:760px){.grid.two{grid-template-columns:1.2fr .8fr}}
ol{padding-left:20px}
li{margin:10px 0;color:#d5e0ec}
.muted{color:#9fb0c3}
.price{font-size:42px;margin:6px 0}
.example{background:#102033;border-radius:14px;padding:14px 16px}
label{display:block;font-size:14px;margin:12px 0 6px;color:#c5d2e2}
input,textarea{width:100%;padding:12px;border-radius:10px;border:1px solid #31445c;background:#07111f;color:#fff;font-size:16px}
form .btn{margin-top:16px}
.note{font-size:14px;color:#9fb0c3}
footer{padding:28px 20px 48px;color:#718096;font-size:13px}
.err{color:#fecaca}
.ok{color:#bbf7d0}
"""

def shell(title, body):
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title><style>{PAGE_CSS}</style></head><body>{body}<footer>Sponsored by CREIGNIFICENT LLC. AI recommends. You approve the price. Stripe confirms the payment.</footer></body></html>"""

def home():
    body = """
<header><div class="brand">SECUREQUOTE LITE</div><nav><a class="ghost" href="/login">Log in</a><a class="btn" href="/signup">Start</a></nav></header>
<main>
<p class="brand">FOR SERVICE BUSINESSES</p>
<h1>Turn a customer inquiry into an approved, paid job.</h1>
<p class="lede">SecureQuote Lite is for cleaning companies, landscapers, mobile detailers, pressure-washing crews, handymen, and other owners who keep getting asked, “How much would you charge me for this job?”</p>
<p class="lede">Instead of scattered texts, phone calls, handwritten notes, invoices, and payment links, the job stays in one controlled workflow.</p>
<div class="flow"><span>Customer request</span><span>AI prepares a quote</span><span>You review it</span><span>Customer receives it</span><span>Customer accepts</span><span>Customer pays</span><span>Permanent record</span></div>
<p><a class="btn" href="/signup">Create my business account</a> <a class="ghost" href="/login">I already have an account</a></p>

<section>
<h2>Who it is for</h2>
<p>Small service-business owners who regularly prepare estimates. Each business gets its own account. ABC Cleaning should not be able to see XYZ Landscaping’s customers, quotes, payments, or records.</p>
</section>

<section>
<h2>Example: ABC Cleaning Company</h2>
<div class="example">
<p>A customer wants her 4,000-square-foot daycare cleaned five nights a week.</p>
<p>You enter the customer, the job, the location, the scope, the timing, and your notes. SecureQuote helps prepare a structured quote. AI does not get the final say.</p>
<p>The recommendation might be <strong>$1,850/month</strong>. You know something about the property the AI does not, so you change it to <strong>$1,950</strong> and approve $1,950.</p>
<p>The customer gets an email with a private link. They can choose <strong>Accept & Pay</strong> or <strong>Decline</strong>. Payment goes through Stripe Checkout. The job is not marked paid because someone clicked a button. Stripe sends a verified server-to-server confirmation. Then the job is <strong>PAID</strong>.</p>
</div>
</section>

<section>
<h2>How you use it</h2>
<ol>
<li><strong>Create the business account.</strong> Sign up as ABC Cleaning Company and log into the private account.</li>
<li><strong>Enter the job.</strong> Customer name, email, phone, job type, location, scope, timing, and notes.</li>
<li><strong>Review the quote.</strong> AI assists. You set the final price and approve it.</li>
<li><strong>Send it.</strong> The customer opens a simple page with Accept & Pay or Decline.</li>
<li><strong>Get paid.</strong> Stripe Checkout, then a verified webhook. The record shows quote created, owner approved, quote emailed, customer accepted, Stripe payment verified.</li>
</ol>
</section>

<section class="grid two">
<div>
<h2>Why $49 a month</h2>
<p>This is not a sale of “AI.” It is a faster, controlled way to turn an interested customer into an approved, paid job.</p>
<p>If estimates usually sit in texts, emails, and notebooks, one extra closed job can pay for months of the software.</p>
<p><a class="btn" href="/signup">Start — $49/month</a></p>
<p class="note">Subscribe opens after the account exists. Billing uses Stripe Checkout. This page does not charge a card.</p>
</div>
<div>
<h2>Included</h2>
<ul>
<li>Private business account</li>
<li>Quote intake</li>
<li>AI recommendation, owner approval</li>
<li>Customer accept or decline page</li>
<li>Stripe payment verification</li>
<li>Audit history for that business only</li>
</ul>
</div>
</section>

<section>
<h2>What to click</h2>
<p><a href="/signup">Sign up</a> creates the business account. <a href="/login">Log in</a> opens the dashboard. From there you enter a customer, review the price, approve it, and send it. The customer uses the private link. You do not need a walkthrough.</p>
<p class="note">On this deployment, signup, saved quotes, email, and Stripe work only after the database, email, and Stripe keys are configured. <a href="/securequote/health">System health</a> shows what is connected.</p>
</section>
</main>
"""
    return shell("SecureQuote Lite", body)

def account_page(mode):
    title = "Create your business account" if mode == "signup" else "Log in"
    action = "Create account" if mode == "signup" else "Log in"
    extra = '<label>Business name<input id="business" required placeholder="ABC Cleaning Company"></label>' if mode == "signup" else ""
    endpoint = "/securequote/api/auth/signup" if mode == "signup" else "/securequote/api/auth/login"
    body = f"""
<header><div class="brand">SECUREQUOTE LITE</div><nav><a class="ghost" href="/">Home</a><a class="ghost" href="{"/login" if mode=="signup" else "/signup"}">{"Log in" if mode=="signup" else "Start"}</a></nav></header>
<main>
<h1>{title}</h1>
<p class="lede">{"Sign up as the business, for example ABC Cleaning Company. This is the private owner account." if mode=="signup" else "Use the email and password for your business account."}</p>
<section>
<form id="form">{extra}
<label>Email<input id="email" type="email" required placeholder="owner@abccleaning.example"></label>
<label>Password<input id="password" type="password" required minlength="8" placeholder="At least 8 characters"></label>
<button class="btn" type="submit">{action}</button>
<p id="msg" class="note"></p>
</form>
</section>
</main>
<script>
const form=document.getElementById("form");
form.addEventListener("submit",async(e)=>{{
 e.preventDefault();
 const msg=document.getElementById("msg");
 msg.className="note"; msg.textContent="Working...";
 const payload={{email:email.value,password:password.value}};
 if(document.getElementById("business")) payload.business_name=business.value;
 try{{
  const res=await fetch("{endpoint}",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify(payload)}});
  const data=await res.json();
  if(!res.ok) throw new Error(data.detail||"Could not continue. Database may not be connected on this deployment.");
  localStorage.setItem("securequote_token",data.access_token);
  location.href="/app";
 }}catch(err){{msg.className="err"; msg.textContent=err.message;}}
}});
</script>
"""
    return shell(title + " — SecureQuote Lite", body)

def app_page():
    body = """
<header><div class="brand">SECUREQUOTE LITE</div><nav><a class="ghost" href="/">Home</a><button class="ghost" id="out" type="button">Log out</button></nav></header>
<main>
<h1>Dashboard</h1>
<p class="lede" id="who">Your private business account.</p>
<section>
<h2>New quote</h2>
<form id="quote">
<label>Customer name<input name="customer_name" required></label>
<label>Customer email<input name="customer_email" type="email" required></label>
<label>Phone<input name="customer_phone" required></label>
<label>Job type<input name="job_type" required placeholder="Daycare cleaning"></label>
<label>Location<input name="service_location" required></label>
<label>Scope<textarea name="scope_summary" required placeholder="4,000 sq ft daycare, five nights per week"></textarea></label>
<label>Timing<input name="preferred_timing" placeholder="Weeknights"></label>
<label>Notes<textarea name="notes" placeholder="What you know that the AI does not"></textarea></label>
<button class="btn" type="submit">Save quote</button>
<p id="qmsg" class="note"></p>
</form>
</section>
<section>
<h2>Approve a price</h2>
<p class="muted">AI can recommend a number. You type the price you will stand behind, then approve it. Example: change $1,850 to $1,950.</p>
<form id="approve">
<label>Quote id<input name="qid" required></label>
<label>Final price<input name="final_price" type="number" min="0.01" step="0.01" required placeholder="1950"></label>
<label>Summary<input name="summary" value="Approved service quote"></label>
<button class="btn" type="submit">Approve and send</button>
<p id="amsg" class="note"></p>
</form>
</section>
<section>
<h2>Quotes</h2>
<div id="quotes" class="muted">Loading...</div>
</section>
<section>
<h2>Audit history</h2>
<div id="audit" class="muted">Loading...</div>
</section>
</main>
<script>
const token=localStorage.getItem("securequote_token");
if(!token) location.href="/login";
const headers={"Content-Type":"application/json","Authorization":"Bearer "+token};
async function api(path,opts){
 const res=await fetch(path,Object.assign({headers},opts||{}));
 const data=await res.json().catch(()=>({}));
 if(res.status===401){localStorage.removeItem("securequote_token"); location.href="/login";}
 if(!res.ok) throw new Error(data.detail||"Request failed");
 return data;
}
document.getElementById("out").onclick=()=>{localStorage.removeItem("securequote_token"); location.href="/";};
async function load(){
 try{
  const account=await api("/securequote/api/account");
  document.getElementById("who").textContent=account.name+" · subscription "+account.subscription_status;
  const quotes=await api("/securequote/api/quotes");
  document.getElementById("quotes").innerHTML=quotes.length?quotes.map(q=>"<p><strong>"+q.customer_name+"</strong> · "+q.state+" · "+(q.final_price||"no price yet")+" · "+q.id+"</p>").join(""):"No quotes yet. Enter a customer above.";
  const audit=await api("/securequote/api/audit");
  document.getElementById("audit").innerHTML=audit.length?audit.map(e=>"<p>"+e.event+" · "+(e.quote_id||"")+" · "+e.created_at+"</p>").join(""):"No audit events yet.";
 }catch(err){
  document.getElementById("quotes").textContent=err.message;
  document.getElementById("audit").textContent="History appears after the database is connected.";
 }
}
document.getElementById("quote").onsubmit=async(e)=>{
 e.preventDefault();
 const msg=document.getElementById("qmsg");
 const body=Object.fromEntries(new FormData(e.target).entries());
 try{const data=await api("/securequote/api/quotes",{method:"POST",body:JSON.stringify(body)}); msg.className="ok"; msg.textContent="Quote saved. Id: "+data.id; e.target.reset(); load();}
 catch(err){msg.className="err"; msg.textContent=err.message;}
};
document.getElementById("approve").onsubmit=async(e)=>{
 e.preventDefault();
 const msg=document.getElementById("amsg");
 const body=Object.fromEntries(new FormData(e.target).entries());
 try{const data=await api("/securequote/api/quotes/"+encodeURIComponent(body.qid)+"/approve",{method:"POST",body:JSON.stringify({final_price:body.final_price,summary:body.summary})}); msg.className="ok"; msg.textContent="Approved. Customer link token: "+data.public_token; load();}
 catch(err){msg.className="err"; msg.textContent=err.message;}
};
load();
</script>
"""
    return shell("Dashboard — SecureQuote Lite", body)

@app.get("/",response_class=HTMLResponse)
def home_route():
    return home()

@app.get("/signup",response_class=HTMLResponse)
def signup_page():
    return account_page("signup")

@app.get("/login",response_class=HTMLResponse)
def login_page():
    return account_page("login")

@app.get("/app",response_class=HTMLResponse)
def dashboard_page():
    return app_page()

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
@app.get("/securequote/api/audit")
def list_audit(u=Depends(current_user)):
 with db() as c:rows=c.execute(text("SELECT quote_id,event,actor,data,created_at FROM audit_events WHERE tenant_id=:t ORDER BY created_at DESC LIMIT 200"),{"t":u["tid"]}).mappings().all()
 return [dict(r) for r in rows]
@app.get("/securequote/api/account")
def account(u=Depends(current_user)):
 with db() as c:r=rowdict(c.execute(text("SELECT id,name,subscription_status FROM tenants WHERE id=:t"),{"t":u["tid"]}).first())
 if not r: raise HTTPException(404,"Tenant not found")
 return r
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
 event=json.loads(raw); obj=event.get("data",{}).get("object",{}); event_id=event.get("id")
 if not event_id: raise HTTPException(400,"Stripe event id missing")
 with db() as c:
  exists=c.execute(text("SELECT 1 FROM stripe_events WHERE id=:id"),{"id":event_id}).first()
  if exists:return {"received":True,"duplicate":True}
  c.execute(text("INSERT INTO stripe_events(id,event_type) VALUES(:id,:t)"),{"id":event_id,"t":event.get("type","unknown")})
 if event.get("type")=="checkout.session.completed":
  ref=obj.get("client_reference_id"); mode=obj.get("mode")
  with db() as c:
   if mode=="payment":
    q=rowdict(c.execute(text("UPDATE quotes SET payment_status='paid',state='PAID',updated_at=now() WHERE id=:id AND stripe_session_id=:s RETURNING tenant_id"),{"id":ref,"s":obj.get("id")}).first())
    if q:audit(str(q["tenant_id"]),"PAYMENT_VERIFIED",ref,data={"session_id":obj.get("id")})
   elif mode=="subscription":c.execute(text("UPDATE tenants SET subscription_status='active',stripe_customer_id=:c WHERE id=:id"),{"id":ref,"c":obj.get("customer")}); audit(ref,"SUBSCRIPTION_ACTIVATED",data={"customer":obj.get("customer")})
 return {"received":True}

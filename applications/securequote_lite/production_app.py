import os,json,secrets,hmac,hashlib,time,html
from decimal import Decimal
from uuid import uuid4
from fastapi import FastAPI,Depends,HTTPException,Request
from fastapi.responses import HTMLResponse,RedirectResponse
from pydantic import BaseModel,EmailStr,Field
from sqlalchemy import text
from .auth import create_owner,authenticate,token as issue_token,current_user,session_secret_configured,PASSWORD_MIN_LENGTH
from .production_db import db,init_db,rowdict,audit
from .integrations import create_payment_checkout,create_subscription_checkout,send_quote,send_owner_notice,webhook_secret,stripe_configured,email_configured
from .ai import suggest_price
from src.ai.gateway import AIGatewayError,AIProviderNotConfigured,create_ai_provider
app=FastAPI(title="SecureQuote Lite Production V1")
class Signup(BaseModel): business_name:str=Field(min_length=1,max_length=120); email:EmailStr; password:str=Field(max_length=256)
class Login(BaseModel): email:EmailStr; password:str=Field(max_length=256)
class Intake(BaseModel):
 customer_name:str=Field(min_length=1,max_length=100); customer_email:EmailStr; customer_phone:str=Field(min_length=1,max_length=30)
 job_type:str=Field(min_length=1,max_length=120); service_location:str=Field(min_length=1,max_length=240); scope_summary:str=Field(min_length=1,max_length=2000)
 preferred_timing:str=Field(default="",max_length=120); notes:str=Field(default="",max_length=3000)
class Approval(BaseModel): final_price:Decimal=Field(max_digits=12,decimal_places=2); summary:str=Field(default="Approved service quote",max_length=500)

# --- v1 settings (read at request time so deployments can change them without code edits) ---
APPROVABLE_STATES=("NEW","EMAIL_FAILED")
ACTIVE_SUBSCRIPTION="active"
def _flag(name,default="false"): return os.getenv(name,default).strip().lower() in ("1","true","yes","on")
def customer_payments_enabled(): return _flag("SECUREQUOTE_CUSTOMER_PAYMENTS_ENABLED")
def _int_env(name,default,minimum=0):
 try: return max(minimum,int(os.getenv(name,str(default)).strip()))
 except ValueError: return default
def trial_quote_limit(): return _int_env("SECUREQUOTE_TRIAL_QUOTES",3)
def quote_link_days(): return _int_env("SECUREQUOTE_QUOTE_LINK_DAYS",30,1)
def get_ai_provider():
 """Return a configured AI provider or None. Never raises, so the UI can hide the AI button."""
 try:
  provider=create_ai_provider(os.getenv("SECUREQUOTE_LITE_AI_PROVIDER",""))
 except AIProviderNotConfigured: return None
 return provider if getattr(provider,"configured",False) else None
def ai_enabled(): return get_ai_provider() is not None

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
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title><meta name="description" content="SecureQuote Lite: quote intake, owner approval, and customer acceptance for small service businesses. Sponsored by CREIGNIFICENT LLC."><meta name="author" content="Sponsored by CREIGNIFICENT LLC"><style>{PAGE_CSS}</style></head><body>{body}<footer>Sponsored by CREIGNIFICENT LLC. AI can suggest. You approve the price. · <a href="/terms">Terms</a> · <a href="/privacy">Privacy</a> · <a href="/refunds">Refund &amp; cancellation</a></footer></body></html>"""

def home():
    body = """
<header><div class="brand">SECUREQUOTE LITE</div><nav><a class="ghost" href="/login">Log in</a><a class="btn" href="/signup">Start</a></nav></header>
<main>
<p class="brand">FOR SERVICE BUSINESSES</p>
<h1>Turn a customer inquiry into an approved, accepted job.</h1>
<p class="lede">SecureQuote Lite is for cleaning companies, landscapers, mobile detailers, pressure-washing crews, handymen, and other owners who keep getting asked, “How much would you charge me for this job?”</p>
<p class="lede">Instead of scattered texts, phone calls, handwritten notes, invoices, and payment links, the job stays in one controlled workflow.</p>
<div class="flow"><span>Customer request</span><span>Optional AI price suggestion</span><span>You review it</span><span>Customer receives it</span><span>Customer accepts</span><span>You get notified</span><span>Permanent record</span></div>
<p><a class="btn" href="/signup">Create my business account</a> <a class="ghost" href="/login">I already have an account</a></p>

<section>
<h2>Who it is for</h2>
<p>Small service-business owners who regularly prepare estimates. Each business gets its own account. ABC Cleaning should not be able to see XYZ Landscaping’s customers, quotes, payments, or records.</p>
</section>

<section>
<h2>Example: ABC Cleaning Company</h2>
<div class="example">
<p>A customer wants her 4,000-square-foot daycare cleaned five nights a week.</p>
<p>You enter the customer, the job, the location, the scope, the timing, and your notes. If AI suggestions are turned on, SecureQuote can suggest a price range. AI does not get the final say.</p>
<p>The recommendation might be <strong>$1,850/month</strong>. You know something about the property the AI does not, so you change it to <strong>$1,950</strong> and approve $1,950.</p>
<p>The customer gets an email with a private link. They can choose <strong>Accept</strong> or <strong>Decline</strong>. When they accept, you get an email and the quote is marked <strong>ACCEPTED</strong> in your records. You collect payment the way you already do.</p>
</div>
</section>

<section>
<h2>How you use it</h2>
<ol>
<li><strong>Create the business account.</strong> Sign up as ABC Cleaning Company and log into the private account.</li>
<li><strong>Enter the job.</strong> Customer name, email, phone, job type, location, scope, timing, and notes.</li>
<li><strong>Review the quote.</strong> Optionally ask AI to suggest a price. You set the final price and approve it.</li>
<li><strong>Send it.</strong> The customer opens a simple page with Accept or Decline.</li>
<li><strong>Close the job.</strong> You get an email when the customer accepts. The record shows quote created, owner approved, quote emailed, customer accepted.</li>
</ol>
</section>

<section class="grid two">
<div>
<h2>Why $49 a month</h2>
<p>This is not a sale of “AI.” It is a faster, controlled way to turn an interested customer into an approved, accepted job.</p>
<p>If estimates usually sit in texts, emails, and notebooks, one extra closed job can pay for months of the software.</p>
<p><a class="btn" href="/signup">Start — $49/month</a></p>
<p class="note">Your first {trial} quotes are free. Subscribe from your dashboard after the account exists. Billing uses Stripe Checkout. This page does not charge a card. Cancel anytime.</p>
</div>
<div>
<h2>Included</h2>
<ul>
<li>Private business account</li>
<li>Quote intake</li>
<li>Optional AI price suggestion, owner approval</li>
<li>Customer accept or decline page</li>
<li>Email when a customer accepts</li>
<li>Audit history for that business only</li>
</ul>
</div>
</section>

<section>
<h2>What to click</h2>
<p><a href="/signup">Sign up</a> creates the business account. <a href="/login">Log in</a> opens the dashboard. From there you enter a customer, review the price, approve it, and send it. The customer uses the private link. You do not need a walkthrough.</p>
<p class="note">On this deployment, signup, saved quotes, email, and billing work only after the database, email, and Stripe keys are configured. <a href="/securequote/health">System health</a> shows what is connected.</p>
</section>
</main>
"""
    return shell("SecureQuote Lite", body.replace("{trial}",str(trial_quote_limit())))

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
<label>Password<input id="password" type="password" required minlength="{PASSWORD_MIN_LENGTH}" placeholder="At least {PASSWORD_MIN_LENGTH} characters"></label>
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
<section id="billing" hidden>
<h2>Subscription</h2>
<p id="billmsg" class="muted"></p>
<button class="btn" id="subscribe" type="button">Subscribe — $49/month</button>
<p id="submsg" class="note"></p>
</section>
<section>
<h2>New quote</h2>
<form id="quote">
<label>Customer name<input name="customer_name" required maxlength="100"></label>
<label>Customer email<input name="customer_email" type="email" required></label>
<label>Phone<input name="customer_phone" required maxlength="30"></label>
<label>Job type<input name="job_type" required maxlength="120" placeholder="Daycare cleaning"></label>
<label>Location<input name="service_location" required maxlength="240"></label>
<label>Scope<textarea name="scope_summary" required maxlength="2000" placeholder="4,000 sq ft daycare, five nights per week"></textarea></label>
<label>Timing<input name="preferred_timing" maxlength="120" placeholder="Weeknights"></label>
<label>Notes<textarea name="notes" maxlength="3000" placeholder="What you know about the job"></textarea></label>
<button class="btn" type="submit">Save quote</button>
<p id="qmsg" class="note"></p>
</form>
</section>
<section>
<h2>Approve a price</h2>
<p class="muted">Pick a quote below, or paste its id. You type the price you will stand behind, then approve it. The customer gets an email with a private link.</p>
<form id="approve">
<label>Quote id<input name="qid" id="qid" required></label>
<p id="suggestion" class="note"></p>
<button class="ghost" id="suggest" type="button" hidden>Suggest a price (AI)</button>
<label>Final price<input name="final_price" id="final_price" type="number" min="0.01" step="0.01" required placeholder="1950"></label>
<label>Summary<input name="summary" maxlength="500" value="Approved service quote"></label>
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
function esc(v){return String(v==null?"":v).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
let aiEnabled=false;
async function api(path,opts){
 const res=await fetch(path,Object.assign({headers},opts||{}));
 const data=await res.json().catch(()=>({}));
 if(res.status===401){localStorage.removeItem("securequote_token"); location.href="/login";}
 if(!res.ok) throw new Error(data.detail||"Request failed");
 return data;
}
document.getElementById("out").onclick=()=>{localStorage.removeItem("securequote_token"); location.href="/";};
function pick(id,price){
 document.getElementById("qid").value=id;
 if(price) document.getElementById("final_price").value=price;
 document.getElementById("suggestion").textContent="";
 document.getElementById("approve").scrollIntoView({behavior:"smooth"});
}
async function load(){
 try{
  const account=await api("/securequote/api/account");
  aiEnabled=!!account.ai_enabled;
  document.getElementById("suggest").hidden=!aiEnabled;
  document.getElementById("who").textContent=account.name+" · subscription "+account.subscription_status;
  const billing=document.getElementById("billing");
  if(account.subscription_status!=="active"){
   billing.hidden=false;
   document.getElementById("billmsg").textContent="Free trial: "+account.trial_quotes_used+" of "+account.trial_quote_limit+" quotes used. Subscribe to keep creating and sending quotes.";
  } else billing.hidden=true;
  const quotes=await api("/securequote/api/quotes");
  const box=document.getElementById("quotes");
  box.innerHTML=quotes.length?quotes.map(q=>{
   const can=(q.state==="NEW"||q.state==="EMAIL_FAILED");
   return "<p><strong>"+esc(q.customer_name)+"</strong> · "+esc(q.state)+" · "+esc(q.final_price||"no price yet")+" · "+esc(q.id)+
    (can?' <button class="ghost" type="button" data-pick="'+esc(q.id)+'">'+(q.state==="EMAIL_FAILED"?"Retry send":"Review")+'</button>':"")+"</p>";
  }).join(""):"No quotes yet. Enter a customer above.";
  box.querySelectorAll("[data-pick]").forEach(b=>b.onclick=()=>pick(b.dataset.pick));
  const audit=await api("/securequote/api/audit");
  document.getElementById("audit").innerHTML=audit.length?audit.map(e=>"<p>"+esc(e.event)+" · "+esc(e.quote_id||"")+" · "+esc(e.created_at)+"</p>").join(""):"No audit events yet.";
 }catch(err){
  document.getElementById("quotes").textContent=err.message;
  document.getElementById("audit").textContent="History appears after the database is connected.";
 }
}
document.getElementById("subscribe").onclick=async()=>{
 const msg=document.getElementById("submsg");
 msg.className="note"; msg.textContent="Opening secure checkout...";
 try{const data=await api("/securequote/api/billing/checkout",{method:"POST"}); location.href=data.checkout_url;}
 catch(err){msg.className="err"; msg.textContent=err.message;}
};
document.getElementById("suggest").onclick=async()=>{
 const qid=document.getElementById("qid").value.trim();
 const out=document.getElementById("suggestion");
 if(!qid){out.className="err"; out.textContent="Pick a quote first."; return;}
 out.className="note"; out.textContent="Asking AI for a suggestion...";
 try{
  const r=await api("/securequote/api/quotes/"+encodeURIComponent(qid)+"/suggest",{method:"POST"});
  const mid=((Number(r.price_min)+Number(r.price_max))/2).toFixed(2);
  document.getElementById("final_price").value=mid;
  out.className="ok";
  out.textContent="AI suggests $"+r.price_min+" to $"+r.price_max+" (confidence "+r.confidence+"%). "+r.summary+" The price box is pre-filled with the middle of that range. Change it before you approve.";
 }catch(err){out.className="err"; out.textContent=err.message;}
};
document.getElementById("quote").onsubmit=async(e)=>{
 e.preventDefault();
 const msg=document.getElementById("qmsg");
 const body=Object.fromEntries(new FormData(e.target).entries());
 try{const data=await api("/securequote/api/quotes",{method:"POST",body:JSON.stringify(body)}); msg.className="ok"; msg.textContent="Quote saved. Id: "+data.id; e.target.reset(); pick(data.id); load();}
 catch(err){msg.className="err"; msg.textContent=err.message;}
};
document.getElementById("approve").onsubmit=async(e)=>{
 e.preventDefault();
 const msg=document.getElementById("amsg");
 const body=Object.fromEntries(new FormData(e.target).entries());
 try{const data=await api("/securequote/api/quotes/"+encodeURIComponent(body.qid)+"/approve",{method:"POST",body:JSON.stringify({final_price:body.final_price,summary:body.summary})}); msg.className="ok"; msg.textContent="Approved and emailed to the customer. Link expires "+data.link_expires_at+"."; load();}
 catch(err){msg.className="err"; msg.textContent=err.message; load();}
};
load();
</script>
"""
    return shell("Dashboard — SecureQuote Lite", body)

def simple_page(title, heading, paragraphs_html):
    body = f"""
<header><div class="brand">SECUREQUOTE LITE</div><nav><a class="ghost" href="/">Home</a><a class="ghost" href="/app">Dashboard</a></nav></header>
<main><h1>{html.escape(heading)}</h1><section>{paragraphs_html}</section></main>
"""
    return shell(title + " — SecureQuote Lite", body)

DRAFT_BANNER = '<p class="err"><strong>Draft — review before launch.</strong> This page is a working draft and has not been reviewed by a lawyer.</p>'
OPERATOR = '<p>SecureQuote Lite is operated by Creignificent LLC. Contact: <a href="mailto:Creignificent@gmail.com">Creignificent@gmail.com</a>.</p>'

TERMS_HTML = DRAFT_BANNER + OPERATOR + """
<p class="muted">Last updated: [date to be set at launch]</p>
<h3>1. The service</h3><p>SecureQuote Lite is web software that helps service businesses record customer inquiries, prepare and approve quotes, and send customers a private link to accept or decline. Optional AI suggestions are estimates only. You, the business owner, decide every final price.</p>
<h3>2. Accounts</h3><p>You must give accurate account information and keep your password secure. You are responsible for activity under your account and for the customer information you enter. You must have the right to contact the customers you enter.</p>
<h3>3. Subscription and billing</h3><p>Paid plans are billed monthly in advance through Stripe at the price shown when you subscribe (currently $49/month). A limited number of free trial quotes may be offered. See the Refund &amp; Cancellation Policy for cancellations.</p>
<h3>4. Acceptable use</h3><p>Do not use the service for unlawful purposes, to send spam, to upload malicious content, or to try to access another business's data.</p>
<h3>5. Your data</h3><p>You keep ownership of the data you enter. We use it only to provide the service, as described in the Privacy Policy.</p>
<h3>6. Availability and changes</h3><p>We aim to keep the service available but do not guarantee it will be uninterrupted or error-free. We may change or discontinue features with reasonable notice.</p>
<h3>7. Disclaimer and limitation of liability</h3><p>The service is provided "as is" to the extent permitted by law. To the extent permitted by law, our total liability is limited to the amount you paid for the service in the 12 months before the claim.</p>
<h3>8. Termination</h3><p>You may stop using the service at any time. We may suspend accounts that violate these terms.</p>
<h3>9. Governing law</h3><p>[To be confirmed before launch.]</p>
<h3>10. Contact</h3><p>Questions about these terms: Creignificent@gmail.com.</p>
"""

PRIVACY_HTML = DRAFT_BANNER + OPERATOR + """
<p class="muted">Last updated: [date to be set at launch]</p>
<h3>What we collect</h3><p><strong>Business owners:</strong> business name, email address, and a securely hashed password. <strong>Your customers:</strong> the name, email, phone, job details, and notes that a business enters to prepare a quote, plus whether the customer accepted or declined. <strong>Billing:</strong> handled by Stripe; we store Stripe customer and subscription identifiers and status, not card numbers.</p>
<h3>How we use it</h3><p>To run the service: store quotes, email quote links to customers, notify owners when a customer responds, manage subscriptions, and keep an audit history for each business.</p>
<h3>Service providers</h3><p>We share data only with providers needed to run the service: hosting, our database provider, Stripe (billing), Resend (email delivery), and, only if a business turns on AI suggestions, an AI provider. AI requests include job details only; customer name, email, and phone are not sent.</p>
<h3>What we do not do</h3><p>We do not sell personal information.</p>
<h3>Retention and deletion</h3><p>We keep data while an account is active. A business owner can ask us to delete their account and its data by emailing Creignificent@gmail.com.</p>
<h3>Security</h3><p>Each business's data is separated from other businesses. Passwords are hashed. No system is perfectly secure.</p>
<h3>Contact</h3><p>Privacy questions or requests: Creignificent@gmail.com.</p>
"""

REFUNDS_HTML = DRAFT_BANNER + OPERATOR + """
<p class="muted">Last updated: [date to be set at launch]</p>
<h3>Cancelling</h3><p>You can cancel your subscription at any time by emailing Creignificent@gmail.com. Cancellation stops future charges. Your account stays active until the end of the billing period you already paid for.</p>
<h3>Refunds</h3><p>Monthly fees are generally not refunded for partial months. If you were charged in error or could not use the service because of a problem on our side, email us within 30 days of the charge and we will review it. [Refund terms to be confirmed before launch.]</p>
<h3>Failed payments</h3><p>If a payment fails, Stripe may retry it. While payment is past due, creating and sending new quotes may be paused until billing is fixed.</p>
<h3>Customer payments for jobs</h3><p>In this version, payments between a business and its own customers happen outside SecureQuote Lite. Refunds for those jobs are between the business and its customer.</p>
"""

def legal_page(title, content):
    return simple_page(title, title, content)

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

@app.get("/terms",response_class=HTMLResponse)
def terms_page(): return legal_page("Terms of Service",TERMS_HTML)
@app.get("/privacy",response_class=HTMLResponse)
def privacy_page(): return legal_page("Privacy Policy",PRIVACY_HTML)
@app.get("/refunds",response_class=HTMLResponse)
def refunds_page(): return legal_page("Refund & Cancellation Policy",REFUNDS_HTML)

@app.get("/securequote/billing/success",response_class=HTMLResponse)
def billing_success():
    return simple_page("Subscription started","Thanks — your subscription is starting",'<p>Stripe received your payment details. Your account switches to <strong>active</strong> as soon as Stripe confirms the payment to us, usually within a minute.</p><p><a class="btn" href="/app">Back to my dashboard</a></p>')
@app.get("/securequote/billing/cancel",response_class=HTMLResponse)
def billing_cancel():
    return simple_page("Checkout cancelled","Checkout cancelled",'<p>No charge was made. You can subscribe any time from your dashboard.</p><p><a class="btn" href="/app">Back to my dashboard</a></p>')

@app.on_event("startup")
def startup():
 if os.getenv("SECUREQUOTE_AUTO_MIGRATE","false").lower()=="true": init_db()
@app.get("/securequote/health")
def health():return {"status":"available","database":bool(os.getenv("DATABASE_URL")),"session_secret":session_secret_configured(),"stripe":stripe_configured(),"stripe_webhook":bool(webhook_secret()),"email":email_configured(),"ai":ai_enabled(),"customer_payments":customer_payments_enabled()}
@app.post("/securequote/api/auth/signup")
def signup(v:Signup):
 u=create_owner(v.business_name,v.email,v.password); audit(u["tenant_id"],"TENANT_CREATED",actor=u["email"]); return {"access_token":issue_token(u),"tenant_id":u["tenant_id"]}
@app.post("/securequote/api/auth/login")
def login(v:Login):
 u=authenticate(v.email,v.password); return {"access_token":issue_token(u),"tenant_id":str(u["tenant_id"])}

def _usage(c,tid):
 t=rowdict(c.execute(text("SELECT subscription_status FROM tenants WHERE id=:t"),{"t":tid}).first())
 if not t: raise HTTPException(404,"Tenant not found")
 created=c.execute(text("SELECT count(*) FROM quotes WHERE tenant_id=:t"),{"t":tid}).scalar_one()
 sent=c.execute(text("SELECT count(*) FROM quotes WHERE tenant_id=:t AND sent_at IS NOT NULL"),{"t":tid}).scalar_one()
 return t["subscription_status"],int(created),int(sent)
SUBSCRIBE_MSG="Your free trial is used up. Subscribe ($49/month) from the dashboard to keep creating and sending quotes."
def require_can_create(c,tid):
 status,created,_=_usage(c,tid)
 if status!=ACTIVE_SUBSCRIPTION and created>=trial_quote_limit(): raise HTTPException(402,SUBSCRIBE_MSG)
def require_can_send(c,tid,qid):
 status,_,sent=_usage(c,tid)
 if status==ACTIVE_SUBSCRIPTION: return
 already=c.execute(text("SELECT sent_at IS NOT NULL FROM quotes WHERE id=:id AND tenant_id=:t"),{"id":qid,"t":tid}).scalar()
 if not already and sent>=trial_quote_limit(): raise HTTPException(402,SUBSCRIBE_MSG)

@app.post("/securequote/api/quotes")
def create_quote(v:Intake,u=Depends(current_user)):
 qid=str(uuid4()); p=v.model_dump(mode="json")
 with db() as c:
  c.execute(text("SELECT id FROM tenants WHERE id=:t FOR UPDATE"),{"t":u["tid"]})
  require_can_create(c,u["tid"])
  c.execute(text("INSERT INTO quotes(id,tenant_id,customer_name,customer_email,customer_phone,payload) VALUES(:id,:t,:n,:e,:p,CAST(:j AS JSONB))"),{"id":qid,"t":u["tid"],"n":v.customer_name,"e":v.customer_email,"p":v.customer_phone,"j":json.dumps(p)})
  audit(u["tid"],"QUOTE_CREATED",qid,u["email"],conn=c)
 return {"id":qid,"state":"NEW"}
@app.get("/securequote/api/audit")
def list_audit(u=Depends(current_user)):
 with db() as c:rows=c.execute(text("SELECT quote_id,event,actor,data,created_at FROM audit_events WHERE tenant_id=:t ORDER BY created_at DESC LIMIT 200"),{"t":u["tid"]}).mappings().all()
 return [dict(r) for r in rows]
@app.get("/securequote/api/account")
def account(u=Depends(current_user)):
 with db() as c:
  r=rowdict(c.execute(text("SELECT id,name,subscription_status FROM tenants WHERE id=:t"),{"t":u["tid"]}).first())
  if not r: raise HTTPException(404,"Tenant not found")
  _,created,sent=_usage(c,u["tid"])
 r.update({"trial_quote_limit":trial_quote_limit(),"trial_quotes_used":created,"quotes_sent":sent,"ai_enabled":ai_enabled(),"customer_payments_enabled":customer_payments_enabled()})
 return r
@app.get("/securequote/api/quotes")
def list_quotes(u=Depends(current_user)):
 with db() as c:rows=c.execute(text("SELECT id,customer_name,customer_email,state,final_price,payment_status,public_token_expires_at,created_at FROM quotes WHERE tenant_id=:t ORDER BY created_at DESC"),{"t":u["tid"]}).mappings().all()
 return [dict(r) for r in rows]

@app.post("/securequote/api/quotes/{qid}/suggest")
def suggest(qid:str,u=Depends(current_user)):
 provider=get_ai_provider()
 if provider is None: raise HTTPException(503,"AI suggestions are not configured on this deployment")
 with db() as c:
  q=rowdict(c.execute(text("SELECT payload,state FROM quotes WHERE id=:id AND tenant_id=:t"),{"id":qid,"t":u["tid"]}).first())
  if not q: raise HTTPException(404,"Quote not found")
  if q["state"] not in APPROVABLE_STATES: raise HTTPException(409,"Only quotes that have not been sent can get a suggestion")
  require_can_send(c,u["tid"],qid)
 try: result=suggest_price(q["payload"],provider)
 except AIGatewayError as exc:
  audit(u["tid"],"AI_SUGGESTION_FAILED",qid,u["email"],{"error_category":str(exc.category)})
  raise HTTPException(502,"AI suggestion failed. Enter your own price.")
 rec=result.output.model_dump(mode="json")
 with db() as c:
  c.execute(text("UPDATE quotes SET ai_recommendation=CAST(:r AS JSONB),updated_at=now() WHERE id=:id AND tenant_id=:t"),{"r":json.dumps(rec),"id":qid,"t":u["tid"]})
  audit(u["tid"],"AI_SUGGESTED",qid,u["email"],{"price_min":rec["price_min"],"price_max":rec["price_max"],"confidence":rec["confidence"],"provider":str(result.metadata.provider),"model":result.metadata.model},conn=c)
 return rec

@app.post("/securequote/api/quotes/{qid}/approve")
async def approve(qid:str,v:Approval,u=Depends(current_user)):
 if v.final_price<=0: raise HTTPException(422,"Price must be positive")
 public=secrets.token_urlsafe(32); days=quote_link_days()
 with db() as c:
  cur=rowdict(c.execute(text("SELECT state FROM quotes WHERE id=:id AND tenant_id=:t FOR UPDATE"),{"id":qid,"t":u["tid"]}).first())
  if not cur: raise HTTPException(404,"Quote not found")
  if cur["state"] not in APPROVABLE_STATES: raise HTTPException(409,f"Quote is {cur['state']} and cannot be approved again")
  require_can_send(c,u["tid"],qid)
  r=rowdict(c.execute(text("UPDATE quotes SET state='APPROVED',final_price=:p,human_version=CAST(:h AS JSONB),public_token=:tok,public_token_expires_at=now()+make_interval(days=>:d),updated_at=now() WHERE id=:id AND tenant_id=:t RETURNING customer_email,public_token_expires_at"),{"p":v.final_price,"h":json.dumps(v.model_dump(mode="json")),"tok":public,"d":days,"id":qid,"t":u["tid"]}).first())
  business=c.execute(text("SELECT name FROM tenants WHERE id=:t"),{"t":u["tid"]}).scalar()
  audit(u["tid"],"QUOTE_APPROVED",qid,u["email"],{"final_price":str(v.final_price)},conn=c)
 try:
  await send_quote(r["customer_email"],public,business or "",days)
 except Exception:
  # Never leave a quote looking sent when it was not: mark it EMAIL_FAILED so the owner can retry.
  with db() as c:
   c.execute(text("UPDATE quotes SET state='EMAIL_FAILED',updated_at=now() WHERE id=:id AND tenant_id=:t"),{"id":qid,"t":u["tid"]})
   audit(u["tid"],"QUOTE_EMAIL_FAILED",qid,u["email"],conn=c)
  raise HTTPException(502,"The quote email could not be sent, so the customer has not received it. The quote is marked EMAIL_FAILED. Fix email settings, then click Retry send.")
 with db() as c:
  c.execute(text("UPDATE quotes SET sent_at=COALESCE(sent_at,now()),updated_at=now() WHERE id=:id AND tenant_id=:t"),{"id":qid,"t":u["tid"]})
  audit(u["tid"],"QUOTE_EMAILED",qid,u["email"],conn=c)
 return {"state":"APPROVED","public_token":public,"link_expires_at":r["public_token_expires_at"].isoformat()}

VISIBLE_STATES=("APPROVED","ACCEPTED","PAID","DECLINED")
def _load_public(c,tok,lock=False):
 q=rowdict(c.execute(text("SELECT q.id,q.tenant_id,q.customer_name,q.customer_email,q.final_price,q.state,q.payment_status,q.public_token_expires_at,(q.public_token_expires_at IS NOT NULL AND q.public_token_expires_at<=now()) AS expired,t.name AS business FROM quotes q JOIN tenants t ON t.id=q.tenant_id WHERE q.public_token=:p"+(" FOR UPDATE OF q" if lock else "")),{"p":tok}).first())
 if not q or q["state"] not in VISIBLE_STATES: raise HTTPException(404,"Quote not found")
 if q["expired"] or q["public_token_expires_at"] is None: raise HTTPException(410,"This quote link has expired. Please contact the business for a new quote.")
 return q
def customer_page(title,heading,inner):
 return shell(title,f'<main><p class="brand">SECUREQUOTE LITE</p><h1>{html.escape(heading)}</h1><section>{inner}</section></main>')
@app.get("/securequote/q/{tok}",response_class=HTMLResponse)
def public_quote(tok:str):
 with db() as c:q=_load_public(c,tok)
 safe_tok=html.escape(tok,quote=True)
 accept_label="Accept &amp; Pay" if customer_payments_enabled() else "Accept"
 buttons=f'<form method="post" action="/securequote/q/{safe_tok}/accept"><button class="btn">{accept_label}</button></form><form method="post" action="/securequote/q/{safe_tok}/decline"><button class="ghost">Decline</button></form>' if q["state"]=="APPROVED" else ""
 return customer_page("Your service quote","Your service quote",'<p>'+html.escape(str(q["customer_name"]))+', '+html.escape(str(q["business"]))+' sent you an approved quote of <strong>$'+html.escape(str(q["final_price"]))+'</strong>.</p><p>Status: '+html.escape(str(q["state"]))+'</p><p class="note">This link expires '+html.escape(q["public_token_expires_at"].strftime("%B %d, %Y"))+'.</p>'+buttons)
@app.post("/securequote/q/{tok}/accept")
async def accept(tok:str):
 if customer_payments_enabled():
  with db() as c:
   q=_load_public(c,tok,lock=True)
   if q["state"] not in ("APPROVED","ACCEPTED"): raise HTTPException(409,"Quote cannot be accepted")
   c.execute(text("UPDATE quotes SET state='ACCEPTED',accepted_at=COALESCE(accepted_at,now()),updated_at=now() WHERE id=:id"),{"id":q["id"]})
  session=await create_payment_checkout(str(q["id"]),tok,q["customer_email"],int(Decimal(q["final_price"])*100))
  with db() as c:c.execute(text("UPDATE quotes SET stripe_session_id=:s WHERE id=:id"),{"s":session["id"],"id":q["id"]})
  audit(str(q["tenant_id"]),"QUOTE_ACCEPTED",str(q["id"]),data={"stripe_session_id":session["id"]}); return RedirectResponse(session["url"],303)
 with db() as c:
  q=_load_public(c,tok,lock=True)
  if q["state"]=="ACCEPTED": return customer_page("Quote accepted","Already accepted",f"<p>You already accepted this quote. {html.escape(str(q['business']))} will contact you.</p>")
  if q["state"]!="APPROVED": raise HTTPException(409,"Quote cannot be accepted")
  c.execute(text("UPDATE quotes SET state='ACCEPTED',accepted_at=now(),updated_at=now() WHERE id=:id"),{"id":q["id"]})
  audit(str(q["tenant_id"]),"QUOTE_ACCEPTED",str(q["id"]),conn=c)
  owner=c.execute(text("SELECT email FROM users WHERE tenant_id=:t ORDER BY created_at LIMIT 1"),{"t":q["tenant_id"]}).scalar()
 try:
  await send_owner_notice(owner,q["customer_name"],q["final_price"],q["id"]); audit(str(q["tenant_id"]),"OWNER_NOTIFIED",str(q["id"]))
 except Exception:
  audit(str(q["tenant_id"]),"OWNER_NOTIFY_FAILED",str(q["id"]))
 return customer_page("Quote accepted","Thank you — quote accepted",f"<p>{html.escape(str(q['business']))} has been notified and will contact you to schedule the work and arrange payment.</p>")
@app.post("/securequote/q/{tok}/decline")
def decline(tok:str):
 with db() as c:
  q=_load_public(c,tok,lock=True)
  if q["state"]!="APPROVED": raise HTTPException(409,"Quote cannot be declined")
  c.execute(text("UPDATE quotes SET state='DECLINED',updated_at=now() WHERE id=:id"),{"id":q["id"]})
  audit(str(q["tenant_id"]),"QUOTE_DECLINED",str(q["id"]),conn=c)
 return customer_page("Quote declined","Quote declined",f"<p>Thanks for letting {html.escape(str(q['business']))} know.</p>")
@app.post("/securequote/api/billing/checkout")
async def billing(u=Depends(current_user)):
 with db() as c:status,_,_=_usage(c,u["tid"])
 if status==ACTIVE_SUBSCRIPTION: raise HTTPException(409,"Subscription is already active")
 s=await create_subscription_checkout(u["tid"],u["email"]); return {"checkout_url":s["url"]}

# Stripe subscription status -> SecureQuote tenant status
STATUS_MAP={"active":"active","trialing":"active","past_due":"past_due","unpaid":"past_due","canceled":"canceled","incomplete_expired":"canceled","incomplete":"inactive","paused":"inactive"}
def verify_stripe_signature(raw,sig_header,secret,tolerance=300):
 ts=None; v1=[]
 for part in (sig_header or "").split(","):
  k,_,val=part.strip().partition("=")
  if k=="t": ts=val
  elif k=="v1": v1.append(val)
 try: ts_int=int(ts)
 except (TypeError,ValueError): return False
 if not v1 or abs(time.time()-ts_int)>tolerance: return False
 expected=hmac.new(secret.encode(),ts.encode()+b"."+raw,hashlib.sha256).hexdigest()
 return any(hmac.compare_digest(expected,s) for s in v1)
def _tenant_for_subscription(c,obj):
 sub_id=obj.get("id") if obj.get("object")=="subscription" else obj.get("subscription")
 if isinstance(sub_id,dict): sub_id=sub_id.get("id")
 tid=(obj.get("metadata") or {}).get("tenant_id") if obj.get("object")=="subscription" else None
 cust=obj.get("customer")
 if isinstance(cust,dict): cust=cust.get("id")
 row=c.execute(text("SELECT id FROM tenants WHERE (CAST(:tid AS TEXT) IS NOT NULL AND CAST(id AS TEXT)=:tid) OR (CAST(:s AS TEXT) IS NOT NULL AND stripe_subscription_id=:s) OR (CAST(:c AS TEXT) IS NOT NULL AND stripe_customer_id=:c) LIMIT 1"),{"tid":tid,"s":sub_id,"c":cust}).first()
 return str(row[0]) if row else None
def process_stripe_event(c,event):
 etype=event.get("type"); obj=event.get("data",{}).get("object",{})
 if etype=="checkout.session.completed":
  ref=obj.get("client_reference_id"); mode=obj.get("mode")
  if mode=="payment":
   q=rowdict(c.execute(text("UPDATE quotes SET payment_status='paid',state='PAID',updated_at=now() WHERE id=:id AND stripe_session_id=:s RETURNING tenant_id"),{"id":ref,"s":obj.get("id")}).first())
   if q:audit(str(q["tenant_id"]),"PAYMENT_VERIFIED",ref,data={"session_id":obj.get("id")},conn=c)
  elif mode=="subscription" and ref:
   r=c.execute(text("UPDATE tenants SET subscription_status='active',stripe_customer_id=:c,stripe_subscription_id=:s WHERE id=:id RETURNING id"),{"id":ref,"c":obj.get("customer"),"s":obj.get("subscription")}).first()
   if r: audit(ref,"SUBSCRIPTION_ACTIVATED",data={"customer":obj.get("customer"),"subscription":obj.get("subscription")},conn=c)
 elif etype in ("customer.subscription.updated","customer.subscription.deleted"):
  tid=_tenant_for_subscription(c,obj)
  if tid:
   status="canceled" if etype=="customer.subscription.deleted" else STATUS_MAP.get(obj.get("status",""),"inactive")
   period_end=obj.get("current_period_end") or ((obj.get("items") or {}).get("data") or [{}])[0].get("current_period_end")
   c.execute(text("UPDATE tenants SET subscription_status=:st,stripe_subscription_id=COALESCE(stripe_subscription_id,:s),subscription_current_period_end=COALESCE(to_timestamp(CAST(:pe AS DOUBLE PRECISION)),subscription_current_period_end) WHERE id=:id"),{"st":status,"s":obj.get("id"),"pe":period_end,"id":tid})
   audit(tid,"SUBSCRIPTION_"+status.upper(),data={"stripe_status":obj.get("status"),"event":etype},conn=c)
 elif etype=="invoice.payment_failed":
  tid=_tenant_for_subscription(c,obj)
  if tid:
   c.execute(text("UPDATE tenants SET subscription_status='past_due' WHERE id=:id"),{"id":tid})
   audit(tid,"SUBSCRIPTION_PAYMENT_FAILED",data={"invoice":obj.get("id")},conn=c)
@app.post("/securequote/api/stripe/webhook")
async def webhook(request:Request):
 raw=await request.body(); secret=webhook_secret()
 if not secret: raise HTTPException(503,"Webhook secret missing")
 if not verify_stripe_signature(raw,request.headers.get("stripe-signature",""),secret): raise HTTPException(400,"Invalid Stripe signature")
 event=json.loads(raw); event_id=event.get("id")
 if not event_id: raise HTTPException(400,"Stripe event id missing")
 # One transaction: the event is recorded as handled only if processing succeeds.
 # If processing raises, everything rolls back, Stripe gets a 500, and its retry is processed again.
 with db() as c:
  inserted=c.execute(text("INSERT INTO stripe_events(id,event_type) VALUES(:id,:t) ON CONFLICT (id) DO NOTHING RETURNING id"),{"id":event_id,"t":event.get("type","unknown")}).first()
  if not inserted: return {"received":True,"duplicate":True}
  process_stripe_event(c,event)
 return {"received":True}

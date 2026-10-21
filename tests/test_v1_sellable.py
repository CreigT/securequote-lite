"""Tests for the v1-sellable changes. Database tests run when TEST_DATABASE_URL points at a disposable Postgres."""
import hashlib,hmac,json,os,re,time,unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import AsyncMock,patch
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text
from applications.securequote_lite import production_app as pa
from applications.securequote_lite import production_db as pdb
from applications.securequote_lite import auth
from applications.securequote_lite.business.schemas import SecureQuoteAIRecommendation
from src.ai.gateway import ProviderOperationMetadata,StructuredGenerationResult

ROOT=Path(__file__).resolve().parents[1]
SECRET="s"*40
WEBHOOK_SECRET="whsec_test_only"
TEST_DB=os.getenv("TEST_DATABASE_URL","")
BASE_ENV={"SECUREQUOTE_SESSION_SECRET":SECRET,"STRIPE_WEBHOOK_SECRET":WEBHOOK_SECRET,"SECUREQUOTE_BASE_URL":"https://sq.example","SECUREQUOTE_LITE_AI_PROVIDER":"","SECUREQUOTE_CUSTOMER_PAYMENTS_ENABLED":"false","SECUREQUOTE_TRIAL_QUOTES":"3","SECUREQUOTE_QUOTE_LINK_DAYS":"30"}
INTAKE={"customer_name":"Dana <b>Lee</b>","customer_email":"dana@example.com","customer_phone":"555-010-2020","job_type":"Daycare cleaning","service_location":"Fontana, CA","scope_summary":"4,000 sq ft daycare, five nights per week","preferred_timing":"Weeknights","notes":"Gate code at desk"}

def sign(payload,secret=WEBHOOK_SECRET,ts=None):
 ts=str(int(ts or time.time())); raw=json.dumps(payload).encode()
 sig=hmac.new(secret.encode(),ts.encode()+b"."+raw,hashlib.sha256).hexdigest()
 return raw,{"stripe-signature":f"t={ts},v1={sig}","content-type":"application/json"}

class NoDatabaseTests(unittest.TestCase):
 def setUp(self):
  self.env=patch.dict(os.environ,dict(BASE_ENV,DATABASE_URL=""),clear=False); self.env.start(); self.client=TestClient(pa.app)
 def tearDown(self): self.env.stop()
 # 1a
 def test_missing_secret_rejects_auth_even_with_forged_empty_key_token(self):
  os.environ["SECUREQUOTE_SESSION_SECRET"]=""
  body="eyJ1aWQiOiIxIiwidGlkIjoiMSIsImVtYWlsIjoieCIsImV4cCI6OTk5OTk5OTk5OX0"
  forged=body+"."+hmac.new(b"",body.encode(),hashlib.sha256).hexdigest()
  r=self.client.get("/securequote/api/quotes",headers={"Authorization":"Bearer "+forged})
  self.assertEqual(r.status_code,503)
  with self.assertRaises(HTTPException): auth.token({"id":"1","tenant_id":"1","email":"x"})
  self.assertEqual(self.client.post("/securequote/api/auth/signup",json={"business_name":"A","email":"a@example.com","password":"x"*14}).status_code,503)
  self.assertFalse(self.client.get("/securequote/health").json()["session_secret"])
 def test_short_secret_is_treated_as_missing(self):
  os.environ["SECUREQUOTE_SESSION_SECRET"]="short"
  self.assertFalse(auth.session_secret_configured())
  with self.assertRaises(HTTPException): auth.current_user("Bearer a.b")
 def test_valid_secret_round_trip_and_tamper(self):
  t=auth.token({"id":"u1","tenant_id":"t1","email":"o@example.com"})
  self.assertEqual(auth.current_user("Bearer "+t)["tid"],"t1")
  with self.assertRaises(HTTPException) as e: auth.current_user("Bearer "+t[:-2]+"00")
  self.assertEqual(e.exception.status_code,401)
 # 1b
 def test_dashboard_escapes_customer_values(self):
  page=self.client.get("/app").text
  self.assertIn("function esc(",page)
  self.assertIn("esc(q.customer_name)",page); self.assertIn("esc(e.event)",page); self.assertIn("esc(q.id)",page)
  self.assertNotRegex(page,r'"\+q\.customer_name\+"')
 # 1f
 def test_password_minimum_is_12_on_page_and_server(self):
  self.assertEqual(auth.PASSWORD_MIN_LENGTH,12)
  page=self.client.get("/signup").text
  self.assertIn('minlength="12"',page); self.assertNotIn('minlength="8"',page)
  with self.assertRaises(HTTPException) as e: auth.create_owner("Biz","a@example.com","elevenchars")
  self.assertEqual(e.exception.status_code,422)
 # 1e signature parsing
 def test_stripe_signature_verification(self):
  raw,h=sign({"id":"evt_1"})
  self.assertTrue(pa.verify_stripe_signature(raw,h["stripe-signature"],WEBHOOK_SECRET))
  self.assertFalse(pa.verify_stripe_signature(raw,h["stripe-signature"],"other"))
  old_raw,old=sign({"id":"evt_1"},ts=time.time()-3600)
  self.assertFalse(pa.verify_stripe_signature(old_raw,old["stripe-signature"],WEBHOOK_SECRET))
  self.assertFalse(pa.verify_stripe_signature(raw,"garbage",WEBHOOK_SECRET))
  r=self.client.post("/securequote/api/stripe/webhook",content=raw,headers={"stripe-signature":"t=1,v1=bad"})
  self.assertEqual(r.status_code,400)
 # 2
 def test_customer_payments_flag_defaults_off(self):
  os.environ.pop("SECUREQUOTE_CUSTOMER_PAYMENTS_ENABLED",None)
  self.assertFalse(pa.customer_payments_enabled())
  os.environ["SECUREQUOTE_CUSTOMER_PAYMENTS_ENABLED"]="true"; self.assertTrue(pa.customer_payments_enabled())
 # 3
 def test_billing_pages_exist(self):
  for path in ("/securequote/billing/success","/securequote/billing/cancel"):
   r=self.client.get(path); self.assertEqual(r.status_code,200); self.assertIn("Sponsored by CREIGNIFICENT LLC",r.text)
 def test_dashboard_has_subscribe_button(self):
  page=self.client.get("/app").text
  self.assertIn('id="subscribe"',page); self.assertIn("/securequote/api/billing/checkout",page)
 # 4
 def test_ai_button_hidden_without_provider(self):
  self.assertIsNone(pa.get_ai_provider()); self.assertFalse(self.client.get("/securequote/health").json()["ai"])
  self.assertIn('id="suggest" type="button" hidden',self.client.get("/app").text)
  os.environ["SECUREQUOTE_LITE_AI_PROVIDER"]="openai"; os.environ["OPENAI_API_KEY"]=""; os.environ["OPENAI_MODEL"]=""
  self.assertIsNone(pa.get_ai_provider())
 # 5
 def test_legal_pages(self):
  for path in ("/terms","/privacy","/refunds"):
   r=self.client.get(path); self.assertEqual(r.status_code,200,path)
   self.assertIn("Draft — review before launch",r.text); self.assertIn("Creignificent LLC",r.text); self.assertIn("Creignificent@gmail.com",r.text)
  home=self.client.get("/").text
  for path in ("/terms","/privacy","/refunds"): self.assertIn(f'href="{path}"',home)
 def test_sponsor_line_in_footer_metadata_and_readme(self):
  home=self.client.get("/").text
  self.assertIn("<footer>Sponsored by CREIGNIFICENT LLC.",home)
  self.assertRegex(home,r'<meta name="description" content="[^"]*Sponsored by CREIGNIFICENT LLC\.')
  self.assertIn("Sponsored by CREIGNIFICENT LLC.",(ROOT/"README.md").read_text())
 def test_homepage_does_not_promise_customer_payment_collection(self):
  home=self.client.get("/").text
  self.assertNotIn("Customer pays",home); self.assertNotIn("Accept & Pay",home); self.assertNotIn("Stripe payment verification",home)
 # 6
 def test_env_example_lists_required_vars(self):
  env=(ROOT/".env.example").read_text()
  for name in ("DATABASE_URL","SECUREQUOTE_SESSION_SECRET","SECUREQUOTE_BASE_URL","STRIPE_SECRET_KEY","STRIPE_WEBHOOK_SECRET","STRIPE_MONTHLY_PRICE_ID","RESEND_API_KEY","SECUREQUOTE_FROM_EMAIL","SECUREQUOTE_LITE_AI_PROVIDER","OPENAI_API_KEY","GEMINI_API_KEY","SECUREQUOTE_CUSTOMER_PAYMENTS_ENABLED","SECUREQUOTE_TRIAL_QUOTES","SECUREQUOTE_QUOTE_LINK_DAYS","SECUREQUOTE_AUTO_MIGRATE"):
   self.assertRegex(env,rf"(?m)^{name}=",name)
  self.assertTrue((ROOT/"LAUNCH_CHECKLIST.md").exists())
 def test_embedded_schema_matches_migration_files(self):
  files=[]
  for f in sorted((ROOT/"migrations").glob("*.sql")): files+=[s.strip() for s in f.read_text().split(";") if s.strip()]
  self.assertEqual(files,pdb.schema_statements())

@unittest.skipUnless(TEST_DB,"TEST_DATABASE_URL not set; skipping Postgres-backed tests")
class DatabaseTests(unittest.TestCase):
 def setUp(self):
  self.env=patch.dict(os.environ,dict(BASE_ENV,DATABASE_URL=TEST_DB),clear=False); self.env.start()
  pdb.init_db()
  with pdb.db() as c: c.execute(text("TRUNCATE tenants,users,quotes,audit_events,stripe_events CASCADE"))
  self.client=TestClient(pa.app)
  self.send=patch.object(pa,"send_quote",new=AsyncMock(return_value={"id":"em_1"})); self.send_mock=self.send.start()
  self.notify=patch.object(pa,"send_owner_notice",new=AsyncMock(return_value={"id":"em_2"})); self.notify_mock=self.notify.start()
 def tearDown(self):
  self.send.stop(); self.notify.stop(); self.env.stop()
 def owner(self,name="ABC Cleaning",email="owner@abc.example"):
  r=self.client.post("/securequote/api/auth/signup",json={"business_name":name,"email":email,"password":"correct-horse-battery"})
  self.assertEqual(r.status_code,200,r.text); return {"Authorization":"Bearer "+r.json()["access_token"]},r.json()["tenant_id"]
 def quote(self,h):
  r=self.client.post("/securequote/api/quotes",json=INTAKE,headers=h); self.assertEqual(r.status_code,200,r.text); return r.json()["id"]
 def approve(self,h,qid,price="1950"):
  return self.client.post(f"/securequote/api/quotes/{qid}/approve",json={"final_price":price},headers=h)
 def q(self,qid):
  with pdb.db() as c: return pdb.rowdict(c.execute(text("SELECT * FROM quotes WHERE id=:id"),{"id":qid}).first())
 def set_q(self,qid,**cols):
  with pdb.db() as c:
   for k,v in cols.items(): c.execute(text(f"UPDATE quotes SET {k}=:v WHERE id=:id"),{"v":v,"id":qid})
 def set_status(self,tid,status):
  with pdb.db() as c: c.execute(text("UPDATE tenants SET subscription_status=:s WHERE id=:id"),{"s":status,"id":tid})
 def status(self,tid):
  with pdb.db() as c: return c.execute(text("SELECT subscription_status FROM tenants WHERE id=:id"),{"id":tid}).scalar()
 def events(self,tid):
  with pdb.db() as c: return [r[0] for r in c.execute(text("SELECT event FROM audit_events WHERE tenant_id=:t ORDER BY id"),{"t":tid})]
 # 1c
 def test_approve_only_from_new_or_email_failed(self):
  h,_=self.owner(); qid=self.quote(h)
  self.assertEqual(self.approve(h,qid).status_code,200)
  self.assertEqual(self.approve(h,qid,"1").status_code,409)
  for state in ("PAID","DECLINED","ACCEPTED"):
   self.set_q(qid,state=state); r=self.approve(h,qid,"1"); self.assertEqual(r.status_code,409,state)
  self.assertEqual(str(self.q(qid)["final_price"]),"1950.00")
 # 1d
 def test_email_failure_marks_email_failed_and_retry_works(self):
  h,tid=self.owner(); qid=self.quote(h)
  self.send_mock.side_effect=HTTPException(502,"Email delivery failed")
  r=self.approve(h,qid); self.assertEqual(r.status_code,502); self.assertIn("EMAIL_FAILED",r.json()["detail"])
  row=self.q(qid); self.assertEqual(row["state"],"EMAIL_FAILED"); self.assertIsNone(row["sent_at"])
  old_token=row["public_token"]
  self.assertEqual(self.client.get(f"/securequote/q/{old_token}").status_code,404)
  self.send_mock.side_effect=None
  r=self.approve(h,qid,"2000"); self.assertEqual(r.status_code,200)
  row=self.q(qid); self.assertEqual(row["state"],"APPROVED"); self.assertIsNotNone(row["sent_at"]); self.assertNotEqual(row["public_token"],old_token)
  self.assertIn("QUOTE_EMAIL_FAILED",self.events(tid)); self.assertIn("QUOTE_EMAILED",self.events(tid))
 # 1g
 def test_quote_link_expiry(self):
  os.environ["SECUREQUOTE_QUOTE_LINK_DAYS"]="7"
  h,_=self.owner(); qid=self.quote(h); tok=self.approve(h,qid).json()["public_token"]
  exp=self.q(qid)["public_token_expires_at"]; days=(exp-datetime.now(timezone.utc)).total_seconds()/86400
  self.assertTrue(6.9<days<7.1,days)
  self.assertEqual(self.client.get(f"/securequote/q/{tok}").status_code,200)
  self.set_q(qid,public_token_expires_at=datetime(2020,1,1,tzinfo=timezone.utc))
  self.assertEqual(self.client.get(f"/securequote/q/{tok}").status_code,410)
  self.assertEqual(self.client.post(f"/securequote/q/{tok}/accept").status_code,410)
  self.assertEqual(self.client.post(f"/securequote/q/{tok}/decline").status_code,410)
 # 2
 def test_accept_without_payments_records_and_notifies_owner(self):
  h,tid=self.owner(); qid=self.quote(h); tok=self.approve(h,qid).json()["public_token"]
  page=self.client.get(f"/securequote/q/{tok}").text
  self.assertIn(">Accept<",page); self.assertNotIn("Accept &amp; Pay",page); self.assertIn("Dana &lt;b&gt;Lee&lt;/b&gt;",page)
  with patch.object(pa,"create_payment_checkout",new=AsyncMock()) as checkout:
   r=self.client.post(f"/securequote/q/{tok}/accept",follow_redirects=False)
   checkout.assert_not_called()
  self.assertEqual(r.status_code,200); self.assertIn("quote accepted",r.text)
  self.assertEqual(self.q(qid)["state"],"ACCEPTED")
  self.notify_mock.assert_awaited_once(); self.assertEqual(self.notify_mock.await_args.args[0],"owner@abc.example")
  self.assertIn("OWNER_NOTIFIED",self.events(tid))
  self.assertEqual(self.client.post(f"/securequote/q/{tok}/decline").status_code,409)
 def test_owner_notice_failure_still_records_acceptance(self):
  h,tid=self.owner(); qid=self.quote(h); tok=self.approve(h,qid).json()["public_token"]
  self.notify_mock.side_effect=HTTPException(502,"x")
  self.assertEqual(self.client.post(f"/securequote/q/{tok}/accept").status_code,200)
  self.assertEqual(self.q(qid)["state"],"ACCEPTED"); self.assertIn("OWNER_NOTIFY_FAILED",self.events(tid))
 def test_accept_with_payments_enabled_uses_stripe_checkout(self):
  os.environ["SECUREQUOTE_CUSTOMER_PAYMENTS_ENABLED"]="true"
  h,_=self.owner(); qid=self.quote(h); tok=self.approve(h,qid).json()["public_token"]
  self.assertIn("Accept &amp; Pay",self.client.get(f"/securequote/q/{tok}").text)
  with patch.object(pa,"create_payment_checkout",new=AsyncMock(return_value={"id":"cs_1","url":"https://checkout.stripe.com/x"})):
   r=self.client.post(f"/securequote/q/{tok}/accept",follow_redirects=False)
  self.assertEqual(r.status_code,303); self.assertEqual(r.headers["location"],"https://checkout.stripe.com/x")
  self.assertEqual(self.q(qid)["stripe_session_id"],"cs_1")
 def test_decline(self):
  h,_=self.owner(); qid=self.quote(h); tok=self.approve(h,qid).json()["public_token"]
  self.assertEqual(self.client.post(f"/securequote/q/{tok}/decline").status_code,200)
  self.assertEqual(self.q(qid)["state"],"DECLINED")
 # 3 trial and gating
 def test_trial_limit_blocks_new_quotes_until_subscribed(self):
  os.environ["SECUREQUOTE_TRIAL_QUOTES"]="2"
  h,tid=self.owner(); self.quote(h); self.quote(h)
  r=self.client.post("/securequote/api/quotes",json=INTAKE,headers=h); self.assertEqual(r.status_code,402)
  acct=self.client.get("/securequote/api/account",headers=h).json()
  self.assertEqual((acct["trial_quotes_used"],acct["trial_quote_limit"]),(2,2))
  self.set_status(tid,"active"); self.quote(h)
  self.set_status(tid,"past_due"); self.assertEqual(self.client.post("/securequote/api/quotes",json=INTAKE,headers=h).status_code,402)
 def test_sending_is_blocked_after_trial_for_inactive_accounts(self):
  os.environ["SECUREQUOTE_TRIAL_QUOTES"]="1"
  h,tid=self.owner(); self.set_status(tid,"active"); q1=self.quote(h); q2=self.quote(h)
  self.set_status(tid,"canceled")
  self.assertEqual(self.approve(h,q1).status_code,200)
  self.assertEqual(self.approve(h,q2).status_code,402)
 def test_subscribe_checkout(self):
  h,tid=self.owner()
  with patch.object(pa,"create_subscription_checkout",new=AsyncMock(return_value={"url":"https://checkout.stripe.com/sub"})) as m:
   r=self.client.post("/securequote/api/billing/checkout",headers=h)
   self.assertEqual(r.json()["checkout_url"],"https://checkout.stripe.com/sub"); self.assertEqual(m.await_args.args[0],tid)
   self.set_status(tid,"active"); self.assertEqual(self.client.post("/securequote/api/billing/checkout",headers=h).status_code,409)
 # 3 webhooks + 1e
 def hook(self,payload,client=None):
  raw,h=sign(payload); return (client or self.client).post("/securequote/api/stripe/webhook",content=raw,headers=h)
 def test_subscription_lifecycle_webhooks(self):
  _,tid=self.owner()
  r=self.hook({"id":"evt_a","type":"checkout.session.completed","data":{"object":{"mode":"subscription","client_reference_id":tid,"customer":"cus_1","subscription":"sub_1"}}})
  self.assertEqual(r.json(),{"received":True}); self.assertEqual(self.status(tid),"active")
  self.hook({"id":"evt_b","type":"invoice.payment_failed","data":{"object":{"object":"invoice","id":"in_1","customer":"cus_1"}}})
  self.assertEqual(self.status(tid),"past_due")
  self.hook({"id":"evt_c","type":"customer.subscription.updated","data":{"object":{"object":"subscription","id":"sub_1","customer":"cus_1","status":"active","current_period_end":1893456000}}})
  self.assertEqual(self.status(tid),"active")
  self.hook({"id":"evt_d","type":"customer.subscription.deleted","data":{"object":{"object":"subscription","id":"sub_1","customer":"cus_1","status":"canceled"}}})
  self.assertEqual(self.status(tid),"canceled")
  ev=self.events(tid); self.assertIn("SUBSCRIPTION_ACTIVATED",ev); self.assertIn("SUBSCRIPTION_PAYMENT_FAILED",ev); self.assertIn("SUBSCRIPTION_CANCELED",ev)
 def test_subscription_updated_maps_unpaid_to_past_due(self):
  _,tid=self.owner()
  self.hook({"id":"evt_u","type":"customer.subscription.updated","data":{"object":{"object":"subscription","id":"sub_9","customer":"cus_9","status":"unpaid","metadata":{"tenant_id":tid}}}})
  self.assertEqual(self.status(tid),"past_due")
 def test_duplicate_event_is_ignored(self):
  _,tid=self.owner(); payload={"id":"evt_dup","type":"checkout.session.completed","data":{"object":{"mode":"subscription","client_reference_id":tid,"customer":"cus_1","subscription":"sub_1"}}}
  self.hook(payload); self.set_status(tid,"canceled")
  self.assertTrue(self.hook(payload).json()["duplicate"]); self.assertEqual(self.status(tid),"canceled")
 def test_failed_webhook_processing_is_not_marked_handled_and_retry_succeeds(self):
  _,tid=self.owner(); payload={"id":"evt_retry","type":"checkout.session.completed","data":{"object":{"mode":"subscription","client_reference_id":tid,"customer":"cus_1","subscription":"sub_1"}}}
  with patch.object(pa,"process_stripe_event",side_effect=RuntimeError("db hiccup")):
   r=self.hook(payload,TestClient(pa.app,raise_server_exceptions=False)); self.assertEqual(r.status_code,500)
  with pdb.db() as c: self.assertIsNone(c.execute(text("SELECT 1 FROM stripe_events WHERE id='evt_retry'")).first())
  self.assertEqual(self.status(tid),"inactive")
  self.assertEqual(self.hook(payload).json(),{"received":True}); self.assertEqual(self.status(tid),"active")
 # isolation
 def test_tenant_isolation(self):
  ha,_=self.owner(); qa=self.quote(ha); hb,_=self.owner("XYZ Landscaping","owner@xyz.example")
  self.assertEqual(self.client.get("/securequote/api/quotes",headers=hb).json(),[])
  self.assertEqual(self.approve(hb,qa).status_code,404)
  self.assertEqual(self.client.post(f"/securequote/api/quotes/{qa}/suggest",headers=hb).status_code,503)
 # 4
 def test_suggest_price_with_fake_provider(self):
  seen={}
  class Fake:
   configured=True
   def generate(self,req):
    seen.update(req.input_data)
    out=SecureQuoteAIRecommendation(summary="Nightly daycare cleaning, five nights.",line_items=[{"description":"Nightly cleaning","amount":"1850"}],price_min="1700",price_max="2000",confidence=72,evidence=[{"input_field":"scope_summary","fact":"4,000 sq ft"}])
    return StructuredGenerationResult(output=out,metadata=ProviderOperationMetadata(provider="openai",model="test-model",operation_status="completed",timestamp=datetime.now(timezone.utc),latency_ms=1))
  h,tid=self.owner(); qid=self.quote(h)
  with patch.object(pa,"get_ai_provider",return_value=Fake()):
   self.assertTrue(self.client.get("/securequote/api/account",headers=h).json()["ai_enabled"])
   r=self.client.post(f"/securequote/api/quotes/{qid}/suggest",headers=h)
  self.assertEqual(r.status_code,200,r.text); self.assertEqual(float(r.json()["price_min"]),1700.0)
  for pii in ("customer_name","customer_email","customer_phone"): self.assertNotIn(pii,seen)
  self.assertEqual(seen["scope_summary"],INTAKE["scope_summary"])
  row=self.q(qid); self.assertEqual(float(row["ai_recommendation"]["price_max"]),2000.0); self.assertEqual(row["state"],"NEW")
  self.assertIn("AI_SUGGESTED",self.events(tid))
  self.assertEqual(self.approve(h,qid,"1950").status_code,200)
  self.assertEqual(str(self.q(qid)["final_price"]),"1950.00")
 def test_suggest_without_provider_returns_503(self):
  h,_=self.owner(); qid=self.quote(h)
  self.assertFalse(self.client.get("/securequote/api/account",headers=h).json()["ai_enabled"])
  self.assertEqual(self.client.post(f"/securequote/api/quotes/{qid}/suggest",headers=h).status_code,503)

if __name__=="__main__": unittest.main()

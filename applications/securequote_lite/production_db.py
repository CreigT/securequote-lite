import os,json
from contextlib import contextmanager
from sqlalchemy import create_engine,text
# Keep in sync with migrations/*.sql (tests enforce this). Embedded so serverless bundles do not need the migrations folder.
SCHEMA="""CREATE TABLE IF NOT EXISTS tenants(id UUID PRIMARY KEY,name TEXT NOT NULL,stripe_customer_id TEXT,subscription_status TEXT NOT NULL DEFAULT 'inactive',created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS users(id UUID PRIMARY KEY,tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,role TEXT NOT NULL DEFAULT 'owner',created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS quotes(id UUID PRIMARY KEY,tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,customer_name TEXT NOT NULL,customer_email TEXT NOT NULL,customer_phone TEXT NOT NULL,payload JSONB NOT NULL,state TEXT NOT NULL DEFAULT 'NEW',ai_recommendation JSONB,human_version JSONB,final_price NUMERIC(12,2),public_token TEXT UNIQUE,stripe_session_id TEXT,payment_status TEXT NOT NULL DEFAULT 'unpaid',created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS idx_quotes_tenant ON quotes(tenant_id);
CREATE TABLE IF NOT EXISTS audit_events(id BIGSERIAL PRIMARY KEY,tenant_id UUID,quote_id UUID,event TEXT NOT NULL,actor TEXT,data JSONB NOT NULL DEFAULT '{}'::jsonb,created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_events(tenant_id);
CREATE TABLE IF NOT EXISTS stripe_events(id TEXT PRIMARY KEY,event_type TEXT NOT NULL,processed_at TIMESTAMPTZ NOT NULL DEFAULT now());
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS stripe_subscription_id TEXT;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS subscription_current_period_end TIMESTAMPTZ;
ALTER TABLE quotes ADD COLUMN IF NOT EXISTS public_token_expires_at TIMESTAMPTZ;
ALTER TABLE quotes ADD COLUMN IF NOT EXISTS sent_at TIMESTAMPTZ;
ALTER TABLE quotes ADD COLUMN IF NOT EXISTS accepted_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_tenants_stripe_customer ON tenants(stripe_customer_id);
CREATE INDEX IF NOT EXISTS idx_tenants_stripe_subscription ON tenants(stripe_subscription_id);"""
_engines={}
def get_engine():
    url=os.getenv("DATABASE_URL","")
    if not url: return None
    if url not in _engines: _engines[url]=create_engine(url,pool_pre_ping=True)
    return _engines[url]
def schema_statements(): return [s.strip() for s in SCHEMA.split(";") if s.strip()]
@contextmanager
def db():
    engine=get_engine()
    if engine is None: raise RuntimeError("DATABASE_URL is required")
    with engine.begin() as conn: yield conn
def init_db():
    with db() as c:
        for s in schema_statements(): c.execute(text(s))
def audit(tenant_id,event,quote_id=None,actor=None,data=None,conn=None):
    stmt=text("INSERT INTO audit_events(tenant_id,quote_id,event,actor,data) VALUES(:t,:q,:e,:a,CAST(:d AS JSONB))")
    params={"t":tenant_id,"q":quote_id,"e":event,"a":actor,"d":json.dumps(data or {})}
    if conn is not None: conn.execute(stmt,params); return
    with db() as c:c.execute(stmt,params)
def rowdict(row): return dict(row._mapping) if row else None

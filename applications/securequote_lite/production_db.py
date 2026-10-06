import os,json
from contextlib import contextmanager
from sqlalchemy import create_engine,text
DATABASE_URL=os.getenv("DATABASE_URL",""); engine=create_engine(DATABASE_URL,pool_pre_ping=True) if DATABASE_URL else None
SCHEMA="""CREATE TABLE IF NOT EXISTS tenants(id UUID PRIMARY KEY,name TEXT NOT NULL,stripe_customer_id TEXT,subscription_status TEXT NOT NULL DEFAULT 'inactive',created_at TIMESTAMPTZ NOT NULL DEFAULT now()); CREATE TABLE IF NOT EXISTS users(id UUID PRIMARY KEY,tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,role TEXT NOT NULL DEFAULT 'owner',created_at TIMESTAMPTZ NOT NULL DEFAULT now()); CREATE TABLE IF NOT EXISTS quotes(id UUID PRIMARY KEY,tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,customer_name TEXT NOT NULL,customer_email TEXT NOT NULL,customer_phone TEXT NOT NULL,payload JSONB NOT NULL,state TEXT NOT NULL DEFAULT 'NEW',ai_recommendation JSONB,human_version JSONB,final_price NUMERIC(12,2),public_token TEXT UNIQUE,stripe_session_id TEXT,payment_status TEXT NOT NULL DEFAULT 'unpaid',created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now()); CREATE INDEX IF NOT EXISTS idx_quotes_tenant ON quotes(tenant_id); CREATE TABLE IF NOT EXISTS audit_events(id BIGSERIAL PRIMARY KEY,tenant_id UUID,quote_id UUID,event TEXT NOT NULL,actor TEXT,data JSONB NOT NULL DEFAULT '{}'::jsonb,created_at TIMESTAMPTZ NOT NULL DEFAULT now()); CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_events(tenant_id);"""
@contextmanager
def db():
    if engine is None: raise RuntimeError("DATABASE_URL is required")
    with engine.begin() as conn: yield conn
def init_db():
    with db() as c:
        for s in [x.strip() for x in SCHEMA.split(";") if x.strip()]: c.execute(text(s))
def audit(tenant_id,event,quote_id=None,actor=None,data=None):
    with db() as c:c.execute(text("INSERT INTO audit_events(tenant_id,quote_id,event,actor,data) VALUES(:t,:q,:e,:a,CAST(:d AS JSONB))"),{"t":tenant_id,"q":quote_id,"e":event,"a":actor,"d":json.dumps(data or {})})
def rowdict(row): return dict(row._mapping) if row else None

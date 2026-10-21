import os,base64,hashlib,hmac,json,time
from uuid import uuid4
from fastapi import Header,HTTPException
from sqlalchemy import text
from .production_db import db,rowdict
PASSWORD_MIN_LENGTH=12
SESSION_SECRET_MIN_LENGTH=32
SESSION_SECONDS=43200
def session_secret():
 """Return the configured signing secret, or "" when it is missing or too short to be safe."""
 secret=os.getenv("SECUREQUOTE_SESSION_SECRET","").strip()
 return secret if len(secret)>=SESSION_SECRET_MIN_LENGTH else ""
def session_secret_configured(): return bool(session_secret())
def _require_secret():
 secret=session_secret()
 if not secret: raise HTTPException(503,"Sign-in is not configured on this deployment (SECUREQUOTE_SESSION_SECRET missing or shorter than 32 characters)")
 return secret
def password_hash(password):
 salt=os.urandom(16); rounds=210000; digest=hashlib.pbkdf2_hmac("sha256",password.encode(),salt,rounds); return "pbkdf2_sha256$"+str(rounds)+"$"+base64.urlsafe_b64encode(salt).decode()+"$"+base64.urlsafe_b64encode(digest).decode()
def verify_password(password,encoded):
 try:
  _,rs,salt,digest=encoded.split("$"); got=hashlib.pbkdf2_hmac("sha256",password.encode(),base64.urlsafe_b64decode(salt),int(rs)); return hmac.compare_digest(got,base64.urlsafe_b64decode(digest))
 except Exception:return False
def token(user):
 secret=_require_secret()
 body=base64.urlsafe_b64encode(json.dumps({"uid":str(user["id"]),"tid":str(user["tenant_id"]),"email":user["email"],"exp":int(time.time())+SESSION_SECONDS},separators=(",",":")).encode()).decode().rstrip("="); sig=hmac.new(secret.encode(),body.encode(),hashlib.sha256).hexdigest(); return body+"."+sig
def current_user(authorization=Header(None)):
 secret=_require_secret()
 if not authorization or not authorization.startswith("Bearer "): raise HTTPException(401,"Authentication required")
 raw=authorization[7:]
 try:
  body,sig=raw.split(".",1)
  if not hmac.compare_digest(sig,hmac.new(secret.encode(),body.encode(),hashlib.sha256).hexdigest()): raise ValueError()
  p=json.loads(base64.urlsafe_b64decode(body+"="*(-len(body)%4)))
  if p["exp"]<time.time(): raise ValueError()
  return p
 except Exception: raise HTTPException(401,"Invalid or expired session")
def create_owner(business,email,password):
 _require_secret()
 if len(password)<PASSWORD_MIN_LENGTH: raise HTTPException(422,f"Password must be at least {PASSWORD_MIN_LENGTH} characters")
 if not business.strip(): raise HTTPException(422,"Business name is required")
 tid=str(uuid4()); uid=str(uuid4())
 try:
  with db() as c:
   c.execute(text("INSERT INTO tenants(id,name) VALUES(:id,:n)"),{"id":tid,"n":business.strip()}); c.execute(text("INSERT INTO users(id,tenant_id,email,password_hash) VALUES(:id,:t,:e,:p)"),{"id":uid,"t":tid,"e":email.lower(),"p":password_hash(password)})
 except Exception: raise HTTPException(409,"Account already exists")
 return {"id":uid,"tenant_id":tid,"email":email.lower()}
def authenticate(email,password):
 _require_secret()
 with db() as c:user=rowdict(c.execute(text("SELECT * FROM users WHERE email=:e"),{"e":email.lower()}).first())
 if not user or not verify_password(password,user["password_hash"]): raise HTTPException(401,"Invalid credentials")
 return user

"""Application-specific configuration for SecureQuote Lite."""

import os
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent
APP_NAMESPACE = "securequote_lite"
ROUTE_PREFIX = "/securequote"
DATA_NAMESPACE = "securequote_lite_drafts"
AUDIT_NAMESPACE = "securequote_lite"
AI_PROVIDER = os.getenv("SECUREQUOTE_LITE_AI_PROVIDER", "").strip().lower()
MAX_UPLOAD_BYTES = int(os.getenv("SECUREQUOTE_LITE_MAX_UPLOAD_BYTES", str(5 * 1024 * 1024)))
AUDIT_PATH = Path(os.getenv("SECUREQUOTE_LITE_AUDIT_LOG", APP_ROOT / "logs" / "audit.jsonl"))
if not AUDIT_PATH.is_absolute():
    AUDIT_PATH = APP_ROOT / AUDIT_PATH

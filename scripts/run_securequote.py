"""Start SecureQuote Lite locally."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import uvicorn


if __name__ == "__main__":
    uvicorn.run("applications.securequote_lite.app:app", host="127.0.0.1", port=8010, reload=False)

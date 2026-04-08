import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

SERPAPI_KEY      = "SERPAPI_KEY_REDACTED"

CREDENTIALS_FILE = "melt2.json"

# Immigration finder spreadsheet — one tab per company type
SPREADSHEET_ID  = "1L5yf4yREvRJpcWlrWb55-HPTMRERhCDCL7X22DPWDBE"
IMMIGRATION_TABS = ["LawFirms", "Advisors", "LegaltechBrokers", "Charities"]
DEFAULT_TAB      = "LawFirms"

# ── LLM provider — Vertex AI (service account in CREDENTIALS_FILE) ────────────
VERTEX_PROJECT  = "project-62f74cb6-5066-46e8-a01"
VERTEX_LOCATION = "us-central1"
DEFAULT_MODEL   = "vertex_ai/gemini-2.5-flash"

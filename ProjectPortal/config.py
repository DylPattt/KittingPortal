"""All settings in one place. Override anything with an environment variable.

Windows examples (PowerShell):
    $env:PORTAL_DATA_DIR = "C:\\Users\\46513\Downloads\\ProjectPortal\\ProjectPortal\data"
    $env:PORTAL_REFRESH_TIMES = "06:00,14:00"
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _env(name, default=None):
    return os.environ.get(name, default)


# ---- Source Excel files (read-only; the OneDrive/SharePoint-synced copies) ----
SOURCE_DIR = _env("PORTAL_DATA_DIR", os.path.join(BASE_DIR, "data"))
ONHAND_FILE = os.path.join(SOURCE_DIR, "Sharepoint_OnHand.xlsx")
BOM_FILE = os.path.join(SOURCE_DIR, "Combined_BOMs.xlsx")
ONHAND_SHEET = _env("PORTAL_ONHAND_SHEET", "OnHand")
BOM_SHEET = _env("PORTAL_BOM_SHEET", "Combined Data")


# ---- SQLite database ----
# Keep the database OUT of OneDrive: sync clients lock/corrupt SQLite files.
def _default_state_dir():
    local = _env("LOCALAPPDATA")
    if os.name == "nt" and local:
        return os.path.join(local, "KittingPortal")
    return os.path.join(BASE_DIR, "instance")


STATE_DIR = _env("PORTAL_STATE_DIR", _default_state_dir())
DB_FILE = _env("PORTAL_DB", os.path.join(STATE_DIR, "portal.db"))
SECRET_KEY_FILE = os.path.join(STATE_DIR, "secret.key")

# ---- Data refresh (twice a day by default, local server time, 24h clock) ----
REFRESH_TIMES = [t.strip() for t in _env("PORTAL_REFRESH_TIMES", "06:00,14:00").split(",") if t.strip()]
SCHEDULER_ENABLED = _env("PORTAL_SCHEDULER", "1") == "1"

# ---- Web server (waitress) ----
HOST = _env("PORTAL_HOST", "0.0.0.0")
PORT = int(_env("PORTAL_PORT", "8080"))
THREADS = int(_env("PORTAL_THREADS", "8"))

# ---- Email notifications (optional; in-app notifications always work) ----
SMTP_HOST = _env("PORTAL_SMTP_HOST")             # e.g. smtp.yourcompany.com
SMTP_PORT = int(_env("PORTAL_SMTP_PORT", "25"))
SMTP_USER = _env("PORTAL_SMTP_USER")
SMTP_PASSWORD = _env("PORTAL_SMTP_PASSWORD")
MAIL_FROM = _env("PORTAL_MAIL_FROM", "kitting-portal@localhost")
NOTIFY_EMAILS = [e.strip() for e in _env("PORTAL_NOTIFY_EMAILS", "").split(",") if e.strip()]

# ---- Behaviour ----
BEHIND_PROXY = _env("PORTAL_BEHIND_PROXY", "0") == "1"  # set to 1 when IIS/Caddy sits in front
HTTPS_ONLY_COOKIES = _env("PORTAL_HTTPS", "0") == "1"   # set to 1 once behind HTTPS

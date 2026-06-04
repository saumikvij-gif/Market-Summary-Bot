"""
emailer.py
----------
Emails the daily briefing PDF via SMTP. Reads all configuration from environment
variables (loaded from .env locally, or GitHub Actions secrets in CI).

Required environment variables:
    SMTP_HOST       - e.g. smtp.gmail.com
    SMTP_USER       - the sending account / login
    SMTP_PASSWORD   - app password (NOT your normal password for Gmail)
    EMAIL_TO        - recipient address (comma-separated for multiple)

Optional:
    SMTP_PORT       - default 587 (STARTTLS)
    EMAIL_FROM      - default: SMTP_USER

If the required variables are missing, send_report() does nothing and returns
False — so email delivery is opt-in and never blocks the rest of the pipeline.
"""

import os
import smtplib
from email.message import EmailMessage

from utils import force_utf8

force_utf8()


def _config() -> dict | None:
    """Collect SMTP config from the environment, or None if not fully set."""
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    # Gmail shows app passwords as "abcd efgh ijkl mnop"; strip spaces so a
    # copy-paste with the display spaces still authenticates.
    password = (os.environ.get("SMTP_PASSWORD") or "").replace(" ", "")
    # Default the recipient to the sending account if EMAIL_TO isn't set.
    to = os.environ.get("EMAIL_TO") or user
    if not (host and user and password and to):
        return None
    # Use `or` (not get-with-default) so an empty string — which is what an
    # undefined GitHub secret expands to — falls back correctly.
    return {
        "host": host,
        "port": int(os.environ.get("SMTP_PORT") or 587),
        "user": user,
        "password": password,
        "from": os.environ.get("EMAIL_FROM") or user,
        "to": [addr.strip() for addr in to.split(",") if addr.strip()],
    }


def _send(cfg: dict, msg: EmailMessage) -> bool:
    try:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=30) as server:
            server.starttls()
            server.login(cfg["user"], cfg["password"])
            server.send_message(msg)
        print(f"📧 Emailed to {', '.join(cfg['to'])}")
        return True
    except Exception as exc:
        print(f"  ⚠️  Could not send email: {exc}")
        return False


def send_report(pdf_path: str, date_str: str) -> bool:
    """Email the briefing PDF as a downloadable attachment. Returns True if sent."""
    cfg = _config()
    if cfg is None:
        print("  ℹ️  Email not configured (SMTP_* / EMAIL_TO unset) — skipping send.")
        return False
    if not (pdf_path and os.path.exists(pdf_path)):
        print("  ⚠️  No PDF to email.")
        return False

    msg = EmailMessage()
    msg["Subject"] = f"Daily Market Summary — {date_str}"
    msg["From"] = cfg["from"]
    msg["To"] = ", ".join(cfg["to"])
    msg.set_content(
        f"Your daily market summary for {date_str} is attached as a PDF.\n\n"
        "It includes the sentiment dashboard, top gainers, top news, the market "
        "snapshot, and the trend charts."
    )
    with open(pdf_path, "rb") as f:
        msg.add_attachment(f.read(), maintype="application", subtype="pdf",
                           filename=f"Market Summary {date_str}.pdf")
    return _send(cfg, msg)

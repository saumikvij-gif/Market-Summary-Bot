"""
notify_failure.py
-----------------
Sends a short "a workflow failed" email. Deliberately uses ONLY the Python
standard library (no project dependencies) so it still works even if an earlier
step — including dependency installation — was what failed.

Run from a workflow's `if: failure()` step with the SMTP_* / EMAIL_TO env vars
and (optionally) RUN_URL and WORKFLOW_NAME set.
"""

import os
import smtplib
from email.message import EmailMessage


def main() -> None:
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    password = (os.environ.get("SMTP_PASSWORD") or "").replace(" ", "")
    to = os.environ.get("EMAIL_TO") or user
    if not (host and user and password and to):
        print("SMTP not configured; cannot send failure alert.")
        return

    workflow = os.environ.get("WORKFLOW_NAME", "Market Summary Bot")
    run_url = os.environ.get("RUN_URL", "(run URL unavailable)")

    msg = EmailMessage()
    msg["Subject"] = f"⚠️ {workflow} workflow failed"
    msg["From"] = os.environ.get("EMAIL_FROM") or user
    msg["To"] = to
    msg.set_content(
        f"A scheduled '{workflow}' run failed.\n\n"
        f"Inspect the logs here:\n{run_url}\n"
    )

    port = int(os.environ.get("SMTP_PORT") or 587)
    with smtplib.SMTP(host, port, timeout=30) as server:
        server.starttls()
        server.login(user, password)
        server.send_message(msg)
    print("Failure alert sent.")


if __name__ == "__main__":
    main()

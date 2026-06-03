"""
send_latest.py
--------------
Emails the most recently generated briefing PDF without regenerating anything.
This decouples delivery from generation: the PDF is produced right after the US
market close, but the email is sent later (e.g. at 9 AM Hong Kong time) by
scheduling this script separately.

Reads SMTP_* / EMAIL_TO from the environment (see emailer.py).

    python send_latest.py
"""

import os
import re
import sys
import glob
import datetime

from dotenv import load_dotenv
import emailer

# Load SMTP_* / EMAIL_TO from .env locally (no-op in CI, which uses secrets).
load_dotenv()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PDF_GLOB = "summaries/market_summary_*.pdf"

# Don't email a briefing older than this — if generation has been failing we'd
# otherwise silently resend stale data. Override with STALE_MAX_DAYS.
STALE_MAX_DAYS = int(os.environ.get("STALE_MAX_DAYS") or 4)


def latest_pdf_path() -> str | None:
    """Return the path of the newest dated briefing PDF, or None.

    Picks the file with the greatest YYYY-MM-DD in its name (ISO dates sort
    correctly). Falls back to the root market_summary.pdf if no dated file exists.
    """
    best_path, best_date = None, None
    for path in glob.glob(PDF_GLOB):
        m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(path))
        if m and (best_date is None or m.group(1) > best_date):
            best_date, best_path = m.group(1), path
    if best_path:
        return best_path
    return "market_summary.pdf" if os.path.exists("market_summary.pdf") else None


def summary_age_days(path: str) -> int | None:
    """Days between the date in the filename and today (UTC); None if no date."""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(path))
    if not m:
        return None
    file_date = datetime.date.fromisoformat(m.group(1))
    today = datetime.datetime.now(datetime.timezone.utc).date()
    return (today - file_date).days


def main() -> None:
    path = latest_pdf_path()
    if not path:
        print("No briefing PDF found to email.")
        sys.exit(1)

    # Stale guard: refuse to silently resend an old briefing (generation likely failing).
    age = summary_age_days(path)
    if age is not None and age > STALE_MAX_DAYS:
        print(f"Latest briefing ({path}) is {age} days old (> {STALE_MAX_DAYS}); "
              f"not emailing stale data. Check the generate workflow.")
        sys.exit(1)

    m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(path))
    date_str = (datetime.date.fromisoformat(m.group(1)).strftime("%B %d, %Y")
                if m else datetime.date.today().strftime("%B %d, %Y"))

    print(f"Emailing latest briefing: {path}")
    if emailer.send_report(path, date_str):
        return
    # Configured-but-failed (or not configured) — surface as a job failure.
    print("Email was not sent.")
    sys.exit(1)


if __name__ == "__main__":
    main()

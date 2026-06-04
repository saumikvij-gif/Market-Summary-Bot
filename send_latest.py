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
from utils import force_utf8

# Load SMTP_* / EMAIL_TO from .env locally (no-op in CI, which uses secrets).
load_dotenv()

force_utf8()

PDF_GLOB = "summaries/market_summary_*.pdf"

# Don't email a briefing older than this — if generation has been failing we'd
# otherwise silently resend stale data. Override with STALE_MAX_DAYS.
STALE_MAX_DAYS = int(os.environ.get("STALE_MAX_DAYS") or 4)

# Records the date of the last briefing we successfully emailed. Because GitHub's
# scheduled triggers are unreliable, this workflow is scheduled at several
# staggered times as a backstop; this marker makes those redundant runs
# idempotent — each distinct briefing is emailed exactly once, never duplicated.
MARKER_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           ".last_emailed")


def already_emailed(briefing_date: str) -> bool:
    """True if MARKER_FILE already records this briefing date as sent."""
    try:
        with open(MARKER_FILE, encoding="utf-8") as f:
            return f.read().strip() == briefing_date
    except FileNotFoundError:
        return False


def mark_emailed(briefing_date: str) -> None:
    """Record this briefing date as sent (committed by the workflow afterwards)."""
    with open(MARKER_FILE, "w", encoding="utf-8") as f:
        f.write(briefing_date + "\n")


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
    briefing_date = (m.group(1) if m
                     else datetime.datetime.now(datetime.timezone.utc).date().isoformat())
    date_str = datetime.date.fromisoformat(briefing_date).strftime("%B %d, %Y")

    # Idempotency guard: if a staggered backstop run already emailed this exact
    # briefing, skip (success — no duplicate email).
    if already_emailed(briefing_date):
        print(f"Briefing for {briefing_date} was already emailed — skipping "
              f"(backstop run, no duplicate).")
        return

    print(f"Emailing latest briefing: {path}")
    if emailer.send_report(path, date_str):
        mark_emailed(briefing_date)
        return
    # Configured-but-failed (or not configured) — surface as a job failure.
    print("Email was not sent.")
    sys.exit(1)


if __name__ == "__main__":
    main()

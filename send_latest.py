"""
send_latest.py
--------------
Emails the most recently generated market summary (and the trend charts) without
regenerating anything. This decouples delivery from generation: the summary is
produced right after the US market close, but the email is sent later (e.g. at
9 AM Hong Kong time) by scheduling this script separately.

Reads SMTP_* / EMAIL_TO from the environment (see emailer.py).

    python send_latest.py
"""

import os
import re
import sys
import glob

from dotenv import load_dotenv
import emailer

# Load SMTP_* / EMAIL_TO from .env locally (no-op in CI, which uses secrets).
load_dotenv()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SUMMARY_GLOB = "summaries/market_summary_*.md"
CHART_PATHS = ["charts/index_trends.png", "charts/sentiment_trend.png"]


def latest_summary_path() -> str | None:
    """Return the path of the newest dated summary file, or None.

    Picks the file with the greatest YYYY-MM-DD in its name (ISO dates sort
    correctly), ignoring any older run-id-style filenames. Falls back to the
    root market_summary.md if no dated files exist.
    """
    best_path, best_date = None, None
    for path in glob.glob(SUMMARY_GLOB):
        m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(path))
        if m and (best_date is None or m.group(1) > best_date):
            best_date, best_path = m.group(1), path
    if best_path:
        return best_path
    return "market_summary.md" if os.path.exists("market_summary.md") else None


def main() -> None:
    path = latest_summary_path()
    if not path:
        print("No summary file found to email.")
        sys.exit(1)

    print(f"Emailing latest summary: {path}")
    with open(path, encoding="utf-8") as f:
        summary = f.read()

    charts = [p for p in CHART_PATHS if os.path.exists(p)]
    if emailer.send_summary(summary, charts):
        return
    # Configured-but-failed (or not configured) — surface as a job failure.
    print("Email was not sent.")
    sys.exit(1)


if __name__ == "__main__":
    main()

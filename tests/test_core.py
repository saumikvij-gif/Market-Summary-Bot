import datetime

import market_summary as ms
import send_latest


# ── Formatting (the FX sign / -0.0 fix) ────────────────────────────────────────

def test_format_section_no_double_sign():
    data = {"EUR/USD": {"price": 1.17, "change": 0.0, "pct_change": -0.04}}
    line = ms.format_section("FX", data).splitlines()[1]
    assert "+-" not in line          # the bug we fixed
    assert "(-0.04%)" in line


def test_format_section_positive():
    data = {"Y": {"price": 100.0, "change": 2.5, "pct_change": 2.5}}
    line = ms.format_section("T", data).splitlines()[1]
    assert "+2.50" in line and "(+2.50%)" in line


def test_arrow():
    assert ms.arrow(1.0) == "▲"
    assert ms.arrow(-1.0) == "▼"


def test_latest_session_date():
    md = {"indices": {"S&P 500": {"session_date": "2026-06-03"}}}
    assert ms.latest_session_date(md) == "2026-06-03"
    assert ms.latest_session_date({"indices": {}}) is None


# ── Stale-email guard ──────────────────────────────────────────────────────────

def test_summary_age_days():
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    assert send_latest.summary_age_days(f"summaries/market_summary_{today}.md") == 0
    assert send_latest.summary_age_days("summaries/market_summary_2000-01-01.md") > 1000
    assert send_latest.summary_age_days("summaries/no_date.md") is None

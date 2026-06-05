"""
market_summary.py
-----------------
Fetches market data from Yahoo Finance and generates a concise
market summary using the Anthropic Claude API.

Usage:
    python market_summary.py

Environment variables required:
    ANTHROPIC_API_KEY  - Your Anthropic API key

Optional:
    OUTPUT_FILE        - Base path for output; the PDF is derived from its stem
                         (default: market_summary.pdf)
"""

import os
import datetime
import anthropic
import yfinance as yf
from dotenv import load_dotenv

from utils import retry, force_utf8

# Project modules. These are all hard dependencies (pinned in requirements.txt);
# each pipeline stage in main() still wraps its *work* in try/except so one
# failing source (a feed, the DB, charts, email) never sinks the whole briefing.
import news_feeds
import sentiment
import sector_watch
import history
import charts
import pdf_report as report_module
import emailer

# Load environment variables from a local .env file if present (no-op in CI)
load_dotenv()

force_utf8()

# ── Configuration ─────────────────────────────────────────────────────────────

# Tickers to track — customise freely
INDICES = {
    "S&P 500":       "^GSPC",
    "Nasdaq 100":    "^NDX",
    "Dow Jones":     "^DJI",
    "Russell 2000":  "^RUT",
    "VIX":           "^VIX",
}

STOCKS = {
    "Nvidia":    "NVDA",
    "Microsoft": "MSFT",
    "AMD":       "AMD",
    "Meta":      "META",
    "Alphabet":  "GOOG",
    "Broadcom":  "AVGO",
    "Micron":    "MU",
    "Intel":     "INTC",   # INTL → INTC (Intel)
    "SanDisk":   "SNDK",
    "Coherent":  "COHR",
    "Amazon":    "AMZN",
    "Apple":     "AAPL",
    "Arm":       "ARM",
}

COMMODITIES = {
    "Gold":        "GC=F",
    "Crude Oil":   "CL=F",
    "Bitcoin":     "BTC-USD",
}

FX = {
    "EUR/USD": "EURUSD=X",
    "USD/JPY": "JPY=X",
    "GBP/USD": "GBPUSD=X",
}

# The 11 SPDR sector ETFs — used to measure market breadth (how broadly sectors
# are participating), a key input to the enriched market sentiment component.
SECTORS = {
    "Technology":        "XLK",
    "Financials":        "XLF",
    "Health Care":       "XLV",
    "Energy":            "XLE",
    "Consumer Disc.":    "XLY",
    "Consumer Staples":  "XLP",
    "Industrials":       "XLI",
    "Utilities":         "XLU",
    "Materials":         "XLB",
    "Real Estate":       "XLRE",
    "Communication Svc": "XLC",
}

# Interest-rate signals. The 10Y captures financial conditions (market
# component); the 13-week T-Bill tracks Fed-policy expectations and powers the
# Fed score. We use ^IRX (13-week bill) rather than a 2Y because Yahoo has no
# reliable free 2Y yield — the old 2YY=F future returns values frozen for days,
# which silently faked a flat signal. ^IRX is the front of the curve: live,
# liquid, and the purest policy-path read (see the Fed sub-model in sentiment.py).
RATES = {
    "10Y Treasury Yield":  "^TNX",
    "13-Week T-Bill Yield": "^IRX",
}

# Base path for the run's output; the PDF briefing is derived from its stem.
OUTPUT_FILE = os.environ.get("OUTPUT_FILE", "market_summary.pdf")


# ── Data fetching ──────────────────────────────────────────────────────────────

def _session_date(info: dict) -> str:
    """Session date from Yahoo's last-trade time (regularMarketTime, a UTC epoch),
    or today (UTC) as a fallback. Used only for holiday/stale detection."""
    ts = info.get("regularMarketTime")
    try:
        if ts:
            return datetime.datetime.fromtimestamp(
                int(ts), datetime.timezone.utc).date().isoformat()
    except Exception:
        pass
    return datetime.datetime.now(datetime.timezone.utc).date().isoformat()


def fetch_quote(ticker_symbol: str) -> dict:
    """price, change, pct_change, and session_date for one ticker.

    price/change/% are taken STRAIGHT from Yahoo's own quote fields
    (info.regularMarketPrice / regularMarketChange / regularMarketChangePercent) —
    the exact, pre-computed numbers the website shows. We don't recompute the
    change ourselves, because that would depend on a `previousClose` that yfinance
    sometimes reports stale/wrong (fast_info gave Apple a 313.97 prev close that
    wasn't even a real recent close). Falls back to the daily-history bar (NaN-safe
    via dropna, change computed locally) only if the quote is unavailable.

    `change` is stored at 4dp so a yield ticker's (^IRX/^TNX) sub-basis-point move
    survives for the Fed leg; every display formats it :+.2f. `or 0.0` normalizes
    a falsy -0.0 so it never renders as "+-0.00".
    """
    ticker = yf.Ticker(ticker_symbol)
    # yfinance is unofficial and rate-limits; retry transient failures.
    try:
        info = retry(lambda: ticker.info, attempts=2, label=ticker_symbol) or {}
    except Exception:
        info = {}

    price = info.get("regularMarketPrice")
    change = info.get("regularMarketChange")
    pct = info.get("regularMarketChangePercent")
    if price is not None and change is not None and pct is not None:
        return {
            "price":        round(float(price), 2),
            "change":       round(float(change), 4) or 0.0,
            "pct_change":   round(float(pct), 2) or 0.0,
            "session_date": _session_date(info),
        }

    # Fallback: the daily history bar (NaN-safe via dropna), change computed here.
    hist = retry(lambda: ticker.history(period="5d"),
                 attempts=3, label=ticker_symbol)
    closes = hist["Close"].dropna() if not hist.empty else None
    if closes is None or closes.empty:
        return {"error": f"No data for {ticker_symbol}"}
    last = float(closes.iloc[-1])
    prev = float(closes.iloc[-2]) if len(closes) >= 2 else last
    change = last - prev
    return {
        "price":        round(last, 2),
        "change":       round(change, 4) or 0.0,
        "pct_change":   round((change / prev * 100) if prev else 0.0, 2) or 0.0,
        "session_date": closes.index[-1].date().isoformat(),
    }


def latest_session_date(market_data: dict) -> str | None:
    """The session date of the S&P 500 quote (canonical 'data date'), or None."""
    sp = market_data.get("indices", {}).get("S&P 500", {})
    return sp.get("session_date") if isinstance(sp, dict) else None


def fetch_top_gainers(count: int = 5) -> list:
    """Return the market's top daily gainers via Yahoo's screener, or [].

    Each item: {symbol, name, price, pct_change}. Fail-safe — returns [] on any
    error so a screener hiccup never blocks the summary.
    """
    try:
        result = retry(lambda: yf.screen("day_gainers", count=count),
                       attempts=2, label="day_gainers")
        quotes = result.get("quotes", []) if isinstance(result, dict) else []
        gainers = []
        for q in quotes[:count]:
            gainers.append({
                "symbol": q.get("symbol"),
                "name": (q.get("shortName") or q.get("symbol") or "").strip(),
                "price": q.get("regularMarketPrice"),
                "pct_change": q.get("regularMarketChangePercent"),
            })
        return gainers
    except Exception as exc:
        print(f"  ⚠️  Could not fetch top gainers: {exc}")
        return []


def format_top_gainers(gainers: list) -> str:
    """Render the top gainers as a markdown section, or '' if none."""
    if not gainers:
        return ""
    lines = ["### Top Gainers (Market)"]
    for g in gainers:
        price = f"{g['price']:,.2f}" if g.get("price") is not None else "n/a"
        pct = g.get("pct_change")
        pct_str = f"▲ {pct:+.2f}%" if pct is not None else ""
        lines.append(f"- {g['name']} ({g['symbol']}): {price}  {pct_str}")
    return "\n".join(lines)


def fetch_all_data() -> dict:
    """Fetch quotes for every configured instrument."""
    sections = {
        "indices":    INDICES,
        "stocks":     STOCKS,
        "sectors":    SECTORS,
        "commodities":COMMODITIES,
        "fx":         FX,
        "rates":      RATES,
    }
    result = {}
    for section, tickers in sections.items():
        result[section] = {}
        for name, symbol in tickers.items():
            print(f"  Fetching {name} ({symbol})…")
            result[section][name] = fetch_quote(symbol)
    return result


# ── Formatting helpers ─────────────────────────────────────────────────────────

def arrow(pct: float) -> str:
    return "▲" if pct >= 0 else "▼"


def format_section(title: str, data: dict) -> str:
    lines = [f"### {title}"]
    for name, q in data.items():
        if "error" in q:
            lines.append(f"- {name}: N/A")
        else:
            # `:+` gives each value its own correct sign, so a near-zero change
            # paired with a negative percent can't render as "+-0.04%".
            lines.append(
                f"- {name}: {q['price']:,.2f}  "
                f"{arrow(q['pct_change'])} {q['change']:+,.2f} "
                f"({q['pct_change']:+.2f}%)"
            )
    return "\n".join(lines)


def build_data_block(market_data: dict) -> str:
    today = datetime.date.today().strftime("%B %d, %Y")
    parts = [f"## Market Data — {today}\n"]
    # Note: "sectors" (SPDR ETFs) is intentionally omitted from display — it's
    # still fetched to power the composite breadth signal, just not shown.
    label_map = {
        "indices":    "Major Indices",
        "stocks":     "Key Stocks",
        "commodities":"Commodities & Crypto",
        "fx":         "FX Rates",
        "rates":      "Interest Rates",
    }
    for key, label in label_map.items():
        section = market_data.get(key)
        if section:
            parts.append(format_section(label, section))
            parts.append("")
    return "\n".join(parts)


# ── Claude summary ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a professional financial analyst writing a concise daily market summary.
Your summaries are clear, insightful, and suitable for a general but financially
literate audience. Highlight notable moves, potential drivers, and any interesting
cross-asset relationships. Keep the tone neutral and factual. Use markdown.

Do NOT include a top-level "# " title or document heading — start directly with
the first section (use "## " subheadings). A title is added separately.
"""

# Tool schema that forces Claude to return the summary as machine-readable text.
# (Sentiment is computed separately and reproducibly in sentiment.py, so Claude
# only writes the narrative.)
REPORT_TOOL = {
    "name": "submit_market_report",
    "description": "Submit the daily market summary prose.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary_markdown": {
                "type": "string",
                "description": (
                    "The market summary in markdown. Use '## ' subheadings, no "
                    "top-level '# ' title. Do NOT include a sentiment section — "
                    "that is rendered separately."
                ),
            },
        },
        "required": ["summary_markdown"],
    },
}


def generate_report(data_block: str, news_block: str = "") -> dict:
    """Send market data (and optional news) to Claude; return {'summary_markdown': ...}."""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    user_message = (
        "Here is today's market data, including the market's top daily gainers. "
        "Write a concise market summary (around 300–400 words) covering the key "
        "themes, notable movers (mention any standout top gainers), and any "
        "cross-asset signals worth highlighting.\n\n"
        + data_block
    )

    if news_block.strip():
        user_message += (
            "\n\nHere are today's financial news headlines and Reddit "
            "finance-community post titles. Use them to explain the price "
            "action, and note that Reddit communities use sarcasm and slang — "
            "interpret tone accordingly. If there are any Federal Reserve / "
            "monetary policy or interest-rate developments, call them out "
            "explicitly in the summary, as they are especially important.\n\n"
            + news_block
        )

    message = retry(lambda: client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1500,
        system=SYSTEM_PROMPT,
        tools=[REPORT_TOOL],
        tool_choice={"type": "tool", "name": "submit_market_report"},
        messages=[{"role": "user", "content": user_message}],
    ), attempts=3, label="Claude API")

    for block in message.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError("Claude did not return a structured report.")


# ── Output ─────────────────────────────────────────────────────────────────────

def print_to_console(data_block: str, summary: str) -> None:
    today = datetime.date.today().strftime("%B %d, %Y")
    divider = "─" * 60
    print(f"\n{divider}")
    print(f"  Daily Market Summary — {today}")
    print(divider)
    print(summary)
    print(f"\n{divider}")
    print(data_block)


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise EnvironmentError(
            "ANTHROPIC_API_KEY is not set. "
            "Export it or add it to your GitHub Actions secrets."
        )

    print("Fetching market data…")
    market_data = fetch_all_data()

    # Detect market holidays / stale data: if the latest session isn't today,
    # the US market didn't trade (holiday/weekend) and figures are last close.
    session_date = latest_session_date(market_data)
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    is_fresh = (session_date == today)
    if session_date and not is_fresh:
        print(f"  ⚠️  No new US session for {today}; latest data is {session_date} "
              f"(market holiday or weekend).")

    print("\nBuilding data summary…")
    data_block = build_data_block(market_data)

    print("\nFetching top market gainers…")
    gainers = fetch_top_gainers(5)
    gainers_block = format_top_gainers(gainers)
    if gainers_block:
        data_block += "\n" + gainers_block + "\n"

    # Pull current news/Reddit/Fed headlines as {source: [titles]}. A wide
    # per-source limit gives the sentiment scorer a broader, more representative
    # sample — affordable now that score_text is cached. Fail-safe: a news
    # outage yields {} and never blocks the rest of the run.
    print("\nFetching financial news headlines…")
    try:
        headlines = news_feeds.gather_headlines(limit=20)
    except Exception as exc:
        print(f"  ⚠️  Could not fetch news headlines: {exc}")
        headlines = {}
    news_block = ""
    try:
        news_block = news_feeds.build_headline_block(headlines)
    except Exception:
        pass

    # Top news with summaries, for the briefing's Top News section.
    top_news = []
    try:
        top_news = news_feeds.get_top_news(5)
    except Exception as exc:
        print(f"  ⚠️  Could not fetch top news: {exc}")

    # AI-stack Sector Watch: per-basket price move + news/Reddit sentiment.
    # Display-only — does NOT feed the composite score.
    print("\nBuilding AI-stack sector watch…")
    watch_rows = []   # NB: keep this name distinct from the `sector_watch` module
    try:
        _, reddit_titles, _ = news_feeds.split_headlines(headlines)
        idx = market_data.get("indices", {})
        sp_move = (idx.get("S&P 500", {}) or {}).get("pct_change")
        nasdaq_move = (idx.get("Nasdaq 100", {}) or {}).get("pct_change")
        # Tech baskets are measured vs the Nasdaq, the rest vs the S&P (see module).
        watch_rows = sector_watch.build_sector_watch(reddit_titles, sp_move, nasdaq_move)
        watch_block = sector_watch.render_md(watch_rows)
        if watch_block:
            data_block += "\n" + watch_block + "\n"
    except Exception as exc:
        print(f"  ⚠️  Could not build sector watch: {exc}")

    # Compute the quantitative sentiment dashboard (reproducible, NLP-based).
    # This is the score of record — it drives the DB and the daily chart.
    print("\nComputing quantitative sentiment…")
    dashboard = None
    try:
        # Recent raw composite scores (stored as -100..100), oldest-first and
        # excluding today, so the dashboard can compute its EMA trend line over
        # prior days + today. A short pull window is enough for a short EMA.
        prior_scores = [
            r["score"] / 100.0
            for r in history.sentiment_history(limit=sentiment.SMOOTHING_SPAN * 4)
            if r["score"] is not None and r["run_date"] != (session_date or today)
        ]
        dashboard = sentiment.build_dashboard(market_data, headlines,
                                              prior_scores=prior_scores)
        print(f"  Sentiment: {dashboard['overall_score']:+.2f} ({dashboard['label']})"
              f"  |  trend {dashboard['smoothed_score']:+.2f} "
              f"({dashboard['smoothed_label']})")
    except Exception as exc:
        print(f"  ⚠️  Could not compute sentiment dashboard: {exc}")

    # AI narrative is best-effort: if Claude is unavailable, still ship the
    # data + quant score rather than failing the whole run.
    print("\nGenerating AI summary with Claude…")
    report = None
    try:
        report = generate_report(data_block, news_block)
    except Exception as exc:
        print(f"  ⚠️  Claude summary failed: {exc}; continuing with data + score only.")

    # Assemble the document: Claude's prose (if available) + the quant dashboard.
    if report is not None:
        summary = report["summary_markdown"].rstrip()
    else:
        summary = ("## Market Summary\n\n_The AI narrative was unavailable today; "
                   "market data and the quantitative sentiment score are shown below._")
    if session_date and not is_fresh:
        summary = (f"> **Note:** No US trading session on {today}. Figures are from "
                   f"the last session ({session_date}).\n\n") + summary
    if dashboard is not None:
        summary += "\n\n" + sentiment.render_dashboard_md(dashboard)

    print_to_console(data_block, summary)

    # Record this run for historical trend tracking (never block on DB errors).
    # Key by the actual session date so holidays don't create duplicate/today
    # rows; store the quant score (scaled to -100..100 to match the chart axis).
    db_date = session_date or today
    try:
        if dashboard is not None:
            history.save_run(
                market_data, summary, run_date=db_date,
                sentiment=dashboard["label"],
                score=int(round(dashboard["overall_score"] * 100)),
            )
        else:
            history.save_run(market_data, summary, run_date=db_date)
        print(f"📊 Run recorded to {os.path.basename(history.SUMMARIES_CSV)}")
    except Exception as exc:
        print(f"  ⚠️  Could not record run to database: {exc}")

    # Generate trend charts from the accumulated history (never block on errors).
    chart_paths = []
    try:
        chart_paths = charts.generate_all()
    except Exception as exc:
        print(f"  ⚠️  Could not generate charts: {exc}")

    # Build the downloadable PDF briefing (full report in one file).
    pdf_path = os.path.splitext(OUTPUT_FILE)[0] + ".pdf"
    today_pretty = datetime.date.today().strftime("%B %d, %Y")
    stale_note = ("" if (is_fresh or not session_date) else
                  f"No US trading session on {today}. Figures are from the last "
                  f"session ({session_date}).")
    try:
        prose = report.get("summary_markdown", "") if report else ""
        html = report_module.build_html(
            today_pretty, prose, gainers, top_news, dashboard or {},
            market_data, chart_paths, stale_note=stale_note,
            sector_watch=watch_rows)
        if report_module.write_pdf(html, pdf_path):
            print(f"📄 PDF briefing written to {pdf_path}")
        else:
            pdf_path = None
            print("  ⚠️  PDF generation reported an error.")
    except Exception as exc:
        pdf_path = None
        print(f"  ⚠️  Could not build PDF: {exc}")

    # Email the PDF as a downloadable attachment (opt-in, fail-safe).
    try:
        emailer.send_report(pdf_path, today_pretty)
    except Exception as exc:
        print(f"  ⚠️  Could not send email: {exc}")


if __name__ == "__main__":
    main()

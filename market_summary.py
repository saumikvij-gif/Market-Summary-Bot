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
    OUTPUT_FILE        - Path to save the summary (default: market_summary.md)
"""

import os
import sys
import json
import datetime
import anthropic
import yfinance as yf
from dotenv import load_dotenv

# Load environment variables from a local .env file if present (no-op in CI)
load_dotenv()

# Ensure UTF-8 console output so symbols like ▲/▼ don't crash on Windows (cp1252)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

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
    "Apple":    "AAPL",
    "Microsoft":"MSFT",
    "Nvidia":   "NVDA",
    "Amazon":   "AMZN",
    "Alphabet": "GOOGL",
    "Tesla":    "TSLA",
    "Meta":     "META",
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

OUTPUT_FILE = os.environ.get("OUTPUT_FILE", "market_summary.md")


# ── Data fetching ──────────────────────────────────────────────────────────────

def fetch_quote(ticker_symbol: str) -> dict:
    """Return a dict with price, change, and pct_change for one ticker."""
    ticker = yf.Ticker(ticker_symbol)
    hist = ticker.history(period="2d")

    if hist.empty or len(hist) < 1:
        return {"error": f"No data for {ticker_symbol}"}

    latest_close  = hist["Close"].iloc[-1]
    prev_close    = hist["Close"].iloc[-2] if len(hist) >= 2 else latest_close
    change        = latest_close - prev_close
    pct_change    = (change / prev_close * 100) if prev_close else 0.0

    return {
        "price":      round(float(latest_close),  2),
        # `or 0.0` normalizes -0.0 (falsy) to 0.0 so tiny negatives don't render as "+-0.00"
        "change":     round(float(change),         2) or 0.0,
        "pct_change": round(float(pct_change),     2) or 0.0,
    }


def fetch_all_data() -> dict:
    """Fetch quotes for every configured instrument."""
    sections = {
        "indices":    INDICES,
        "stocks":     STOCKS,
        "commodities":COMMODITIES,
        "fx":         FX,
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
            sign  = "+" if q["change"] >= 0 else ""
            lines.append(
                f"- {name}: {q['price']:,.2f}  "
                f"{arrow(q['pct_change'])} {sign}{q['change']:,.2f} "
                f"({sign}{q['pct_change']:.2f}%)"
            )
    return "\n".join(lines)


def build_data_block(market_data: dict) -> str:
    today = datetime.date.today().strftime("%B %d, %Y")
    parts = [f"## Market Data — {today}\n"]
    label_map = {
        "indices":    "Major Indices",
        "stocks":     "Key Stocks",
        "commodities":"Commodities & Crypto",
        "fx":         "FX Rates",
    }
    for key, label in label_map.items():
        parts.append(format_section(label, market_data[key]))
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

def fetch_news_block() -> str:
    """Pull current financial news/Reddit headlines as a text block.

    Imports the headline gatherer from reddit_news. Returns an empty string if
    anything goes wrong, so a news outage never blocks the market summary.
    """
    try:
        import reddit_news
        # Keep it light — a few headlines per source is enough context.
        headlines = reddit_news.gather_headlines(limit=4)
        return reddit_news.build_headline_block(headlines)
    except Exception as exc:
        print(f"  ⚠️  Could not fetch news headlines: {exc}")
        return ""


def generate_summary(data_block: str, news_block: str = "") -> str:
    """Send the formatted market data (and optional news) to Claude."""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    user_message = (
        "Here is today's market data. Please write a concise market summary "
        "(around 300–400 words) covering the key themes, notable movers, and "
        "any cross-asset signals worth highlighting.\n\n"
        + data_block
    )

    if news_block.strip():
        user_message += (
            "\n\nHere are today's financial news headlines and Reddit "
            "finance-community post titles. Use them to explain the price "
            "action, and note that Reddit communities use sarcasm and slang — "
            "interpret tone accordingly. If there are any Federal Reserve / "
            "monetary policy or interest-rate developments, call them out "
            "explicitly, as they are especially important for markets.\n\n"
            "After the main summary, ADD a dedicated section titled "
            "'## News & Sentiment' that states an overall market sentiment "
            "(Bullish, Bearish, or Neutral) with a confidence level, lists 3–5 "
            "key themes from the news, and names the specific headlines driving "
            "your read.\n\n"
            + news_block
        )

    message = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    return message.content[0].text


# ── Output ─────────────────────────────────────────────────────────────────────

def save_output(data_block: str, summary: str) -> None:
    today = datetime.date.today().strftime("%B %d, %Y")
    content = f"# Daily Market Summary — {today}\n\n{summary}\n\n---\n\n{data_block}"
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"\n✅ Summary saved to {OUTPUT_FILE}")


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

    print("\nBuilding data summary…")
    data_block = build_data_block(market_data)

    print("\nFetching financial news headlines…")
    news_block = fetch_news_block()

    print("\nGenerating AI summary with Claude…")
    summary = generate_summary(data_block, news_block)

    print_to_console(data_block, summary)
    save_output(data_block, summary)


if __name__ == "__main__":
    main()

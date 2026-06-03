# Market Summary Bot

Fetches market data from Yahoo Finance and generates a concise daily market
summary using the Anthropic Claude API. Can run locally or automatically every
weekday via GitHub Actions.

## What it does

1. Pulls latest quotes for major indices, key stocks, commodities/crypto, and FX
   pairs via [`yfinance`](https://pypi.org/project/yfinance/).
2. Formats the data into a markdown block.
3. Sends it to Claude, which writes a ~300–400 word analyst-style summary.
4. Prints the result to the console and saves it to a markdown file.

## Setup

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...   # PowerShell: $env:ANTHROPIC_API_KEY="sk-ant-..."
python market_summary.py
```

### Environment variables

| Variable            | Required | Default             | Description                          |
| ------------------- | -------- | ------------------- | ------------------------------------ |
| `ANTHROPIC_API_KEY` | Yes      | —                   | Your Anthropic API key               |
| `OUTPUT_FILE`       | No       | `market_summary.pdf`| Base path for output; the PDF briefing is derived from its stem |

## Automated runs (GitHub Actions)

The workflow in [`.github/workflows/market_summary.yml`](.github/workflows/market_summary.yml)
runs Mon–Fri at 21:30 UTC (after NYSE close), generates a summary into
`summaries/`, and commits it back to the repo. It can also be triggered manually
from the **Actions** tab (`workflow_dispatch`).

**Required secret:** add `ANTHROPIC_API_KEY` under
**Settings → Secrets and variables → Actions → New repository secret**.

## Customising tickers

Edit the `INDICES`, `STOCKS`, `COMMODITIES`, and `FX` dictionaries at the top of
[`market_summary.py`](market_summary.py).

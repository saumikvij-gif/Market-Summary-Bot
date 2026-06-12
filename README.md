# Market Summary Bot

An automated daily US-equity market briefing. Each trading day it gathers market
data and financial news, computes a reproducible quantitative sentiment score,
writes an analyst-style narrative with the Anthropic Claude API, charts the
trends, renders a PDF, and emails it. Runs locally or fully automated via GitHub
Actions.

See [`Pipeline_Overview.md`](Pipeline_Overview.md) for the end-to-end design.

## What it does

1. Pulls quotes for indices, key stocks, sector ETFs, commodities/crypto, FX, and
   rates via [`yfinance`](https://pypi.org/project/yfinance/).
2. Fetches news/Reddit/Fed headlines from public RSS feeds, filtered for
   US-market relevance and recency.
3. Computes a deterministic **sentiment score** (−1…+1) — a weighted composite of
   market data, news NLP, and Fed-rate expectations (see Pipeline_Overview.md §4).
   FinBERT scores formal text, VADER scores social text.
4. Builds a display-only **Sector Watch** of 8 AI-stack thesis baskets, scored by
   the session move (with breadth + news for texture) so the label describes how
   each sector traded; relative strength vs the Nasdaq 100 / S&P 500 is shown as
   context.
5. Builds a display-only **Positioning & Regime** read per basket — OTM put/call
   volume (daily), short %-of-float as a squeeze-fuel level (biweekly exchange
   data), and the price state, combined via the classic agreement/divergence
   playbook (bearish-confirmed / squeeze setup / complacent / hedged rally).
   Under evaluation: logged daily to `history_options.csv` and drives no scores.
6. Asks Claude for a ~300–400 word analyst narrative grounded in the data + news.
7. Records the run to CSV history, regenerates trend charts, and builds a PDF.
8. Emails the PDF (separate, decoupled delivery stage).

## Setup

```powershell
pip install -r requirements.txt
$env:ANTHROPIC_API_KEY = "sk-ant-..."   # bash: export ANTHROPIC_API_KEY=sk-ant-...
python market_summary.py
```

FinBERT is optional and heavier (torch + transformers). Without it the bot falls
back to VADER automatically, so a default install stays lightweight. To enable it:

```powershell
pip install -r requirements-ml.txt
$env:SENTIMENT_ENGINE = "hybrid"        # FinBERT for news/Fed, VADER for social
```

### Environment variables

| Variable            | Required | Default               | Description                                       |
| ------------------- | -------- | --------------------- | ------------------------------------------------- |
| `ANTHROPIC_API_KEY` | Yes      | —                     | Anthropic API key for the narrative               |
| `OUTPUT_FILE`       | No       | `market_summary.pdf`  | Base path for output; the PDF is derived from its stem |
| `SENTIMENT_ENGINE`  | No       | `hybrid`              | `hybrid` (FinBERT+VADER) or `vader` (lightweight) |
| `NEWS_LIMIT`        | No       | `8`                   | Max headlines kept per feed                       |
| `NEWS_MAX_AGE_HOURS`| No       | `24`                  | Drop headlines older than this (set `48` to span a weekend/holiday gap) |
| `SMTP_HOST` / `SMTP_USER` / `SMTP_PASSWORD` / `EMAIL_TO` | For email | — | SMTP delivery config (see [`emailer.py`](emailer.py)); unset → email is skipped |
| `SMTP_PORT` / `EMAIL_FROM` | No | `587` / `SMTP_USER` | Optional SMTP overrides                           |

## Automated runs (GitHub Actions)

Two decoupled workflows so data is captured at the US close but the briefing
lands at the start of the Hong Kong business day:

- [`market_summary.yml`](.github/workflows/market_summary.yml) — **Generate**:
  fired ~21:30 UTC after the NYSE close (on-time via an external cron-job.com
  dispatch; GitHub's native `30 21 * * 1-5` cron is kept as a delayed fallback).
  Produces the summary + PDF (named by the data's **session date**), updates the
  CSV history, regenerates charts, and commits them.
- [`email_summary.yml`](.github/workflows/email_summary.yml) — **Deliver**:
  fired once a day by the external cron-job.com dispatch (GitHub's native cron was
  removed — it ran hours late). Emails the latest committed briefing exactly once
  via [`send_latest.py`](send_latest.py).

**Required secret:** `ANTHROPIC_API_KEY` (Settings → Secrets and variables →
Actions). For email, add the `SMTP_*` / `EMAIL_TO` secrets too.

## Storage & history

History is two committed CSV files — [`history_quotes.csv`](history_quotes.csv)
(one row per date × instrument) and [`history_summaries.csv`](history_summaries.csv)
(one row per date) — read in memory by [`history.py`](history.py). There is no
SQLite. Re-running a date overwrites it, so the history stays clean. Backfill real
historical prices with `python history.py --backfill`.

## Customising tickers

Edit `INDICES`, `STOCKS`, `SECTORS`, `COMMODITIES`, `FX`, and `RATES` at the top of
[`market_summary.py`](market_summary.py). The AI-stack baskets for Sector Watch
live in `SECTOR_BASKETS` in [`sector_watch.py`](sector_watch.py).

## Tests

```powershell
python -m pytest -q
```

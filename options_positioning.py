"""
options_positioning.py
----------------------
Positioning & Regime read per Sector-Watch basket: three legs combined into a
classic positioning playbook, because no single indicator is reliable alone.

  1. OTM put/call volume (Yahoo option chains, front two expiries) — DAILY.
     Out-of-the-money only: ITM flow is stock-replacement mechanics, OTM is
     directional bets/hedges. P/C > 1 = puts bid (fear), < 1 = calls (chasing).
     yfinance's openInterest/impliedVol fields are junk (verified) — volume only.
  2. Short % of float + its month-over-month change (Yahoo info) — biweekly
     exchange data, used only as a LEVEL ("how loaded is the squeeze fuel"),
     the one use its staleness honestly permits.
  3. Price state from daily history — falling / stabilizing / rising / at highs.

The bottom line per basket is a simple call — was TODAY bullish or bearish —
with direction taken from the day's move (price IS the day, same convention as
the Sector Watch) and the positioning legs explaining the session's CHARACTER:

    up day, shorts/puts loaded into a selloff  → Bullish — short-squeeze rebound
    up day, puts still heavily bid             → Bullish — but heavily hedged
    up day, no hedges/shorts, at highs         → Bullish — complacent advance
    down day, puts bid + shorts loaded/rising  → Bearish — conviction selling
    down day, no hedging demand                → Bearish — flows not panicked

STATUS: UNDER EVALUATION. Options flow has no free history anywhere, so this is
displayed and logged daily (history_options.csv) and gets judged on its own
accumulated record. DISPLAY-ONLY — feeds no scores. Everything is fail-safe.
"""

from concurrent.futures import ThreadPoolExecutor

# ── Leg 1: OTM put/call ────────────────────────────────────────────────────────
OTM_MIN = 1.02        # strikes ≥2% from spot count as OTM (directional flow)
EXPIRIES = 2          # front expiries pooled — the active trading flow
WORKERS = 8
PUT_HEAVY = 1.10      # single-stock flow skews call-heavy, so "balanced" centres
CALL_HEAVY = 0.70     # below 1.0
PC_VHIGH = 2.00       # extreme pessimism gate for the contrarian regime

# ── Leg 2: short interest (level + m/m drift) ──────────────────────────────────
SI_VHIGH, SI_HIGH, SI_LOW = 0.05, 0.03, 0.02   # short % of float tiers
SI_RISING = 1.10                                # shares-short m/m ratio

# ── Leg 3: price state ─────────────────────────────────────────────────────────
R5_FALL, R5_RISE = -2.5, 2.5    # 5-day basket move (%) gates
DRAWDOWN_21D = -6.0             # 21-day move that counts as a real selloff
PRE_BOUNCE_SLIDE = -5.0         # week-ex-today this red also counts as a selloff
NEAR_HIGH = 0.97                # within 3% of the 60-day high
BOUNCE_DAY = 1.5                # an up-day this big inside a selloff = bouncing


def _per_ticker(sym):
    """(otm_put_volume, otm_call_volume) for one name, or None on any failure."""
    try:
        import yfinance as yf
        t = yf.Ticker(sym)
        spot = float(t.fast_info["lastPrice"])
        if not spot:
            return None
        pv = cv = 0.0
        for exp in t.options[:EXPIRIES]:
            ch = t.option_chain(exp)
            puts, calls = ch.puts, ch.calls
            pv += float(puts[puts["strike"] <= spot / OTM_MIN]["volume"].fillna(0).sum())
            cv += float(calls[calls["strike"] >= spot * OTM_MIN]["volume"].fillna(0).sum())
        return pv, cv
    except Exception:
        return None


def _short_info(sym):
    """(short_pct_of_float, shares_short_m/m_ratio) — either part may be None."""
    try:
        import yfinance as yf
        info = yf.Ticker(sym).info
        spf = info.get("shortPercentOfFloat")
        cur, prev = info.get("sharesShort"), info.get("sharesShortPriorMonth")
        return spf, (cur / prev) if (cur and prev) else None
    except Exception:
        return None, None


def _price_metrics(tickers):
    """{sym: (r1, r5, r21, r3, proximity_to_60d_high)} from 6 months of history."""
    out = {}
    try:
        import yfinance as yf
        data = yf.download(tickers, period="6mo", progress=False,
                           group_by="ticker", auto_adjust=False)
    except Exception:
        return out
    for t in tickers:
        try:
            c = data[t]["Close"].dropna()
            if len(c) < 22:
                continue
            last = float(c.iloc[-1])
            out[t] = ((last / float(c.iloc[-2]) - 1) * 100,
                      (last / float(c.iloc[-6]) - 1) * 100,
                      (last / float(c.iloc[-22]) - 1) * 100,
                      (last / float(c.iloc[-4]) - 1) * 100,
                      last / float(c.tail(60).max()))
        except Exception:
            pass
    return out


def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    n = len(xs)
    if not n:
        return None
    mid = n // 2
    return xs[mid] if n % 2 else (xs[mid - 1] + xs[mid]) / 2


def flow_read(pc) -> str:
    """Human label for an OTM put/call ratio."""
    if pc is None:
        return "n/a"
    if pc >= PUT_HEAVY:
        return "put-heavy (hedging)"
    if pc <= CALL_HEAVY:
        return "call-heavy (bullish bets)"
    return "balanced"


def price_state(r1, r5, r21, r3, prox) -> str:
    """Classify the basket's price action.

    The drawdown checks come BEFORE the 5-day "falling" gate on purpose: a
    basket deep in a selloff that rips +N% TODAY is "bouncing", not "falling",
    even while its 5-day return is still red — otherwise a live short squeeze
    gets stamped bearish (and visibly contradicts the same-day Sector Watch).
    """
    if r5 is None:
        return "n/a"
    # "In a selloff" = a deep 21-day drawdown OR a brutal week even before
    # today's move (r5 - r1 ≈ the 4 prior sessions) — the latter catches a crash
    # so fresh that a pre-crash run-up still flatters the 21-day number.
    in_drawdown = ((r21 is not None and r21 <= DRAWDOWN_21D)
                   or (r1 is not None and r5 - r1 <= PRE_BOUNCE_SLIDE))
    if in_drawdown and r1 is not None and r1 >= BOUNCE_DAY:
        return "bouncing"             # selloff met a sharp up-day — squeeze tape
    if in_drawdown and (r3 is None or r3 >= -0.5):
        return "stabilizing"          # deep recent drawdown, slide has paused
    if r5 <= R5_FALL:
        return "falling"
    if prox is not None and prox >= NEAR_HIGH:
        return "at highs"
    if r5 >= R5_RISE:
        return "rising"
    return "drifting"


def day_read(r1, pc, si, mm, state):
    """The section's bottom line: was TODAY bullish or bearish — direction from
    the day's move (price is the day), with the positioning legs explaining the
    CHARACTER of the session. Returns (label, one-line rationale)."""
    if r1 is None:
        return "n/a", "no price data"
    pc_vhigh = pc is not None and pc >= PC_VHIGH
    pc_high = pc is not None and pc >= PUT_HEAVY
    pc_low = pc is not None and pc <= CALL_HEAVY
    si_high = si is not None and si >= SI_HIGH
    si_low = si is not None and si < SI_LOW
    si_rising = mm is not None and mm >= SI_RISING

    if r1 >= 0.15:
        if state == "bouncing" and (si_high or pc_vhigh):
            return "Bullish", ("short-squeeze rebound — shorts/puts were loaded "
                               "into the selloff and fueled the rip")
        if pc_high:
            return "Bullish", "advance, but heavily hedged — protection still bid"
        if pc_low and si_low and state == "at highs":
            return "Bullish", "complacent advance — few hedges or shorts in place"
        return "Bullish", "broad advance; positioning unremarkable"
    if r1 <= -0.15:
        if pc_high and (si_high or si_rising):
            return "Bearish", "conviction selling — puts bid and shorts loaded/rising"
        if pc_low:
            return "Bearish", "decline without hedging demand — flows not panicked"
        return "Bearish", "orderly decline; positioning unremarkable"
    if pc_vhigh:
        return "Neutral", "flat session, but protection heavily bid underneath"
    if state in ("bouncing", "stabilizing"):
        return "Neutral", "flat session — recent selloff pausing"
    return "Neutral", "quiet session"


def build_positioning(groups: dict = None, fetch=_per_ticker,
                      short_fetch=_short_info, history_fn=_price_metrics) -> list:
    """Per-basket positioning-regime rows (+ an all-tracked summary row).

    `groups` maps basket name -> tickers (default: the Sector-Watch baskets).
    `fetch`/`short_fetch`/`history_fn` are injectable for tests. Returns rows
    sorted most put-heavy first (summary row pinned last), or [].
    """
    if groups is None:
        import sector_watch
        groups = {name: cfg["tickers"] for name, cfg in sector_watch.SECTOR_BASKETS.items()}
    all_t = sorted({t for ts in groups.values() for t in ts})
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        flows = dict(zip(all_t, ex.map(fetch, all_t)))
        shorts = dict(zip(all_t, ex.map(short_fetch, all_t)))
    prices = history_fn(all_t)

    rows = []
    for name, tickers in list(groups.items()) + [("All tracked", all_t)]:
        pv = cv = 0.0
        covered = 0
        for t in tickers:
            f = flows.get(t)
            if f:
                covered += 1
                pv += f[0]
                cv += f[1]
        pc = round(pv / cv, 3) if cv else None
        if pc is None:
            continue
        si = _median([shorts[t][0] for t in tickers if t in shorts])
        mm = _median([shorts[t][1] for t in tickers if t in shorts])
        pm = [prices[t] for t in tickers if t in prices]
        r1 = _median([p[0] for p in pm])
        r5 = _median([p[1] for p in pm])
        state = price_state(r1, r5, _median([p[2] for p in pm]),
                            _median([p[3] for p in pm]), _median([p[4] for p in pm]))
        label, why = day_read(r1, pc, si, mm, state)
        rows.append({"basket": name, "otm_put_call": pc,
                     "put_vol": int(pv), "call_vol": int(cv),
                     "covered": covered, "total": len(tickers),
                     "short_pct_float": round(si, 4) if si is not None else None,
                     "si_mm": round(mm, 3) if mm is not None else None,
                     "r1_pct": round(r1, 2) if r1 is not None else None,
                     "r5_pct": round(r5, 2) if r5 is not None else None,
                     "price_state": state,
                     "today": label, "today_why": why})
    if not any(r["basket"] != "All tracked" for r in rows):
        return []
    rows.sort(key=lambda r: (r["basket"] == "All tracked", -r["otm_put_call"]))
    return rows


def render_md(rows: list) -> str:
    """Markdown rendering for the data block / Claude."""
    if not rows:
        return ""
    lines = ["### Positioning & Regime (OTM put/call + short float + price state; "
             "under evaluation)"]
    for r in rows:
        si = (f"{r['short_pct_float'] * 100:.1f}%"
              if r.get("short_pct_float") is not None else "n/a")
        r1 = f"{r['r1_pct']:+.1f}%" if r.get("r1_pct") is not None else "n/a"
        lines.append(f"- {r['basket']}: P/C {r['otm_put_call']:.2f}, "
                     f"short float {si}, day {r1} → "
                     f"**{r['today']}** — {r['today_why']}")
    return "\n".join(lines)

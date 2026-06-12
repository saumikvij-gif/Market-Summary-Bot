import options_positioning as op


def _fake_fetch(flows):
    """P/C fetch stub: {sym: (put_vol, call_vol) or None}."""
    return lambda sym: flows.get(sym)


def _fake_short(shorts):
    """short-info stub: {sym: (short_pct_float, mm_ratio)}."""
    return lambda sym: shorts.get(sym, (None, None))


def _fake_prices(prices):
    """history stub: {sym: (r1, r5, r21, r3, prox)}."""
    return lambda tickers: prices


def test_flow_read_thresholds():
    assert op.flow_read(op.PUT_HEAVY) == "put-heavy (hedging)"
    assert op.flow_read(op.CALL_HEAVY) == "call-heavy (bullish bets)"
    assert op.flow_read(0.9) == "balanced"
    assert op.flow_read(None) == "n/a"


def test_price_state_classification():
    #               r1     r5     r21    r3    prox
    assert op.price_state(-1.0, -4.0, -8.0, -2.0, 0.80) == "falling"
    # A big up-day INSIDE a deep selloff is bouncing, not falling — even with a
    # red 5-day (the live-squeeze case that used to contradict Sector Watch).
    assert op.price_state(+9.8, -3.1, -15.0, -2.0, 0.85) == "bouncing"
    # A crash so fresh the 21-day still looks fine: the brutal week-ex-today
    # (r5 - r1 = -12.9) marks the selloff instead.
    assert op.price_state(+9.8, -3.1, -2.0, -2.0, 0.85) == "bouncing"
    assert op.price_state(+0.2, -1.0, -9.0, +0.5, 0.85) == "stabilizing"
    assert op.price_state(+1.0, +1.0, +3.0, +0.5, 0.99) == "at highs"
    assert op.price_state(+1.0, +4.0, +6.0, +2.0, 0.90) == "rising"
    assert op.price_state(+0.1, +0.5, +1.0, +0.2, 0.90) == "drifting"
    assert op.price_state(None, None, None, None, None) == "n/a"


def test_day_read_direction_follows_the_move():
    # The label is ALWAYS the day's direction; positioning only sets the why.
    assert op.day_read(+2.0, 0.9, 0.02, 1.0, "rising")[0] == "Bullish"
    assert op.day_read(-2.0, 0.9, 0.02, 1.0, "falling")[0] == "Bearish"
    assert op.day_read(+0.05, 0.9, 0.02, 1.0, "drifting")[0] == "Neutral"
    assert op.day_read(None, 0.9, 0.02, 1.0, "n/a")[0] == "n/a"


def test_day_read_character_from_positioning():
    # Squeeze rebound: big up-day off a selloff with shorts loaded.
    label, why = op.day_read(+9.8, 2.31, 0.071, 1.03, "bouncing")
    assert label == "Bullish" and "squeeze" in why
    # Hedged advance: up day but puts heavily bid.
    label, why = op.day_read(+1.4, 3.01, 0.01, 1.1, "at highs")
    assert label == "Bullish" and "hedged" in why
    # Conviction selling: down day with puts bid and shorts loaded/rising.
    label, why = op.day_read(-2.0, 1.5, 0.04, 1.2, "falling")
    assert label == "Bearish" and "conviction" in why
    # Unpanicked decline: down day with call-tilted flow.
    label, why = op.day_read(-1.0, 0.5, 0.01, 1.0, "falling")
    assert label == "Bearish" and "not panicked" in why


def test_build_positioning_aggregates_and_reads_the_day():
    groups = {"Squeeze": ["AAA", "BBB"], "Chasing": ["CCC"]}
    flows = {"AAA": (300.0, 100.0), "BBB": (100.0, 100.0),   # pooled P/C = 2.0
             "CCC": (50.0, 200.0)}                            # P/C = 0.25
    shorts = {"AAA": (0.06, 1.2), "BBB": (0.08, 1.1), "CCC": (0.01, 0.9)}
    prices = {"AAA": (9.0, -3.0, -15.0, -2.0, 0.8), "BBB": (9.0, -3.0, -15.0, -2.0, 0.8),
              "CCC": (1.0, 1.0, 3.0, 0.5, 0.99)}
    rows = op.build_positioning(groups, fetch=_fake_fetch(flows),
                                short_fetch=_fake_short(shorts),
                                history_fn=_fake_prices(prices))
    names = [r["basket"] for r in rows]
    # Most put-heavy first; the all-names summary row is pinned last.
    assert names == ["Squeeze", "Chasing", "All tracked"]
    squeeze = rows[0]
    assert squeeze["otm_put_call"] == 2.0
    assert squeeze["short_pct_float"] == 0.07          # median of 6% and 8%
    assert squeeze["price_state"] == "bouncing"
    assert squeeze["today"] == "Bullish" and "squeeze" in squeeze["today_why"]
    chasing = rows[1]
    assert chasing["today"] == "Bullish" and "complacent" in chasing["today_why"]
    # Summary row pools every ticker once: (300+100+50)/(100+100+200) = 1.125.
    assert rows[-1]["otm_put_call"] == 1.125


def test_build_positioning_skips_failed_and_empty():
    groups = {"Mixed": ["AAA", "DEAD"]}
    rows = op.build_positioning(groups, fetch=_fake_fetch({"AAA": (10.0, 10.0)}),
                                short_fetch=_fake_short({}),
                                history_fn=_fake_prices({}))
    assert rows[0]["covered"] == 1 and rows[0]["total"] == 2   # DEAD skipped
    assert rows[0]["price_state"] == "n/a"                      # no history
    assert op.build_positioning(groups, fetch=_fake_fetch({}),
                                short_fetch=_fake_short({}),
                                history_fn=_fake_prices({})) == []


def test_render_md_shows_day_verdict_and_handles_empty():
    groups = {"Sellers": ["AAA"]}
    rows = op.build_positioning(groups, fetch=_fake_fetch({"AAA": (30.0, 10.0)}),
                                short_fetch=_fake_short({"AAA": (0.06, 1.2)}),
                                history_fn=_fake_prices({"AAA": (-2.0, -4.0, -8.0, -2.0, 0.8)}))
    md = op.render_md(rows)
    assert "Positioning & Regime" in md and "under evaluation" in md
    assert "P/C 3.00" in md and "**Bearish**" in md and "conviction" in md
    assert op.render_md([]) == ""


def test_pdf_block_ends_in_a_simple_day_call():
    import pdf_report
    groups = {"Sellers": ["AAA"]}
    rows = op.build_positioning(groups, fetch=_fake_fetch({"AAA": (30.0, 10.0)}),
                                short_fetch=_fake_short({"AAA": (0.06, 1.2)}),
                                history_fn=_fake_prices({"AAA": (-2.0, -4.0, -8.0, -2.0, 0.8)}))
    html = pdf_report._options_positioning_block(rows)
    assert "Positioning &amp; Regime" in html
    assert "Bearish" in html and "conviction" in html
    assert "3.00" in html and "6.0%" in html
    # Lean table: no flow/coverage columns, no multi-day regime verdicts.
    assert "Coverage" not in html and "Flow" not in html and "MIXED" not in html
    assert pdf_report._options_positioning_block([]) == ""

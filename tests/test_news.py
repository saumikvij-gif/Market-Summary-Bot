import calendar
import time

import news_feeds as rn


def test_foreign_bourse_recaps_skipped():
    # The Investing.com template recaps that polluted the standout picks.
    assert rn.is_boilerplate("Morocco stocks lower at close of trade; Moroccan All Shares down 0.11%")
    assert rn.is_boilerplate("Israel stocks higher at close of trade; TA 35 up 0.40%")
    # A real US-market headline is kept.
    assert not rn.is_boilerplate("Nvidia jumps after Jensen Huang touts Marvell")


def test_recency_filter():
    now = time.time()
    fresh = {"published_parsed": time.gmtime(now - 3600)}          # 1h old
    stale = {"published_parsed": time.gmtime(now - 72 * 3600)}     # 72h old
    undated = {"title": "no date here"}
    assert rn._is_stale(fresh, now_ts=now) is False
    assert rn._is_stale(stale, now_ts=now) is True
    assert rn._is_stale(undated, now_ts=now) is False             # undated → kept


def test_us_market_relevance_gate():
    # US index / Fed / mega-cap / cross-asset → kept.
    assert rn.is_us_market_relevant("S&P 500 closes at a record high")
    assert rn.is_us_market_relevant("Fed holds interest rates steady")
    assert rn.is_us_market_relevant("Nvidia jumps after earnings beat")
    assert rn.is_us_market_relevant("Treasury yields climb as CPI runs hot")
    assert rn.is_us_market_relevant("Bitcoin surges past a new high")
    # Foreign-bourse / off-topic → dropped (the Morocco-class noise).
    assert not rn.is_us_market_relevant("Nikkei climbs as the yen weakens")
    assert not rn.is_us_market_relevant("FTSE 100 slips at the open")
    assert not rn.is_us_market_relevant("Local council approves a new park")
    # A bare foreign "<country> stocks" must NOT pass on the word "stocks" alone.
    assert not rn.is_us_market_relevant("China stocks mixed in quiet session")


def test_norm_title_dedup_key():
    # Same story, different punctuation/casing → same dedup key.
    assert (rn._norm_title("Apple's Q3: Revenue beats!")
            == rn._norm_title("apple s q3   revenue beats"))


def test_wants_us_gate_targeting():
    # Outlet feeds get the gate; the Fed feed and the subreddits are exempt.
    assert rn._wants_us_gate("Yahoo Finance") is True
    assert rn._wants_us_gate("Investing.com") is True
    assert rn._wants_us_gate("Fed (Monetary Policy)") is False
    assert rn._wants_us_gate("r/wallstreetbets") is False

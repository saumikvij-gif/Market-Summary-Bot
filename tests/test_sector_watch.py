import sector_watch


def test_render_md_handles_missing_values():
    rows = [
        {"sector": "Semiconductors / Compute", "move_pct": 1.08,
         "rel_strength": 0.9, "breadth_pct": 80, "news_score": 0.4,
         "score": 0.5, "label": "Bullish"},
        {"sector": "Memory (DRAM/NAND/HBM)", "move_pct": None,
         "rel_strength": None, "breadth_pct": None, "news_score": None,
         "score": 0.0, "label": "Neutral"},
    ]
    md = sector_watch.render_md(rows)
    assert "Sector Watch" in md
    assert "Semiconductors / Compute" in md
    assert "+1.08%" in md
    assert "80% trend" in md       # breadth rendered
    assert "Bullish" in md
    assert "n/a" in md             # missing values render as n/a
    assert sector_watch.render_md([]) == ""


def test_median_robust_to_outlier():
    # One +30% name shouldn't define a basket whose other members are flat-ish.
    assert sector_watch._median([0.1, 0.2, 0.3, 30.0]) == (0.2 + 0.3) / 2
    assert sector_watch._median([1.0, -1.0, 5.0]) == 1.0   # odd count → middle value


def test_momentum_score_normalizes_and_clamps():
    fs = sector_watch.MOMENTUM_FULL_SCALE_PCT
    assert sector_watch._momentum_score(0.0) == 0.0
    assert sector_watch._momentum_score(fs) == 1.0          # +full scale → +1
    assert sector_watch._momentum_score(-fs) == -1.0        # −full scale → −1
    assert sector_watch._momentum_score(2 * fs) == 1.0      # clamped, not >1
    assert 0 < sector_watch._momentum_score(fs / 2) < 1.0   # partial week → partial
    assert sector_watch._momentum_score(float("nan")) is None
    assert sector_watch._momentum_score(None) is None


def test_calibrate_decompresses_without_flipping_sign():
    cal = sector_watch._calibrate
    # 0 and the rails are fixed points (no-op at the extremes).
    assert cal(0.0) == 0.0
    assert cal(1.0) == 1.0
    assert cal(-1.0) == -1.0
    # Mid-range conviction is pushed OUT toward the rail (counters blend shrinkage)…
    assert cal(0.3) > 0.3
    assert cal(-0.3) < -0.3
    # …but the sign is never flipped, and it stays within [-1, 1].
    assert cal(0.3) > 0 and cal(-0.3) < 0
    assert -1.0 <= cal(0.9) <= 1.0
    # None passes straight through (a missing score stays missing).
    assert cal(None) is None


def test_rel_strength_nan_safe():
    nan = float("nan")
    # Normal case: real delta + bounded score.
    score, delta = sector_watch._rel_strength(1.0, -1.0)   # basket +1 vs bench -1
    assert delta == 2.0 and 0 < score <= 1.0
    # NaN/None benchmark or basket → drops out entirely (no "nan%", no clamp-to-1).
    assert sector_watch._rel_strength(1.0, nan) == (None, None)
    assert sector_watch._rel_strength(nan, -1.0) == (None, None)
    assert sector_watch._rel_strength(1.0, None) == (None, None)
    assert sector_watch._finite(nan) is False
    assert sector_watch._finite(0.0) is True


def test_trend_strength_with_live_price():
    flat = [10.0] * 210                       # flat 1y history
    # Live price above the (near-flat) 20/50-day MAs → all cleared → 1.0.
    assert sector_watch._trend_strength_with(flat, 12.0) == 1.0
    # Below → none cleared → 0.0.
    assert sector_watch._trend_strength_with(flat, 8.0) == 0.0
    # No live price → no signal.
    assert sector_watch._trend_strength_with(flat, None) is None


def test_sector_metric_weights():
    w = sector_watch.SECTOR_METRIC_WEIGHTS
    assert (w["rel_strength"], w["breadth"], w["news"], w["momentum"], w["reddit"]) \
        == (0.35, 0.30, 0.25, 0.10, 0.00)
    assert abs(sum(w.values()) - 1.0) < 1e-9


def test_rs_full_scale_widened():
    # Widened so high-beta baskets don't pin relative strength to ±1 constantly.
    assert sector_watch.RS_FULL_SCALE_PCT >= 3.0


def test_news_company_aggregation_caps_single_company_flooding():
    # An earnings day floods Oracle with 5 bearish stories; Microsoft has 1 bullish.
    companies = {"Oracle": ["oracle", "orcl"], "Microsoft": ["microsoft", "msft"]}
    titles = [f"Oracle stock falls on costs {i}" for i in range(5)] + ["Microsoft soars on cloud"]
    scores = {t: (-0.8 if "Oracle" in t else 0.8) for t in titles}
    score, detail = sector_watch._company_news_score(titles, companies, scores.get)
    # Naive per-article mean would be dragged bearish by Oracle's volume…
    naive = sum(scores[t] for t in titles) / len(titles)
    assert naive < -0.4
    # …but per-constituent aggregation gives each company one vote: (-0.8 + 0.8)/2 = 0.
    assert detail["companies"] == 2
    assert abs(score - 0.0) < 1e-9


def test_news_unattributed_headlines_form_one_sector_bucket():
    companies = {"Oracle": ["oracle"]}
    scores = {"Oracle wins big contract": 0.9,
              "Cloud spending trends shift": -0.3,
              "Sector-wide capex macro note": -0.3}
    score, detail = sector_watch._company_news_score(list(scores), companies, scores.get)
    # Oracle bucket = +0.9; the two unattributed items collapse to ONE bucket (-0.3).
    assert detail["companies"] == 1 and detail["themed"] == 2
    assert abs(score - 0.3) < 1e-9          # mean(+0.9, -0.3)


def test_news_company_alias_is_whole_word():
    # "MU" (Micron) must not match inside "museum"; whole-word matching guards this.
    companies = {"Micron": ["micron", "mu"]}
    score, detail = sector_watch._company_news_score(
        ["The museum opened today"], companies, lambda t: 0.5)
    assert detail["companies"] == 0 and detail["themed"] == 1   # no false Micron hit


def test_news_per_company_cap_bounds_scoring():
    # 20 fresh Oracle headlines available, but cap=8 means only 8 are ever scored
    # (bounds FinBERT cost) — Oracle still contributes exactly one vote.
    companies = {"Oracle": ["oracle"]}
    titles = [f"Oracle update number {i}" for i in range(20)]
    scored_calls = []

    def scorer(t):
        scored_calls.append(t)
        return 0.5

    score, detail = sector_watch._company_news_score(titles, companies, scorer, cap=8)
    assert len(scored_calls) == 8 and detail["scored"] == 8
    assert abs(score - 0.5) < 1e-9


def test_reddit_sentiment_keyword_match():
    titles = ["Nvidia GPU demand is insane", "weekend off-topic chat"]
    score, n = sector_watch._reddit_sentiment(["gpu", "nvidia"], titles)
    assert n == 1                 # only the first title matches
    score, n = sector_watch._reddit_sentiment(["gpu"], [])
    assert (score, n) == (None, 0)

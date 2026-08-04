import bloomberg_close as bc


def test_title_regex_matches_real_pattern():
    # Real title shapes from the Bloomberg Television channel feed.
    assert bc.TITLE_RE.search("Stocks in the Red Amid Geopolitical Uncertainty | The Close 7/27/2026")
    assert bc.TITLE_RE.search("Bloomberg The Close 06/12/2026")
    # Other daily shows must not match.
    assert not bc.TITLE_RE.search("Chip Rout Deepens | The Asia Trade 7/28/2026")
    assert not bc.TITLE_RE.search("Closing bell recap")   # 'Close' alone isn't the show


def test_show_regexes_do_not_cross_match():
    # Real titles seen on the channel 2026-08 — each show's regex must match
    # its own episodes and nothing else (two shows end in "Trade").
    titles = {
        "close": "Stocks & Bonds Rose as US-Iran Hopes Spur Oil Drop | The Close 8/3/2026",
        "opening_trade": "Europe's Key Rivers Are Drying Up | The Opening Trade 8/3/2026",
        "asia_trade": "Yen Rally Loses Steam | The Asia Trade 8/4/2026",
    }
    for show, cfg in bc.SHOWS.items():
        for other, title in titles.items():
            assert bool(cfg["regex"].search(title)) == (show == other), \
                f"{show} regex vs {other} title"
    # Neighbouring shows that share words must not match anyone's regex.
    for stray in ("\"Bull Market Could Last Through 2030\" | Open Interest 8/3/2026",
                  "New Models Take On OpenAI | The China Show | 8/3/2026",
                  "Iran Peace Talks to Restart, Deal Could Be Close"):
        assert not any(cfg["regex"].search(stray) for cfg in bc.SHOWS.values()), stray


def test_date_from_title_and_grid_title_cleanup():
    assert bc._date_from_title("Stocks Slide | The Close 7/27/2026") == "2026-07-27"
    assert bc._date_from_title("Bloomberg The Close 12/3/2026") == "2026-12-03"
    assert bc._date_from_title("no date here") == ""
    # aria-label chrome stripped back to the bare episode title.
    assert bc._clean_grid_title(
        "Stocks in the Red | The Close 7/27/2026 1 hour, 31 minutes"
    ) == "Stocks in the Red | The Close 7/27/2026"
    assert bc._clean_grid_title(
        "Markets Rally | The Close 7/28/2026 by Bloomberg Television 12,345 views"
    ) == "Markets Rally | The Close 7/28/2026"


def test_proxy_config_absent_without_env(monkeypatch):
    # Unset AND empty (how an undefined GitHub secret expands) both mean "off".
    monkeypatch.delenv("WEBSHARE_PROXY_USERNAME", raising=False)
    monkeypatch.delenv("WEBSHARE_PROXY_PASSWORD", raising=False)
    assert bc._proxy_config() is None
    monkeypatch.setenv("WEBSHARE_PROXY_USERNAME", "")
    monkeypatch.setenv("WEBSHARE_PROXY_PASSWORD", "")
    assert bc._proxy_config() is None


def test_clean_panel_lines_strips_timestamps_and_chrome():
    raw = "\n".join([
        "Transcript", "Search transcript",
        "0:01", "1 second", ">> THE COUNTDOWN IS ON.",
        "45:53", "45 minutes, 53 seconds", "A MIXED BAG FOR THE BROAD MARKETS.",
        "1:31:11", "1 hour, 31 minutes, 11 seconds", "THAT DOES IT FOR US.",
        "English (auto-generated)", "",
    ])
    text = bc.clean_panel_lines(raw)
    assert text == (">> THE COUNTDOWN IS ON. A MIXED BAG FOR THE BROAD MARKETS. "
                    "THAT DOES IT FOR US.")


def test_fetch_transcript_chain_api_then_browser_then_none(monkeypatch):
    calls = []
    # API blocked, browser succeeds → browser text wins.
    monkeypatch.setattr(bc, "fetch_transcript_api",
                        lambda v: calls.append("api") or None)
    monkeypatch.setattr(bc, "fetch_transcript_browser",
                        lambda v: calls.append("browser") or "spoken words " * 300)
    text = bc.fetch_transcript("vid")
    assert calls == ["api", "browser"] and text.startswith("spoken words")
    # Both blocked → None (rundown mode downstream).
    monkeypatch.setattr(bc, "fetch_transcript_browser", lambda v: None)
    assert bc.fetch_transcript("vid") is None
    # Transcript capped to bound Claude cost.
    monkeypatch.setattr(bc, "fetch_transcript_api",
                        lambda v: "x" * (bc.MAX_TRANSCRIPT_CHARS + 999))
    assert len(bc.fetch_transcript("vid")) == bc.MAX_TRANSCRIPT_CHARS


def test_build_summary_falls_back_to_rundown(monkeypatch):
    episode = {"video_id": "abc123", "title": "Markets Rally | The Close 7/27/2026",
               "published": "2026-07-27", "description": "Today's guests are X, Y."}
    monkeypatch.setattr(bc, "find_latest_episode", lambda show: episode)
    monkeypatch.setattr(bc, "fetch_transcript", lambda vid: None)   # blocked
    monkeypatch.setattr(bc, "summarize_rundown", lambda ep: "### On the show\n- X\n- Y")
    info = bc.build_bloomberg_summary()
    assert info["mode"] == "rundown"
    assert info["url"].endswith("abc123")
    assert "On the show" in info["summary_md"]
    assert info["show"] == "The Close"          # default show unchanged


def test_build_summary_uses_transcript_when_available(monkeypatch):
    episode = {"video_id": "abc123", "title": "Markets Rally | The Close 7/27/2026",
               "published": "2026-07-27", "description": "guests"}
    monkeypatch.setattr(bc, "find_latest_episode", lambda show: episode)
    monkeypatch.setattr(bc, "fetch_transcript", lambda vid: "lots of words " * 100)
    captured = {}
    def fake_sum(ep, tr):
        captured["transcript"] = tr
        return "### Key takeaways\n- markets rallied"
    monkeypatch.setattr(bc, "summarize_transcript", fake_sum)
    info = bc.build_bloomberg_summary()
    assert info["mode"] == "transcript"
    assert "Key takeaways" in info["summary_md"]
    assert captured["transcript"].startswith("lots of words")


def test_build_summary_none_when_no_episode(monkeypatch):
    monkeypatch.setattr(bc, "find_latest_episode", lambda show: None)
    assert bc.build_bloomberg_summary() is None


def test_build_summary_other_show_carries_identity(monkeypatch):
    episode = {"video_id": "xyz789", "title": "Yen Rallies | The Asia Trade 8/4/2026",
               "published": "2026-08-04", "description": "guests",
               "show": "The Asia Trade", "show_desc": "Asia market-open morning show"}
    seen = {}
    def fake_find(show):
        seen["show"] = show
        return episode
    monkeypatch.setattr(bc, "find_latest_episode", fake_find)
    monkeypatch.setattr(bc, "fetch_transcript", lambda vid: None)
    monkeypatch.setattr(bc, "summarize_rundown", lambda ep: "### On the show\n- A")
    info = bc.build_bloomberg_summary("asia_trade")
    assert seen["show"] == "asia_trade"
    assert info["show"] == "The Asia Trade"
    assert "Bloomberg: The Asia Trade" in bc.render_md(info)


def test_render_md_labels_mode_and_handles_none():
    info = {"title": "T | The Close 7/27/2026", "published": "2026-07-27",
            "url": "https://youtube.com/watch?v=x", "mode": "rundown",
            "summary_md": "### On the show\n- A guest"}
    md = bc.render_md(info)
    assert "Bloomberg: The Close" in md and "transcript unavailable" in md
    info["mode"] = "transcript"
    assert "full-transcript summary" in bc.render_md(info)
    assert bc.render_md(None) == ""


def test_pdf_block_renders_and_omits_cleanly():
    import pdf_report
    close = {"show": "The Close", "title": "Markets Rally | The Close 7/27/2026",
             "published": "2026-07-27", "url": "https://www.youtube.com/watch?v=x",
             "mode": "rundown",
             "summary_md": "### On the show\n- Jane Doe — Example Capital CIO"}
    asia = {"show": "The Asia Trade", "title": "Yen Jumps | The Asia Trade 7/28/2026",
            "published": "2026-07-28", "url": "https://www.youtube.com/watch?v=y",
            "mode": "transcript", "summary_md": "### Key takeaways\n- yen rallied"}
    html = pdf_report._bloomberg_block([close, asia])
    assert html.count("Bloomberg News Summary") == 1     # one section header
    assert "The Close" in html and "The Asia Trade" in html
    assert "Episode rundown" in html and "Full-transcript summary" in html
    assert "Jane Doe" in html and "yen rallied" in html
    # Original single-dict shape still accepted; empties omit cleanly.
    assert "Jane Doe" in pdf_report._bloomberg_block(close)
    assert pdf_report._bloomberg_block(None) == ""
    assert pdf_report._bloomberg_block({}) == ""
    assert pdf_report._bloomberg_block([]) == ""
    assert pdf_report._bloomberg_block([{}, None]) == ""

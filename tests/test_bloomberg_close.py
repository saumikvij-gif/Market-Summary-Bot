import bloomberg_close as bc


def test_title_regex_matches_real_pattern():
    # Real title shapes from the Bloomberg Television channel feed.
    assert bc.TITLE_RE.search("Stocks in the Red Amid Geopolitical Uncertainty | The Close 7/27/2026")
    assert bc.TITLE_RE.search("Bloomberg The Close 06/12/2026")
    # Other daily shows must not match.
    assert not bc.TITLE_RE.search("Chip Rout Deepens | The Asia Trade 7/28/2026")
    assert not bc.TITLE_RE.search("Closing bell recap")   # 'Close' alone isn't the show


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
    monkeypatch.setattr(bc, "find_latest_close", lambda: episode)
    monkeypatch.setattr(bc, "fetch_transcript", lambda vid: None)   # blocked
    monkeypatch.setattr(bc, "summarize_rundown", lambda ep: "### On the show\n- X\n- Y")
    info = bc.build_bloomberg_summary()
    assert info["mode"] == "rundown"
    assert info["url"].endswith("abc123")
    assert "On the show" in info["summary_md"]


def test_build_summary_uses_transcript_when_available(monkeypatch):
    episode = {"video_id": "abc123", "title": "Markets Rally | The Close 7/27/2026",
               "published": "2026-07-27", "description": "guests"}
    monkeypatch.setattr(bc, "find_latest_close", lambda: episode)
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
    monkeypatch.setattr(bc, "find_latest_close", lambda: None)
    assert bc.build_bloomberg_summary() is None


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
    info = {"title": "Markets Rally | The Close 7/27/2026", "published": "2026-07-27",
            "url": "https://www.youtube.com/watch?v=x", "mode": "rundown",
            "summary_md": "### On the show\n- Jane Doe — Example Capital CIO"}
    html = pdf_report._bloomberg_block(info)
    assert "Bloomberg News Summary" in html
    assert "Episode rundown" in html and "Jane Doe" in html
    assert pdf_report._bloomberg_block(None) == ""
    assert pdf_report._bloomberg_block({}) == ""

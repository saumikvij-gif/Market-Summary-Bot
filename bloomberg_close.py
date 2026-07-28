"""
bloomberg_close.py
------------------
"Bloomberg News Summary": a nightly summary of Bloomberg Television's daily
market-close show, **The Close**, as a new display-only briefing section.

How it works
  1. Find the latest episode via the channel's public RSS feed (no API key) —
     entries titled "… | The Close M/D/YYYY" on the Bloomberg Television channel.
  2. Try to pull the episode's YouTube auto-transcript and have Claude write a
     proper summary of everything said (key takeaways, guest views, stocks).
  3. If the transcript can't be fetched, fall back to an honest "episode
     rundown" built from the video's description — which reliably carries the
     full guest lineup — clearly labelled that the transcript was unavailable.

The transcript reality (verified 2026-07): YouTube blocks CAPTION-API requests
from cloud/datacenter IPs (GitHub Actions included), and the public proxy
front-ends (Invidious/Piped) are blocked upstream too. Three-layer fetch chain:
  a. youtube-transcript-api — direct; works from unblocked IPs, or from
     anywhere with the optional Webshare rotating-residential proxy
     (WEBSHARE_PROXY_USERNAME / WEBSHARE_PROXY_PASSWORD env / secrets).
  b. Playwright browser scrape — a real headless Chromium opens the watch page
     and clicks "Show transcript"; the transcript PANEL rides a different,
     ungated API, so this works even from IPs the caption API refuses
     (verified: pulled a full 16.7k-word episode from a bot-flagged IP).
  c. Episode rundown from the video description (guest lineup) — always
     available, clearly labelled that the transcript was missing.
Cookie-based auth was rejected — YouTube bans accounts used that way.

Timing note: The Close uploads ~2h after the 21:30 UTC pipeline run, so the
summarised episode is usually the PREVIOUS session's — the episode date is
always shown so the reader knows exactly what they're getting.

DISPLAY-ONLY and fail-safe: feeds no scores; any failure just omits the section.
"""

import os
import re

import requests
import feedparser

from utils import retry

CHANNEL_ID = "UCIALMKvObZNtJ6AmdCLP7Lg"          # Bloomberg Television
RSS_URL = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
TITLE_RE = re.compile(r"\bThe Close\b", re.I)
TIMEOUT = 20
HEADERS = {"User-Agent": "Mozilla/5.0"}
MAX_TRANSCRIPT_CHARS = 150_000                    # bounds Claude cost on 2h shows
SUMMARY_MODEL = "claude-sonnet-4-5"


def find_latest_close():
    """Newest 'The Close' episode from the channel RSS, or None.

    Returns {video_id, title, published (ISO date), description}. The RSS
    description reliably includes the episode's full guest lineup; everything
    after the '--------' separator is channel boilerplate and is dropped.
    """
    resp = requests.get(RSS_URL, timeout=TIMEOUT, headers=HEADERS)
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)
    for entry in feed.entries:                    # newest first
        title = entry.get("title", "")
        if not TITLE_RE.search(title):
            continue
        desc = entry.get("media_description") or entry.get("summary") or ""
        desc = desc.split("--------")[0].strip()
        published = (entry.get("published", "") or "")[:10]
        return {"video_id": entry.get("yt_videoid"), "title": title,
                "published": published, "description": desc}
    return None


def _proxy_config():
    """Webshare rotating-residential proxy config from env, or None.

    This is the switch between transcript mode and rundown mode: with the two
    WEBSHARE_* env vars set (GitHub secrets in CI), transcript fetches work
    from cloud IPs; without them YouTube blocks the request and we fall back.
    """
    user = os.environ.get("WEBSHARE_PROXY_USERNAME")
    password = os.environ.get("WEBSHARE_PROXY_PASSWORD")
    if not (user and password):
        return None
    from youtube_transcript_api.proxies import WebshareProxyConfig
    return WebshareProxyConfig(proxy_username=user, proxy_password=password)


def fetch_transcript_api(video_id: str):
    """Layer (a): the caption API. Fast, but refused from most cloud IPs unless
    the optional Webshare proxy env vars are set. Returns text or None."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi(proxy_config=_proxy_config())
        fetched = api.fetch(video_id, languages=["en"])
        text = " ".join(seg.text for seg in fetched)
        return text if text.strip() else None
    except Exception as exc:
        print(f"  ℹ️  Caption API unavailable ({type(exc).__name__}) — trying the "
              f"browser transcript panel.")
        return None


# Chrome UA for the browser layer: Playwright's default announces
# "HeadlessChrome", which gets a degraded, caption-less page.
_BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
# Panel chrome / timestamp lines to strip from the transcript panel's innerText.
_TS_LINE = re.compile(r"^\d{1,2}(:\d{2}){1,2}$")
_DURATION_LINE = re.compile(
    r"^\d+\s+(hours?|minutes?|seconds?)(,\s*\d+\s+(minutes?|seconds?))*$")
_PANEL_CHROME = {"Transcript", "Search transcript", "Show transcript",
                 "Follow along using the transcript.",
                 "English", "English (auto-generated)"}


def clean_panel_lines(raw: str) -> str:
    """Panel innerText → plain transcript: drop timestamps ('45:53'), duration
    labels ('45 minutes, 53 seconds'), and panel chrome; join what's spoken."""
    kept = []
    for line in raw.splitlines():
        line = line.strip()
        if (not line or line in _PANEL_CHROME
                or _TS_LINE.match(line) or _DURATION_LINE.match(line)):
            continue
        kept.append(line)
    return " ".join(kept)


def fetch_transcript_browser(video_id: str):
    """Layer (b): real headless Chromium opens the watch page and clicks
    'Show transcript'. The transcript panel uses a different backend than the
    caption API and is served even to IPs the caption API refuses. Returns the
    episode text or None; never raises."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(locale="en-US", user_agent=_BROWSER_UA,
                                      viewport={"width": 1400, "height": 900})
            try:
                page = ctx.new_page()
                page.goto(f"https://www.youtube.com/watch?v={video_id}",
                          wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_timeout(3_000)
                try:                     # expand the collapsed description
                    page.locator("tp-yt-paper-button#expand").first.click(timeout=6_000)
                    page.wait_for_timeout(800)
                except Exception:
                    pass
                btn = page.locator(
                    "ytd-video-description-transcript-section-renderer button").first
                btn.scroll_into_view_if_needed(timeout=6_000)
                btn.click(timeout=8_000)
                page.wait_for_timeout(12_000)      # panel fills asynchronously
                raw = page.evaluate(
                    "() => { const e = document.querySelector("
                    "'ytd-engagement-panel-section-list-renderer"
                    "[visibility=\"ENGAGEMENT_PANEL_VISIBILITY_EXPANDED\"]');"
                    " return e ? e.innerText : ''; }")
            finally:
                browser.close()
        text = clean_panel_lines(raw)
        # A real episode is tens of thousands of words; a stub means the panel
        # never actually filled (bot wall, layout change) — treat as missing.
        if len(text.split()) < 200:
            print("  ℹ️  Browser transcript panel came back empty/stub.")
            return None
        return text
    except Exception as exc:
        print(f"  ℹ️  Browser transcript fetch failed ({type(exc).__name__}).")
        return None


def fetch_transcript(video_id: str):
    """The episode transcript via the layered chain, capped, or None."""
    text = fetch_transcript_api(video_id) or fetch_transcript_browser(video_id)
    if not text:
        print("  ℹ️  No transcript available — falling back to the episode rundown.")
        return None
    return text[:MAX_TRANSCRIPT_CHARS]


def _ask_claude(prompt: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    resp = retry(lambda: client.messages.create(
        model=SUMMARY_MODEL,
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}],
    ), attempts=2, label="bloomberg summary")
    return "".join(b.text for b in resp.content if b.type == "text").strip()


def summarize_transcript(episode: dict, transcript: str) -> str:
    """Full summary of everything said in the episode, as briefing markdown."""
    prompt = (
        "You are summarizing the transcript of Bloomberg Television's daily "
        f"market-close show. Episode: \"{episode['title']}\" "
        f"(aired {episode['published']}).\n\n"
        "Write a tight markdown summary for a daily market briefing email:\n"
        "- **Key takeaways** — 4-6 bullets covering the session's main stories "
        "as discussed on air\n"
        "- **Guest views** — each notable guest (name, affiliation) and their "
        "core argument, one line each\n"
        "- **Stocks & sectors discussed** — one bullet\n"
        "- End with the single most striking quote of the episode, attributed.\n"
        "Max ~300 words. No preamble, no title — start directly with the first "
        "section heading (use ### headings).\n\n"
        f"TRANSCRIPT:\n{transcript}"
    )
    return _ask_claude(prompt)


def summarize_rundown(episode: dict) -> str:
    """Fallback: an honest episode rundown from the title + description."""
    prompt = (
        "You have only the METADATA of today's episode of Bloomberg "
        "Television's market-close show (the transcript was unavailable). "
        "Write a short markdown rundown for a daily briefing email:\n"
        "- One line on the session theme, taken from the episode title\n"
        "- **On the show** — the guest lineup as bullets: name — role/firm, "
        "grouped sensibly (markets/strategy first, then corporate, then other)\n"
        "Do NOT invent anything said on air — you only know who appeared. "
        "Max ~150 words. Start directly with the first line (use ### headings "
        "if needed).\n\n"
        f"TITLE: {episode['title']}\n"
        f"AIRED: {episode['published']}\n"
        f"DESCRIPTION:\n{episode['description']}"
    )
    return _ask_claude(prompt)


def build_bloomberg_summary():
    """The full section build. Returns a dict or None (section omitted).

    {title, published, url, mode: 'transcript'|'rundown', summary_md}
    """
    episode = find_latest_close()
    if not episode or not episode.get("video_id"):
        print("  ⚠️  No 'The Close' episode found in the channel RSS.")
        return None
    transcript = fetch_transcript(episode["video_id"])
    mode = "transcript" if transcript else "rundown"
    summary_md = (summarize_transcript(episode, transcript) if transcript
                  else summarize_rundown(episode))
    if not summary_md:
        return None
    return {
        "title": episode["title"],
        "published": episode["published"],
        "url": f"https://www.youtube.com/watch?v={episode['video_id']}",
        "mode": mode,
        "summary_md": summary_md,
    }


def render_md(info: dict) -> str:
    """Markdown rendering for the data block / Claude's main narrative."""
    if not info:
        return ""
    tag = ("full-transcript summary" if info["mode"] == "transcript"
           else "episode rundown — transcript unavailable")
    return (f"### Bloomberg: The Close ({info['published']}; {tag})\n"
            f"{info['summary_md']}")


if __name__ == "__main__":
    # Standalone check: python bloomberg_close.py
    from utils import force_utf8
    from dotenv import load_dotenv
    load_dotenv()
    force_utf8()
    result = build_bloomberg_summary()
    if result:
        print(f"\n{result['title']}  [{result['mode']}]\n{result['url']}\n")
        print(result["summary_md"])
    else:
        print("No Bloomberg summary available.")

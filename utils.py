"""
utils.py
--------
Small shared helpers used across the pipeline: retry-with-backoff for flaky
network/API calls, value clamping, and UTF-8 console setup.
"""

import sys
import time


def force_utf8() -> None:
    """Reconfigure stdout to UTF-8 so symbols like ▲/▼/emoji don't crash on
    Windows consoles (cp1252). No-op where stdout can't be reconfigured."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    """Constrain x to [lo, hi]. The pipeline's sub-scores are all normalized to
    [-1, 1], so this is the single shared clamp used by every scoring module."""
    return max(lo, min(hi, x))


def retry(fn, attempts: int = 3, base_delay: float = 1.5, label: str = ""):
    """Call `fn()` up to `attempts` times with exponential backoff.

    Retries on any exception; re-raises the last one if all attempts fail.
    `base_delay` doubles each attempt (1.5s, 3s, 6s, …). `label` is used only
    for the warning message.
    """
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — intentional broad retry
            last_exc = exc
            if attempt < attempts:
                delay = base_delay * (2 ** (attempt - 1))
                where = f" ({label})" if label else ""
                print(f"  ⏳ attempt {attempt}/{attempts} failed{where}: {exc}; "
                      f"retrying in {delay:.1f}s…")
                time.sleep(delay)
    raise last_exc

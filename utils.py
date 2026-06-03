"""
utils.py
--------
Small shared helpers. Currently: retry-with-backoff for flaky network/API calls.
"""

import time


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

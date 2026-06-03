import pytest

from utils import retry


def test_retry_success_first_try():
    calls = []
    assert retry(lambda: calls.append(1) or "ok", attempts=3, base_delay=0) == "ok"
    assert len(calls) == 1


def test_retry_succeeds_after_failures():
    calls = []

    def f():
        calls.append(1)
        if len(calls) < 3:
            raise ValueError("transient")
        return "ok"

    assert retry(f, attempts=3, base_delay=0) == "ok"
    assert len(calls) == 3


def test_retry_raises_after_exhausting():
    def f():
        raise ValueError("boom")

    with pytest.raises(ValueError):
        retry(f, attempts=2, base_delay=0)

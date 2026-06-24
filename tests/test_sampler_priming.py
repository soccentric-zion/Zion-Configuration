"""Regression test for first-call psutil.cpu_percent=0 (#46).

psutil.cpu_percent() returns 0.0 for every core on the first call
because it uses the call as a baseline and reports *delta* on the
next call. A freshly-started dashboard would show 0% CPU for the
first tick. The fix: prime the baseline in Sampler.start() so the
first call after start() isn't the baseline call.
"""

from __future__ import annotations

import psutil

from app.tasks.sampler import Sampler


async def test_sampler_start_primes_psutil_cpu_percent(monkeypatch) -> None:
    """Sampler.start() must call psutil.cpu_percent once to seed the baseline (#46)."""
    calls: list[dict[str, object]] = []
    real_cpu_percent = psutil.cpu_percent

    def tracking_cpu_percent(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return real_cpu_percent(*args, **kwargs)

    monkeypatch.setattr(psutil, "cpu_percent", tracking_cpu_percent)

    sampler = Sampler()
    sampler.start()
    try:
        # Start() should have invoked psutil.cpu_percent at least once with
        # percpu=True so the next health() call returns real values, not
        # the baseline zero (#46).
        primed = [c for c in calls if c["kwargs"].get("percpu") is True]
        assert primed, "Sampler.start() did not call psutil.cpu_percent(percpu=True) to prime the baseline"
    finally:
        await sampler.stop()

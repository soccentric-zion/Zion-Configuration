"""Alert rule evaluation: thresholds, duration, rate-of-change, escalation."""

from __future__ import annotations

from app.tasks.alerts import (
    Rule,
    Sample,
    condition_met,
    evaluate,
    rate_of_change,
    should_escalate,
)


def _rule(**kw: object) -> Rule:
    base = {
        "id": 1,
        "name": "r",
        "metric": "cpu_temp_c",
        "condition": ">",
        "threshold": 80.0,
        "duration_s": 0,
        "severity": "warning",
    }
    base.update(kw)
    return Rule(**base)  # type: ignore[arg-type]


def test_condition_operators() -> None:
    assert condition_met(">", 90, 80)
    assert not condition_met(">", 70, 80)
    assert condition_met("<", 70, 80)
    assert condition_met("==", 80, 80)
    assert not condition_met("==", 80.5, 80)


def test_threshold_without_duration_uses_latest() -> None:
    samples = [Sample(0, 70), Sample(1, 85)]
    assert evaluate(_rule(duration_s=0), samples, now=1) is True


def test_duration_requires_sustained_breach() -> None:
    rule = _rule(duration_s=60)
    # One sample in window is below threshold -> not firing.
    samples = [Sample(now, v) for now, v in [(0, 85), (30, 70), (60, 85)]]
    assert evaluate(rule, samples, now=60) is False
    # All in-window samples breach -> firing.
    sustained = [Sample(t, 85) for t in (0, 30, 60)]
    assert evaluate(rule, sustained, now=60) is True


def test_rate_of_change() -> None:
    samples = [Sample(0, 10), Sample(10, 60)]  # +50 over 10s = 5/s
    assert rate_of_change(samples) == 5.0
    rule = _rule(condition="rate", threshold=4.0)
    assert evaluate(rule, samples, now=10) is True
    rule_slow = _rule(condition="rate", threshold=6.0)
    assert evaluate(rule_slow, samples, now=10) is False


def test_empty_samples_never_fire() -> None:
    assert evaluate(_rule(), [], now=0) is False


def test_escalation_timing_with_mock_clock() -> None:
    fired = 1000.0
    assert should_escalate(fired, acked=False, now=1000 + 300, escalate_after_s=300) is True
    assert should_escalate(fired, acked=False, now=1000 + 299, escalate_after_s=300) is False
    assert should_escalate(fired, acked=True, now=1000 + 999, escalate_after_s=300) is False

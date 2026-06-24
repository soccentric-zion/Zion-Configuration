"""Regression test for #112 — the H.264 encoder MUST be the software
V4L2 stateful path, not a (nonexistent) hardware H.264 encoder.

req.md §3 SoC Media: "the Pi 5 / CM5 has no dedicated H.264 hardware
encoder. Expose encode via the software / V4L2 stateful path,
clearly labeled as such. Do not present a nonexistent hardware H.264
encoder." The integration test in test_features.py already asserts
"software" is in the encoder status; this file pins the same
invariant at the HAL boundary so a future HAL change cannot regress
the contract without a test failure here.

CLAUDE.md Do #53: the HAL is the canonical enforcement point. If
the encoder surface ever starts reporting ``hardware: True`` for
H.264, the integration test will catch it, but the HAL-level test
is the authoritative pin.
"""

from __future__ import annotations

from app.hal.registry import get_hal


def test_h264_encoder_is_software_path() -> None:
    """``MediaHal.block_status('venc', 'encoder')`` must report
    ``hardware: False`` and a ``path`` that names the software V4L2
    stateful path."""
    hal = get_hal("media")
    r = hal.block_status("venc", "encoder")
    assert r["hardware"] is False, (
        "BCM2712 has no dedicated H.264 hardware encoder; the HAL "
        "MUST report hardware=False. If you are adding a real HW "
        "encoder, also update req.md §3 and audit §3."
    )
    assert r["path"], "encoder path field must be populated"
    assert (
        "software" in r["path"].lower() or "v4l2" in r["path"].lower()
    ), f"encoder path {r['path']!r} must mention software or V4L2"
    # And the codec must cover H.264 (so the test fails if someone
    # silently swaps the H.264 encoder for a different codec).
    assert "H.264" in r["codec"], (
        f"encoder codec {r['codec']!r} must include H.264 (the H.264 " "encoder surface must not silently disappear)"
    )


def test_h264_encoder_no_hardware_flag_in_block_listing() -> None:
    """Block listing must not surface any H.264 hardware encoder."""
    hal = get_hal("media")
    # Block defaults + block_status for "encoder" kind.
    r = hal.block_status("venc", "encoder")
    assert r["hardware"] is False
    # The envelope must carry a simulated field so the SPA banner /
    # `/api/system/simulation` endpoint can render consistently.
    assert "simulated" in r


def test_block_status_unknown_kind_returns_unknown_envelope() -> None:
    """Defensive — unknown kinds return an ``unknown`` envelope (don't
    raise). This is the current contract; if we ever switch to raising,
    update this test in the same change."""
    hal = get_hal("media")
    r = hal.block_status("venc", "nonexistent_kind")
    assert r.get("status") == "unknown"
    assert "simulated" in r

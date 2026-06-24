"""Regression tests for the system HAL identity fix (issue #40 / #43).

`build_date` previously returned `now - 30 days` when `/etc/os-release` was
missing, which made the dashboard lie about build age. Now `_build_epoch`
falls back to other real root paths (dpkg status, dpkg log, sw-versions)
or returns `None` — and `identity()` renders `build_date=None` rather
than a made-up date.
"""

from __future__ import annotations

import time
from pathlib import Path

from app.hal.system import SystemHal, _read_first


def test_read_first_skips_nul_only_file(tmp_path: Path) -> None:
    """An all-NUL file is treated as missing — the function falls through
    to the next path instead of returning the empty string (#40)."""
    nul = tmp_path / "nul.txt"
    nul.write_bytes(b"\x00" * 100)
    real = tmp_path / "real.txt"
    real.write_text("hello world\n")
    assert _read_first(str(nul), str(real)) == "hello world"


def test_read_first_strips_nul_padding(tmp_path: Path) -> None:
    """Leading/trailing NULs are stripped; real content is returned."""
    f = tmp_path / "padded.txt"
    f.write_bytes(b"\x00\x00hello\x00\x00")
    assert _read_first(str(f)) == "hello"


def test_read_first_returns_none_for_all_nul(tmp_path: Path) -> None:
    """All-NUL files with no fallback return None."""
    nul = tmp_path / "nul.txt"
    nul.write_bytes(b"\x00" * 100)
    assert _read_first(str(nul)) is None


def test_build_epoch_no_os_release_no_30day_lie(tmp_path: Path, monkeypatch) -> None:
    """When no candidate file exists, _build_epoch returns None rather
    than `now - 30 days` (#43)."""
    # Force every candidate path to be missing.
    from app.hal import system as sys_mod

    fake = ("/nonexistent/os-release", "/nonexistent/dpkg-status", "/nonexistent/dpkg.log", "/nonexistent/sw-versions")
    monkeypatch.setattr(sys_mod, "_BUILD_EPOCH_CANDIDATES", fake)
    hal = SystemHal()
    assert hal._build_epoch() is None


def test_identity_build_date_is_none_when_no_evidence(monkeypatch) -> None:
    """identity() renders build_date=None instead of a fake 30-days-ago."""
    from app.hal import system as sys_mod

    monkeypatch.setattr(
        sys_mod,
        "_BUILD_EPOCH_CANDIDATES",
        ("/nonexistent/os-release",),
    )
    hal = SystemHal()
    ident = hal.identity()
    assert ident["build_date"] is None


def test_build_epoch_uses_real_file(tmp_path: Path, monkeypatch) -> None:
    """If any candidate path exists, _build_epoch returns its mtime."""
    from app.hal import system as sys_mod

    f = tmp_path / "os-release"
    f.write_text('PRETTY_NAME="TestOS"\n')
    before = time.time()
    monkeypatch.setattr(
        sys_mod,
        "_BUILD_EPOCH_CANDIDATES",
        (str(f),),
    )
    hal = SystemHal()
    epoch = hal._build_epoch()
    assert epoch is not None
    assert before - 5 <= epoch <= time.time() + 5

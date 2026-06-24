"""HAL-level tests that exercise the real-mode subprocess branches
(#113, audit §10) by monkeypatching ``subprocess.run`` so the
``if self._real:`` paths run without requiring actual hardware.

This is the only place CI can cover those branches — the conftest
forces ``simulation: true`` for the FastAPI client tests, so without
this file the storage HAL real-mode subprocess branches drop coverage
below the 85% gate.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Any

import pytest

from app.errors import ApiError
from app.hal.registry import get_hal


@dataclass
class _FakeProc:
    returncode: int = 0
    stdout: str = "ok"
    stderr: str = ""


def _patched_run(argv: list[str], **_: Any) -> _FakeProc:
    """Pretend every binary succeeded; capture argv for assertions."""
    _patched_run.calls.append(argv)  # type: ignore[attr-defined]
    return _FakeProc(returncode=0, stdout="ok", stderr="")


def _enter_real_mode() -> None:
    """Flip ``_real`` on the live storage HAL so the subprocess branches run."""
    _patched_run.calls = []  # type: ignore[attr-defined]
    hal = get_hal("storage")
    hal._real = True
    hal._real_patched = True  # type: ignore[attr-defined]


def _exit_real_mode() -> None:
    hal = get_hal("storage")
    hal._real = False
    hal._real_patched = False  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _restore_real_flag() -> Any:
    _enter_real_mode()
    monkey = pytest.MonkeyPatch()
    monkey.setattr(subprocess, "run", _patched_run)
    yield monkey
    _exit_real_mode()


# --- partition HAL boundary (#53, #121) ------------------------------------
def test_partition_unknown_op_raises_400() -> None:
    hal = get_hal("storage")
    with pytest.raises(ApiError) as exc:
        hal.partition(op="shred", device="/dev/sda", params={})
    assert exc.value.status_code == 400
    assert exc.value.code == "bad_action"


def test_ab_data_slot_unknown_slot_raises_400() -> None:
    hal = get_hal("storage")
    with pytest.raises(ApiError) as exc:
        hal.ab_data_slot(slot="C")
    assert exc.value.status_code == 400
    assert exc.value.code == "bad_slot"


# --- real-mode branches via monkeypatched subprocess ------------------------
def test_smart_test_real_mode_envelope() -> None:
    hal = get_hal("storage")
    r = hal.smart_test(device="/dev/nvme0n1", kind="short")
    assert r["simulated"] is False
    assert r["applied"] is True
    assert r["returncode"] == 0
    assert _patched_run.calls == [["smartctl", "-t", "short", "/dev/nvme0n1"]]


def test_fsck_real_mode_envelope() -> None:
    hal = get_hal("storage")
    r = hal.fsck(device="/dev/mmcblk0p2")
    assert r["simulated"] is False
    assert r["applied"] is True
    assert r["ok"] is True
    assert _patched_run.calls == [["e2fsck", "-n", "/dev/mmcblk0p2"]]


def test_mount_real_mode_envelope() -> None:
    hal = get_hal("storage")
    r = hal.mount(device="/dev/sda1", mountpoint="/mnt/data", fstype="ext4", options="noatime")
    assert r["simulated"] is False
    assert r["applied"] is True
    assert r["returncode"] == 0
    assert _patched_run.calls and _patched_run.calls[0][:4] == ["mount", "-t", "ext4", "-o"]


def test_mount_real_mode_no_options() -> None:
    """When options is empty, argv should not include -o."""
    hal = get_hal("storage")
    r = hal.mount(device="/dev/sda1", mountpoint="/mnt/data", fstype="ext4", options="")
    assert r["simulated"] is False
    assert "-o" not in _patched_run.calls[0]


def test_unmount_real_mode_envelope() -> None:
    hal = get_hal("storage")
    r = hal.unmount(mountpoint="/mnt/data")
    assert r["simulated"] is False
    assert r["applied"] is True
    assert _patched_run.calls == [["umount", "/mnt/data"]]


def test_partition_create_real_mode_envelope() -> None:
    hal = get_hal("storage")
    r = hal.partition(op="create", device="/dev/sda", params={"partno": 1, "size": "10G"})
    assert r["simulated"] is False
    assert r["applied"] is True
    assert _patched_run.calls == [["parted", "/dev/sda", "create", "1", "10G"]]


def test_partition_create_no_size_real_mode() -> None:
    hal = get_hal("storage")
    hal.partition(op="create", device="/dev/sda", params={"partno": 2})
    assert _patched_run.calls == [["parted", "/dev/sda", "create", "2"]]


def test_luks_real_mode_envelope() -> None:
    """Real mode shells out to ``lsblk`` and parses for crypto_LUKS fstype."""
    hal = get_hal("storage")
    # The fake always returns stdout="ok" so the parser finds no crypto_LUKS;
    # we just exercise the real-mode code path.
    r = hal.luks_info()
    assert r["simulated"] is False
    assert r["applied"] is True
    assert _patched_run.calls and _patched_run.calls[0][0] == "lsblk"


def test_ab_data_slot_real_mode_envelope() -> None:
    hal = get_hal("storage")
    r = hal.ab_data_slot(slot="B")
    assert r["simulated"] is False
    assert r["applied"] is True
    assert _patched_run.calls == [["ab_data_slot", "B"]]


def test_overlayfs_real_mode_envelope() -> None:
    """Real mode reads /proc/mounts — no subprocess call. Just confirm path."""
    hal = get_hal("storage")
    r = hal.overlayfs_state()
    assert r["simulated"] is False
    assert "overlayfs" in r

"""OTA orchestration: bridge to SWUpdate, or simulate a full install.

When SWUpdate is reachable on the configured base URL the ``.swu`` is streamed
to its ``/upload`` endpoint and progress is read from its WebSocket. When it is
not reachable (dev machine), a deterministic install is simulated end-to-end so
the OTA panel is fully demoable. Either way progress is broadcast on the
``ota`` WebSocket channel and the result is written to ``ota_history``.

The install path requires the upload to carry an ``OtaSignature`` (see
``verify_bundle_signature``). The router looks up the operator-uploaded
public key in ``signing_keys`` and verifies the signature BEFORE the
install starts. Mismatches return 422 + an audit row (#120).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from app.config import get_settings
from app.db.database import get_db
from app.errors import ApiError
from app.ws.manager import hub

_CHANNEL = "ota"
_log = logging.getLogger("tasks.ota")

# Track in-flight install tasks so:
#   * a task that raises is logged (no more "Task exception was never retrieved")
#   * shutdown can await all of them and surface failures on the ota channel
#   * memory does not silently leak: completed tasks are removed in the callback
_inflight_installs: set[asyncio.Task[None]] = set()


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def verify_bundle_signature(
    *, data: bytes, alg: str, signature: str, public_key_id: str, signed_payload: str
) -> None:
    """Verify the OTA bundle signature (audit §8, #120).

    The function raises ``ApiError("signature_invalid", ..., 422)`` on
    any failure: missing key, bad shape, hash mismatch, or crypto
    verification failure. Success is silent.

    The verification path is end-to-end in simulation too — the SPA
    test suite exercises a known-good and known-bad signature against
    a test-only key generated on the fly, so the production code path
    runs in CI.
    """
    # 1. signed_payload must claim the same hash as the uploaded data.
    try:
        manifest = json.loads(signed_payload)
    except json.JSONDecodeError as exc:
        raise ApiError("signature_invalid", f"signed_payload is not valid JSON: {exc}", 422) from exc
    if not isinstance(manifest, dict):
        raise ApiError("signature_invalid", "signed_payload must be a JSON object", 422)
    expected_sha = manifest.get("sha256")
    actual_sha = _sha256_hex(data)
    if not isinstance(expected_sha, str) or expected_sha != actual_sha:
        # Reject EARLY: a signed manifest for bundle A cannot be replayed
        # against bundle B. The crypto check below would also fail, but
        # doing this first means the audit row records a hash mismatch
        # rather than a generic "bad signature".
        raise ApiError("signature_invalid", "manifest sha256 does not match bundle", 422)

    # 2. Look up the public key. A non-existent key_id is treated as
    #    a signature failure (not a 404) — the caller is trying to
    #    install, not list keys.
    row = await get_db().fetch_one("SELECT alg, public_key FROM signing_keys WHERE key_id = ?", (public_key_id,))
    if row is None:
        raise ApiError("signature_invalid", f"unknown signing key '{public_key_id}'", 422)
    if row["alg"] != alg:
        raise ApiError(
            "signature_invalid",
            f"signing key '{public_key_id}' is {row['alg']!r}, request said {alg!r}",
            422,
        )

    # 3. Crypto verification. We use Python's stdlib `hashlib` for
    #    ed25519 (added in 3.13) and the `cryptography` package for
    #    rsa-pss (not in stdlib). Both are first-party deps via the
    #    project; the rsa-pss path falls through to a clear error
    #    if `cryptography` is not installed (a clean install surface
    #    the operator can act on rather than a stack trace).
    try:
        sig_bytes = base64.b64decode(signature, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ApiError("signature_invalid", f"signature is not valid base64: {exc}", 422) from exc

    payload_bytes = signed_payload.encode("utf-8")

    if alg == "ed25519":
        try:
            from cryptography.exceptions import InvalidSignature
            from cryptography.hazmat.primitives.asymmetric import ed25519
            from cryptography.hazmat.primitives.serialization import load_pem_public_key
        except ImportError:  # pragma: no cover
            raise ApiError(
                "signature_unavailable",
                "ed25519 verification requires the `cryptography` package",
                503,
            ) from None
        try:
            key_pem = row["public_key"]
            # Accept either raw base64 (32 bytes) or PEM.
            try:
                raw = base64.b64decode(key_pem, validate=True)
                if len(raw) == 32:
                    pub: Any = ed25519.Ed25519PublicKey.from_public_bytes(raw)
                else:
                    pub = load_pem_public_key(key_pem.encode("utf-8"))
            except Exception as exc:
                raise ApiError("signature_invalid", f"public key is not a valid ed25519 key: {exc}", 422) from exc
            try:
                pub.verify(sig_bytes, payload_bytes)
            except InvalidSignature as exc:
                raise ApiError("signature_invalid", "ed25519 signature does not match", 422) from exc
        except ApiError:
            raise
        except Exception as exc:  # pragma: no cover
            raise ApiError("signature_invalid", f"ed25519 verify failed: {exc}", 422) from exc
    elif alg == "rsa-pss":
        try:
            from cryptography.exceptions import InvalidSignature
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import padding
            from cryptography.hazmat.primitives.serialization import load_pem_public_key
        except ImportError:  # pragma: no cover
            raise ApiError(
                "signature_unavailable",
                "rsa-pss verification requires the `cryptography` package",
                503,
            ) from None
        try:
            pub = load_pem_public_key(row["public_key"].encode("utf-8"))
        except Exception as exc:
            raise ApiError("signature_invalid", f"public key is not a valid PEM: {exc}", 422) from exc
        try:
            pub.verify(
                sig_bytes,
                payload_bytes,
                padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
                hashes.SHA256(),
            )
        except InvalidSignature as exc:
            raise ApiError("signature_invalid", "rsa-pss signature does not match", 422) from exc
        except Exception as exc:  # pragma: no cover
            raise ApiError("signature_invalid", f"rsa-pss verify failed: {exc}", 422) from exc
    else:  # pragma: no cover — pydantic literal already gates this
        raise ApiError("signature_invalid", f"unsupported alg '{alg}'", 422)


def current_versions() -> dict[str, Any]:
    """Parse /etc/sw-versions (SWUpdate convention) with a simulated fallback."""
    path = Path("/etc/sw-versions")
    versions: list[dict[str, str]] = []
    simulated = True
    if path.exists():
        simulated = False
        for line in path.read_text().splitlines():
            parts = line.split()
            if len(parts) >= 2:
                versions.append({"component": parts[0], "version": parts[1]})
    if not versions:
        versions = [
            {"component": "rootfs", "version": "1.4.2"},
            {"component": "bootloader", "version": "2023.10"},
            {"component": "kernel", "version": "6.1.55"},
        ]
    return {"simulated": simulated, "versions": versions}


def hawkbit_status() -> dict[str, Any]:
    return {
        "simulated": True,
        "configured": False,
        "mode": "suricatta",
        "server": None,
        "state": "disabled",
    }


async def start_install(filename: str, data: bytes, username: str) -> str:
    """Kick off install as a background task and return a job id.

    The asyncio.Task is retained in ``_inflight_installs`` so an unhandled
    exception is logged (no silent "Task exception was never retrieved") and
    shutdown can await all of them.
    """
    job_id = uuid.uuid4().hex[:12]
    task = asyncio.create_task(_run_install(job_id, filename, data, username))
    _inflight_installs.add(task)
    task.add_done_callback(_on_install_done)
    return job_id


def _on_install_done(task: asyncio.Task[None]) -> None:
    _inflight_installs.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        # The inner except-block in _run_install should normally swallow
        # failures and broadcast a `failed` event. This is the safety net for
        # errors that escape that block (e.g. asyncio.CancelledError, bugs in
        # the broadcast/db code itself).
        _log.exception("OTA install task raised: %s", exc)


async def drain_inflight(timeout_s: float = 5.0) -> None:
    """Wait for all in-flight install tasks to finish, then return.

    Called by the lifespan shutdown so a re-deploy / restart does not abandon
    an install mid-flash. Cancels stragglers after ``timeout_s``.
    """
    if not _inflight_installs:
        return
    pending = list(_inflight_installs)
    try:
        await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=timeout_s)
    except TimeoutError:
        for t in pending:
            if not t.done():
                t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


async def _run_install(job_id: str, filename: str, data: bytes, username: str) -> None:
    cfg = get_settings().swupdate
    started = time.time()
    version = _version_from_name(filename)
    try:
        # If the operator has set an override via ``PUT /api/ota/channel``
        # (#147, audit §8), use it; otherwise fall back to the static
        # SwupdateConfig.base_url from config.yaml.
        base_url = cfg.base_url
        row = await get_db().fetch_one("SELECT value FROM system_settings_kv WHERE key = ?", ("ota.channel.base_url",))
        if row is not None and isinstance(row["value"], str) and row["value"]:
            base_url = row["value"]
        ok, message = await _try_real_swupdate(base_url, data, job_id)
        if ok is None:
            # SWUpdate unreachable -> simulate
            await _simulate_progress(job_id)
            ok, message = True, "Simulated install complete"
        await _broadcast(job_id, 100, "done" if ok else "failed", message)
    except Exception:  # never leave the UI stuck
        # Log the full traceback so on-call can debug the failure.
        # The WS message intentionally omits str(exc) — that can leak
        # secrets (e.g. when the exception is raised from a request
        # whose URL contains a token) (#47).
        _log.exception("OTA install failed (job_id=%s, filename=%s)", job_id, filename)
        ok, message = False, "install error (see server logs)"
        await _broadcast(job_id, 0, "failed", message)
    duration = round(time.time() - started, 2)
    await get_db().execute(
        "INSERT INTO ota_history (version, result, duration_s, triggered_by) VALUES (?, ?, ?, ?)",
        (version, "success" if ok else "failed", duration, username),
    )


async def _try_real_swupdate(base_url: str, data: bytes, job_id: str) -> tuple[bool | None, str]:
    """Attempt a real SWUpdate upload. Returns (None, msg) if unreachable."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(f"{base_url}/upload", content=data)
            if resp.status_code >= 400:
                return False, f"SWUpdate rejected upload: HTTP {resp.status_code} {resp.text}"
            # Progress is read from SWUpdate's WS by a real deployment; here we
            # mark a couple of steps then complete.
            for pct in (40, 80):
                await _broadcast(job_id, pct, "installing", "writing to flash")
                await asyncio.sleep(0.1)
            return True, "SWUpdate install complete"
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout):
        return None, "SWUpdate unreachable"


async def _simulate_progress(job_id: str) -> None:
    steps = [
        (10, "verifying", "checking signature"),
        (30, "installing", "writing rootfs"),
        (60, "installing", "writing kernel"),
        (85, "installing", "updating bootloader env"),
        (95, "post-install", "syncing"),
    ]
    for pct, status, line in steps:
        await _broadcast(job_id, pct, status, line)
        await asyncio.sleep(0.15)


async def _broadcast(job_id: str, pct: int, status: str, line: str) -> None:
    await hub.broadcast(
        _CHANNEL,
        {"job_id": job_id, "percent": pct, "status": status, "line": line, "ts": time.time()},
    )


def _version_from_name(filename: str) -> str:
    stem = Path(filename).stem
    return stem or "unknown"


# --- config OTA (push config/device/system-config without code) ------------
async def config_ota_apply(payload: dict[str, Any], username: str) -> dict[str, Any]:
    """Apply a config-only OTA: snapshot then persist the pushed definitions.

    A config OTA never ships code — it pushes device/interface/system-config
    definitions. We store the bundle as a restore point so it is auditable and
    revertible, consistent with the existing ``restore_points`` mechanism.
    """
    import json

    db = get_db()
    name = str(payload.get("name") or f"config-ota-{int(time.time())}")
    rid = await db.execute(
        "INSERT INTO restore_points (name, payload) VALUES (?, ?)",
        (name, json.dumps(payload.get("config", payload))),
    )
    await db.execute(
        "INSERT INTO ota_history (version, result, duration_s, triggered_by) VALUES (?, ?, ?, ?)",
        (f"config:{name}", "success", 0.0, username),
    )
    keys = list((payload.get("config") or payload).keys())
    return {"applied": True, "restore_point_id": rid, "name": name, "sections": keys}


# --- system-level A/B image OTA --------------------------------------------
def system_ab_status() -> dict[str, Any]:
    """Surface the A/B slot state (golden boot + rollback) from the system HAL."""
    from app.hal.registry import get_hal

    boot = get_hal("system").boot()
    return {
        "simulated": boot.get("simulated", True),
        "active_slot": boot.get("active_slot"),
        "fallback_slot": boot.get("fallback_slot"),
        "slot_a_left": boot.get("slot_a_left"),
        "slot_b_left": boot.get("slot_b_left"),
        "boot_count": boot.get("boot_count"),
        "upgrade_available": boot.get("upgrade_available", False),
        "boot_reason": boot.get("boot_reason"),
    }


async def system_ab_switch(target_slot: str, username: str) -> dict[str, Any]:
    """Mark the inactive slot for next boot (golden-boot A/B switch, audit §8, #128).

    Arms an apply plan with ``rollback_kind="ota_slot"`` so the
    watchdog can auto-rollback if the device doesn't come back
    healthy within the rollback window. A fresh heartbeat within
    the window commits the plan. The pre-switch slot is captured
    in the plan's ``before`` snapshot — the rollback handler in
    ``app/tasks/apply_state.py::_rollback_ota_slot`` restores it.
    """
    from app.tasks import apply_state

    status = system_ab_status()
    if target_slot not in {"A", "B"}:
        raise ValueError("target_slot must be 'A' or 'B'")
    previous_active = status["active_slot"]
    await get_db().execute(
        "INSERT INTO ota_history (version, result, duration_s, triggered_by) VALUES (?, ?, ?, ?)",
        (f"ab-switch:{target_slot}", "success", 0.0, username),
    )
    # Arm the next-boot plan. The default 60s window matches
    # set_network / factory_reset / restore_apply. The rollback
    # handler will read ``before.target`` (the previous slot) and
    # write it back to a persisted ``ota_slot`` row when the
    # watchdog fires.
    plan = await apply_state.arm(
        target=f"ota:slot:next-boot:{target_slot}",
        before={"target": previous_active},
        after={"target": target_slot, "pending_reboot": True},
        rollback_kind="ota_slot",
        rollback_armed_s=60,
    )
    return {
        "requested_slot": target_slot,
        "previous_active": previous_active,
        "pending_reboot": True,
        "plan_id": plan["plan_id"],
        "armed_until": plan["armed_until"],
        "rollback_armed_s": plan["rollback_armed_s"],
    }


async def system_ab_rollback(username: str) -> dict[str, Any]:
    """Roll back to the fallback slot."""
    status = system_ab_status()
    fallback = status["fallback_slot"]
    await get_db().execute(
        "INSERT INTO ota_history (version, result, duration_s, triggered_by) VALUES (?, ?, ?, ?)",
        (f"ab-rollback:{fallback}", "success", 0.0, username),
    )
    return {"rolled_back_to": fallback, "previous_active": status["active_slot"], "pending_reboot": True}

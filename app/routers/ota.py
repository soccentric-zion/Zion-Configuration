"""OTA — dedicated section (section 8).

A distinct module for over-the-air updates, structurally separate from the
hardware, media, connectivity and system-admin sections:

- App-level OTA: update app + dashboard via a ``.swu`` (streamed to SWUpdate or
  simulated), progress on the ``/ws/ota`` channel. Each upload MUST carry a
  signed manifest (audit §8 "signed update chain", #120) — the signature is
  verified before the install starts.
- Config OTA: push device/config/system-config definitions without code changes.
- System-level OTA: A/B image updates with golden boot, switch and rollback.

Reuses the existing OTA task orchestration; destructive A/B actions require an
explicit confirmation and admin role.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Form, UploadFile

from app.auth.deps import CurrentUser, require_admin, require_viewer
from app.db import audit
from app.db.database import get_db
from app.errors import ApiError
from app.tasks import ota
from app.util.confirm import require_confirm
from app.util.models import (
    OtaChannelUpdate,
    OtaConfigPush,
    OtaRollback,
    OtaSignature,
    OtaSlotSwitch,
    RebootBody,
    RolloutStart,
    SigningKeyUpload,
)

router = APIRouter(prefix="/api/ota", tags=["ota"])


# --- OTA channel / server config (#147, audit §8) -------------------------
# Persisted in ``system_settings_kv`` under the ``ota.channel.`` prefix
# (per the #42 kv-store pattern). The static ``SwupdateConfig`` from
# ``app.config`` is the default; an operator-set row overrides it so
# the next auto-check + the next install use the new source.
_KV_CHANNEL_PREFIX = "ota.channel."


async def _kv_get(key: str) -> str | None:
    row = await get_db().fetch_one("SELECT value FROM system_settings_kv WHERE key = ?", (key,))
    if row is None:
        return None
    val = row["value"]
    return val if isinstance(val, str) else None


async def _kv_set(key: str, value: str) -> None:
    await get_db().execute(
        """
        INSERT INTO system_settings_kv (key, value, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (key, value),
    )


async def _effective_channel_config() -> dict[str, Any]:
    """Return the effective OTA channel config — persisted overrides win
    over the static ``SwupdateConfig`` defaults (#42)."""
    from app.config import get_settings

    cfg = get_settings().swupdate
    base_url_row = await _kv_get(f"{_KV_CHANNEL_PREFIX}base_url")
    channel_row = await _kv_get(f"{_KV_CHANNEL_PREFIX}channel")
    signing_row = await _kv_get(f"{_KV_CHANNEL_PREFIX}signing_key_id")
    auto_check_row = await _kv_get(f"{_KV_CHANNEL_PREFIX}auto_check")
    return {
        "base_url": base_url_row or cfg.base_url,
        "channel": channel_row or "stable",
        "signing_key_id": signing_row or "default",
        "auto_check": (auto_check_row == "1") if auto_check_row is not None else True,
        "default": (base_url_row is None and channel_row is None and signing_row is None and auto_check_row is None),
    }


@router.get("/status")
async def status(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    versions = ota.current_versions()
    return {
        "versions": versions["versions"],
        "simulated": versions["simulated"],
        "system": ota.system_ab_status(),
        "hawkbit": ota.hawkbit_status(),
    }


@router.get("/history")
async def history(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    rows = await get_db().fetch_all("SELECT * FROM ota_history ORDER BY ts DESC, id DESC LIMIT 50")
    return {"history": rows}


# --- app-level OTA ---------------------------------------------------------
@router.post("/app")
async def app_update(
    file: UploadFile,
    signature: str = Form(...),
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """Upload + verify + install a .swu bundle (audit §8, #120).

    The signature arrives as a form field (multipart) so the binary
    upload stays one stream. ``signature`` is the JSON-encoded
    ``OtaSignature`` body — the pydantic model rejects typos
    (``extra="forbid"``) and the router then verifies the signature
    against the bundle's SHA256. A failure at any step is 422 +
    audit + NO install.
    """
    data = await file.read()
    # Parse the signature form field. pydantic 422s on a bad shape.
    try:
        sig_body = OtaSignature.model_validate_json(signature)
    except Exception as exc:  # pydantic.ValidationError or JSONDecodeError
        # Record the rejection attempt — same code path the SPA error
        # toast reports. We deliberately do NOT include the raw
        # exception text in the audit row (it can be very long and
        # noisy); the structured signature_invalid code is enough.
        await audit.record(
            username=user.username,
            ip=user.ip,
            action="ota_signature_rejected",
            target=file.filename,
            new_value="bad_signature_shape",
        )
        raise ApiError("signature_invalid", f"signature body is malformed: {exc}", 422) from exc
    try:
        await ota.verify_bundle_signature(
            data=data,
            alg=sig_body.alg,
            signature=sig_body.signature,
            public_key_id=sig_body.public_key_id,
            signed_payload=sig_body.signed_payload,
        )
    except ApiError as exc:
        # Audit the rejection — distinct action for the hash-mismatch
        # case so the operator can spot a replay attempt in the log.
        action = "ota_signature_rejected"
        if exc.message.startswith("manifest sha256"):
            action = "ota_signature_hash_mismatch"
        await audit.record(
            username=user.username,
            ip=user.ip,
            action=action,
            target=file.filename,
            new_value=f"key_id={sig_body.public_key_id}:alg={sig_body.alg}",
        )
        raise
    # Signature OK — kick off the install.
    job_id = await ota.start_install(file.filename or "update.swu", data, user.username)
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="ota_app_update",
        target=file.filename,
        new_value=f"{len(data)} bytes:key_id={sig_body.public_key_id}",
    )
    return {"job_id": job_id, "filename": file.filename, "size": len(data)}


# --- signing-key management (audit §8, #120) -------------------------------
@router.get("/signing-keys")
async def list_signing_keys(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """List operator-uploaded public keys (no secret material in payload)."""
    rows = await get_db().fetch_all(
        "SELECT key_id, alg, label, created_at FROM signing_keys ORDER BY created_at DESC, key_id"
    )
    return {"keys": rows}


@router.post("/signing-keys")
async def upload_signing_key(body: SigningKeyUpload, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Upload a public key (admin). The private key never leaves the
    signing host; only the public material is stored."""
    await get_db().execute(
        """INSERT INTO signing_keys (key_id, alg, public_key, label)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(key_id) DO UPDATE SET
             alg = excluded.alg,
             public_key = excluded.public_key,
             label = excluded.label""",
        (body.key_id, body.alg, body.public_key, body.label),
    )
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="signing_key_upload",
        target=body.key_id,
        new_value=f"alg={body.alg}",
    )
    return {"ok": True, "key_id": body.key_id}


@router.delete("/signing-keys/{key_id}")
async def delete_signing_key(key_id: str, user: CurrentUser = Depends(require_admin)) -> dict[str, bool]:
    await get_db().execute("DELETE FROM signing_keys WHERE key_id = ?", (key_id,))
    await audit.record(username=user.username, ip=user.ip, action="signing_key_delete", target=key_id)
    return {"ok": True}


# --- config OTA ------------------------------------------------------------
@router.post("/config")
async def config_update(
    body: OtaConfigPush | None = None,
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """Push a config-only OTA bundle (#87, audit §8 / Guardrail 1).

    The body is validated by the ``OtaConfigPush`` pydantic model
    (``extra="forbid"`` via ``_StrictModel``): unknown keys,
    non-semver ``version``, or an out-of-vocabulary ``kind`` all
    surface as 422 *before* anything lands in the database. The
    ``body | None = None`` pattern keeps the role-guard 403 ahead of
    the pydantic 422 for empty POSTs (per CLAUDE.md Do).
    """
    assert body is not None  # pydantic guarantees a body for valid requests
    result = await ota.config_ota_apply(body.model_dump(), user.username)
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="ota_config_push",
        target=result.get("name"),
        new_value=f"kind={body.kind}:version={body.version}",
    )
    return result


@router.get("/channel")
async def get_channel(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """Return the effective OTA channel + SWUpdate server config (#147, audit §8).

    Layered on top of ``SwupdateConfig`` defaults — persisted
    overrides win so the SPA and the audit log show the *actual*
    effective source the next install will use (#42).
    """
    return await _effective_channel_config()


@router.put("/channel")
async def put_channel(
    body: OtaChannelUpdate,
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """Persist operator-set OTA channel + SWUpdate server config (#147, audit §8).

    All four fields are written. The body is validated by
    ``OtaChannelUpdate``: ``base_url`` is an http(s) URL, ``channel``
    is one of ``stable | beta | nightly``, ``auto_check`` is a bool,
    ``signing_key_id`` matches ``[A-Za-z0-9_-]+``. ``extra="forbid"``
    rejects unknown keys. The router enforces ``confirm: true``
    (privileged write — 428 without it).
    """
    require_confirm(body.model_dump())
    written: list[str] = []
    await _kv_set(f"{_KV_CHANNEL_PREFIX}base_url", body.base_url)
    written.append(f"base_url={body.base_url}")
    await _kv_set(f"{_KV_CHANNEL_PREFIX}channel", body.channel)
    written.append(f"channel={body.channel}")
    await _kv_set(f"{_KV_CHANNEL_PREFIX}signing_key_id", body.signing_key_id)
    written.append(f"signing_key_id={body.signing_key_id}")
    await _kv_set(f"{_KV_CHANNEL_PREFIX}auto_check", "1" if body.auto_check else "0")
    written.append(f"auto_check={body.auto_check}")
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="ota_channel_update",
        target="channel",
        new_value=",".join(written),
    )
    return {"ok": True, "applied": written}


# --- staged rollout (#146, audit §8) ---------------------------------------
# One row per stage of an in-flight rollout; the active stage is the
# one whose ``status='active'``. ``advance`` flips the active row to
# ``completed`` and promotes the next pending row to ``active``;
# ``abort`` flips every row of the rollout to ``aborted``.


async def _active_stage(update_id: str) -> dict[str, Any] | None:
    row = await get_db().fetch_one(
        "SELECT * FROM rollout_stages WHERE update_id = ? AND status = 'active' LIMIT 1",
        (update_id,),
    )
    return row


async def _next_pending_stage(update_id: str) -> dict[str, Any] | None:
    row = await get_db().fetch_one(
        "SELECT * FROM rollout_stages WHERE update_id = ? AND status = 'pending' "
        "ORDER BY percent ASC, id ASC LIMIT 1",
        (update_id,),
    )
    return row


@router.post("/rollout/start")
async def rollout_start(
    body: RolloutStart,
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """Begin a staged rollout (#146, audit §8).

    Inserts one row per stage in ``rollout_stages`` for this
    ``update_id``, all in ``pending`` status except the first which
    is set to ``active`` (the rollout pipeline always starts at
    the lowest percent). Re-running for an already-existing
    ``update_id`` is rejected with 409 so two operators don't
    race-insert stages for the same update.
    """
    require_confirm(body.model_dump())
    existing = await get_db().fetch_one(
        "SELECT id FROM rollout_stages WHERE update_id = ? LIMIT 1",
        (body.update_id,),
    )
    if existing is not None:
        raise ApiError(
            "rollout_exists",
            f"Rollout for update_id '{body.update_id}' already exists; abort it first",
            409,
        )
    stage_ids: list[int] = []
    for idx, pct in enumerate(body.stages):
        rid = await get_db().execute(
            """INSERT INTO rollout_stages (update_id, percent, status, started_at)
               VALUES (?, ?, ?, ?)""",
            (
                body.update_id,
                int(pct),
                "active" if idx == 0 else "pending",
                datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S") if idx == 0 else None,
            ),
        )
        stage_ids.append(rid)
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="ota_rollout_start",
        target=body.update_id,
        new_value="stages=" + ",".join(str(p) for p in body.stages),
    )
    return {
        "ok": True,
        "update_id": body.update_id,
        "stages": [{"percent": int(p), "status": "active" if i == 0 else "pending"} for i, p in enumerate(body.stages)],
        "stage_ids": stage_ids,
    }


@router.get("/rollout/status")
async def rollout_status(
    update_id: str = "default",
    _: CurrentUser = Depends(require_viewer),
) -> dict[str, Any]:
    """Return the rollout state for ``update_id`` (#146, audit §8).

    If no rollout exists, returns ``{"exists": False, "current_percent": None}``.
    The active row carries the current cohort percent.
    """
    rows = await get_db().fetch_all(
        "SELECT id, percent, status, started_at, completed_at FROM rollout_stages "
        "WHERE update_id = ? ORDER BY percent ASC, id ASC",
        (update_id,),
    )
    if not rows:
        return {"exists": False, "update_id": update_id, "current_percent": None, "stages": []}
    active = next((r for r in rows if r["status"] == "active"), None)
    return {
        "exists": True,
        "update_id": update_id,
        "current_percent": active["percent"] if active else None,
        "stages": [
            {
                "percent": r["percent"],
                "status": r["status"],
                "started_at": r["started_at"],
                "completed_at": r["completed_at"],
            }
            for r in rows
        ],
    }


@router.post("/rollout/advance")
async def rollout_advance(
    body: dict[str, Any],
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """Advance the rollout to the next stage (#146, audit §8).

    In simulation there's no real fleet health signal, so the
    operator advances manually. The active row transitions to
    ``completed``; the next pending row (lowest percent) becomes
    ``active``. If no pending row remains, the rollout is complete
    (returns ``{"complete": True}``).
    """
    require_confirm(body)
    update_id = str(body.get("update_id", "default"))
    active = await _active_stage(update_id)
    if active is None:
        raise ApiError("no_active_stage", f"No active rollout stage for '{update_id}'", 409)
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    await get_db().execute(
        "UPDATE rollout_stages SET status = 'completed', completed_at = ? WHERE id = ?",
        (now, active["id"]),
    )
    nxt = await _next_pending_stage(update_id)
    advanced = False
    if nxt is not None:
        await get_db().execute(
            "UPDATE rollout_stages SET status = 'active', started_at = ? WHERE id = ?",
            (now, nxt["id"]),
        )
        advanced = True
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="ota_rollout_advance",
        target=update_id,
        new_value=f"from={active['percent']}:to={nxt['percent'] if nxt else 'complete'}",
    )
    return {
        "ok": True,
        "advanced": advanced,
        "complete": not advanced,
        "from_percent": active["percent"],
        "to_percent": nxt["percent"] if nxt else None,
    }


@router.post("/rollout/abort")
async def rollout_abort(
    body: dict[str, Any],
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """Abort an in-flight rollout (#146, audit §8).

    Every row of the rollout (pending + active) flips to ``aborted``.
    A new rollout for the same ``update_id`` can be started
    afterwards (the previous rows are kept for the audit trail).
    """
    require_confirm(body)
    update_id = str(body.get("update_id", "default"))
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    # Don't trust ``Database.execute()``'s return value to count rows
    # — it returns lastrowid, not rowcount (CLAUDE.md Don't #54).
    # SELECT first so we can report the actual affected count.
    to_abort = await get_db().fetch_all(
        "SELECT id FROM rollout_stages WHERE update_id = ? AND status IN ('pending', 'active')",
        (update_id,),
    )
    for r in to_abort:
        await get_db().execute(
            "UPDATE rollout_stages SET status = 'aborted', completed_at = ? WHERE id = ?",
            (now, r["id"]),
        )
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="ota_rollout_abort",
        target=update_id,
        new_value=f"rows={len(to_abort)}",
    )
    return {"ok": True, "update_id": update_id, "aborted_rows": len(to_abort)}


# --- system-level A/B OTA --------------------------------------------------
@router.get("/system")
async def system(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return ota.system_ab_status()


@router.post("/system/switch")
async def system_switch(
    body: OtaSlotSwitch | None = None, user: CurrentUser = Depends(require_admin)
) -> dict[str, Any]:
    # ``body`` is optional so an empty POST returns the role-guard 403
    # first; the pydantic 422 only fires once the body is present (Guardrail 1, #97).
    if body is None or not body.confirm:
        raise ApiError("confirm_required", "Switching the boot slot requires explicit confirmation", 428)
    try:
        result = await ota.system_ab_switch(body.slot, user.username)
    except ValueError as exc:
        raise ApiError("bad_slot", str(exc), 400) from exc
    await audit.record(username=user.username, ip=user.ip, action="ota_ab_switch", target=body.slot)
    return result


@router.post("/system/rollback")
async def system_rollback(
    body: OtaRollback | None = None, user: CurrentUser = Depends(require_admin)
) -> dict[str, Any]:
    if body is None or not body.confirm:
        raise ApiError("confirm_required", "Rolling back the system image requires explicit confirmation", 428)
    result = await ota.system_ab_rollback(user.username)
    await audit.record(
        username=user.username, ip=user.ip, action="ota_ab_rollback", target=result.get("rolled_back_to")
    )
    return result


@router.post("/reboot")
async def reboot(body: RebootBody | None = None, user: CurrentUser = Depends(require_admin)) -> dict[str, bool]:
    if body is None or not body.confirm:
        raise ApiError("confirm_required", "Reboot requires explicit confirmation", 428)
    await audit.record(
        username=user.username, ip=user.ip, action="ota_reboot", new_value="now" if body.now else "later"
    )
    return {"ok": True}

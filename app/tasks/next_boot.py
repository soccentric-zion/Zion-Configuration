"""Next-boot staging for hardware-touching writes (audit §3, §4, §10, #114).

The audit requires that writes which can disturb running hardware (interface
enable/disable, media block enable, device enable, service start/stop) can
be staged for "apply on next boot" instead of going live immediately. The
write path:

    1. ``arm(target, kind, payload, username)`` — appends a row to
       ``pending_overlays`` with status ``armed``. The router mirrors
       this to the live table on the ``live`` path, or skips the live
       write on the ``next-boot`` path.
    2. ``commit_pending()`` — boot-time hook (POST
       /api/apply/pending/commit). Applies every ``armed`` row to its
       live table in one transaction, marks them ``committed``, and
       returns the list. On real hardware this is called by the
       first-boot script; in simulation the SPA calls it after a
       reboot.
    3. ``discard(id)`` — operator can drop a single staged change.

The watchdog that drives ``commit`` / ``rollback`` for ``apply_plans``
does not apply here — pending_overlays are operator-visible and the
SPA is the trigger. The audit_log carries every arm/commit/discard
action so the who/when is recoverable.
"""

from __future__ import annotations

import json
from typing import Any

from app.db.audit import record as audit_record
from app.db.database import get_db

# The set of (kind -> live-table / column / apply-fn) routing used by
# ``commit_pending``. Each entry is the SQL the commit hook runs to
# materialize the staged change to the live table. New kinds must be
# added here AND in the CHECK constraint on pending_overlays.kind.
# The SQL uses positional ? placeholders; the order matches the args
# passed to ``_apply_to_live`` below: (target_str_or_int, int_value).
_COMMIT_SQL: dict[str, str] = {
    "interface_enable": ("UPDATE hw_interfaces SET enabled = ?, updated_at = datetime('now') " "WHERE name = ?"),
    "media_enable": ("UPDATE media_blocks SET enabled = ?, updated_at = datetime('now') " "WHERE name = ?"),
    "device_enable": ("UPDATE devices SET enabled = ? WHERE id = ?"),
    "service_enable": ("UPDATE hw_interfaces SET enabled = ?, updated_at = datetime('now') " "WHERE name = ?"),
    "service_disable": ("UPDATE hw_interfaces SET enabled = ?, updated_at = datetime('now') " "WHERE name = ?"),
}


async def arm(
    *,
    target: str,
    kind: str,
    payload: dict[str, Any],
    username: str,
) -> int:
    """Stage a hardware-touching change for the next boot.

    Returns the new ``pending_overlays.id``. The router typically writes
    to the live table at the same time (``live`` path) and only calls
    ``arm`` to record the intent for the audit trail. The ``next-boot``
    path skips the live write and relies on ``commit_pending()`` to
    apply it on the next boot.
    """
    if kind not in _COMMIT_SQL:
        raise ValueError(f"unsupported pending_overlay kind: {kind!r}")
    rid = await get_db().execute(
        "INSERT INTO pending_overlays (target, kind, payload_json, armed_by) " "VALUES (?, ?, ?, ?)",
        (target, kind, json.dumps(payload), username),
    )
    await audit_record(
        username=username,
        ip="127.0.0.1",
        action=f"pending_arm:{kind}",
        target=target,
        new_value=json.dumps(payload),
    )
    return int(rid)


async def list_pending() -> list[dict[str, Any]]:
    """Return every pending_overlay row, newest first."""
    rows = await get_db().fetch_all("SELECT * FROM pending_overlays ORDER BY id DESC")
    return [dict(r) for r in rows]


async def list_armed() -> list[dict[str, Any]]:
    """Return only ``armed`` rows (what the boot-time commit will apply)."""
    rows = await get_db().fetch_all("SELECT * FROM pending_overlays WHERE status = 'armed' ORDER BY id ASC")
    return [dict(r) for r in rows]


async def discard(id: int, *, username: str) -> dict[str, Any]:
    """Mark a single armed row as ``discarded`` (operator drop)."""
    row = await get_db().fetch_one("SELECT * FROM pending_overlays WHERE id = ?", (id,))
    if row is None:
        raise ValueError(f"unknown pending_overlay id {id}")
    if row["status"] != "armed":
        raise ValueError(f"pending_overlay {id} is {row['status']!r}, not 'armed' — refusing to discard")
    await get_db().execute(
        "UPDATE pending_overlays SET status = 'discarded' WHERE id = ?",
        (id,),
    )
    await audit_record(
        username=username,
        ip="127.0.0.1",
        action="pending_discard",
        target=f"{row['target']} ({row['kind']})",
    )
    return {"id": id, "status": "discarded"}


async def commit_pending(*, username: str) -> dict[str, Any]:
    """Apply every ``armed`` row to its live table.

    Boot-time hook. Returns the list of (id, kind, target, value) the
    hook applied. Each row's status transitions to ``committed`` and
    ``committed_at`` is set. Unknown / malformed rows are recorded in
    the response but the rest still apply — partial success beats a
    hard failure that strands the device at boot.
    """
    armed = await list_armed()
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for row in armed:
        kind = row["kind"]
        target = row["target"]
        try:
            payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
        except (TypeError, ValueError):
            skipped.append({"id": row["id"], "kind": kind, "target": target, "reason": "bad payload_json"})
            continue
        if kind not in _COMMIT_SQL:
            skipped.append({"id": row["id"], "kind": kind, "target": target, "reason": "unknown kind"})
            continue
        value = payload.get("enabled", payload.get("value"))
        if not isinstance(value, bool):
            skipped.append(
                {"id": row["id"], "kind": kind, "target": target, "reason": "payload missing 'enabled' bool"}
            )
            continue
        sql = _COMMIT_SQL[kind]
        # Param order: (value, target). For device_enable, target is
        # an integer; for the rest it's the surface name string.
        try:
            if kind == "device_enable":
                target_id = int(target)
            else:
                target_id = target
        except ValueError:
            skipped.append(
                {"id": row["id"], "kind": kind, "target": target, "reason": "target not int for device_enable"}
            )
            continue
        try:
            await get_db().execute(sql, (1 if value else 0, target_id))
        except Exception as exc:  # noqa: BLE001 — keep going, log, audit
            skipped.append({"id": row["id"], "kind": kind, "target": target, "reason": f"apply failed: {exc}"})
            continue
        await get_db().execute(
            "UPDATE pending_overlays SET status = 'committed', committed_at = datetime('now') " "WHERE id = ?",
            (row["id"],),
        )
        applied.append({"id": row["id"], "kind": kind, "target": target, "value": value})
    await audit_record(
        username=username,
        ip="127.0.0.1",
        action="pending_commit",
        target="pending_overlays",
        new_value=json.dumps({"applied": len(applied), "skipped": len(skipped)}),
    )
    return {"applied": applied, "skipped": skipped, "applied_count": len(applied)}

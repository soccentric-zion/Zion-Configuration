"""Apply-plan orchestration for destructive state changes.

Some privileged actions can lock out the host (notably
``set_network``: a bad static IP / mask can take the box off the network
permanently). req.md mandates:

    "Apply changes in a way consistent with the app's existing
     rollback/error-handling behavior; never leave an interface or
     firewall in a half-applied state."

This module implements a small two-phase plan store:

1. The handler that wants to mutate state (e.g. ``set_network``) calls
   :func:`arm` *after* it has applied the change. ``arm`` records a
   snapshot of the *before* state and an ``armed_until`` deadline; the
   plan is persisted in the ``apply_plans`` table.
2. The dashboard either confirms the plan within the window
   (the SPA calls ``POST /api/settings/network/{iface}/apply/confirm``
   once the host is reachable with the new config), or the user can
   manually rollback via ``POST .../apply/rollback``.
3. A periodic background :class:`Watchdog` scans the table for plans
   whose ``armed_until`` has passed and fires rollback automatically —
   *unless* a recent heartbeat was recorded (the device checked in
   healthy with the new config), in which case the plan auto-commits
   (#93, audit Guardrail 2). The heartbeat lives in the
   ``system_heartbeat`` table and is set by
   ``POST /api/system/health/heartbeat``.

Rollback targets are dispatched by ``rollback_kind`` (currently only
``net_interfaces``; future: ``firewall_rules``). Each kind knows how to
restore a row from a JSON snapshot.

The plan store is best-effort: a power loss after ``arm`` and before
``commit`` will leave the system in the new state. That is a deliberate
trade-off — the alternative (synchronous atomic update) requires
two-phase commit support that the underlying network tools do not
provide. The point of the plan is to be reversible from inside the
application: as long as the dashboard can still reach the host, the
user can rollback.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.db import audit
from app.db.database import get_db
from app.errors import ApiError


@dataclass
class ApplyPlan:
    id: int
    target: str
    before: dict[str, Any]
    after: dict[str, Any]
    rollback_kind: str
    armed_until: str
    status: str


def _row_to_plan(row: dict[str, Any]) -> ApplyPlan:
    return ApplyPlan(
        id=int(row["id"]),
        target=row["target"],
        before=json.loads(row["before_json"]),
        after=json.loads(row["after_json"]),
        rollback_kind=row["rollback_kind"],
        armed_until=row["armed_until"],
        status=row["status"],
    )


# --- arm / commit / rollback -----------------------------------------------
async def arm(
    target: str,
    before: dict[str, Any],
    after: dict[str, Any],
    rollback_kind: str,
    rollback_armed_s: int,
) -> dict[str, Any]:
    """Record an applied change with a rollback window.

    Returns the persisted plan (id + armed_until) for the response body.
    """
    armed_until = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() + rollback_armed_s))
    plan_id = await get_db().execute(
        """INSERT INTO apply_plans
           (target, before_json, after_json, rollback_kind, armed_until, status)
           VALUES (?, ?, ?, ?, ?, 'armed')""",
        (
            target,
            json.dumps(before),
            json.dumps(after),
            rollback_kind,
            armed_until,
        ),
    )
    return {"plan_id": plan_id, "armed_until": armed_until, "rollback_armed_s": rollback_armed_s}


async def commit(plan_id: int) -> dict[str, bool]:
    row = await get_db().fetch_one("SELECT * FROM apply_plans WHERE id = ?", (plan_id,))
    if row is None:
        raise ApiError("plan_not_found", f"Unknown apply plan {plan_id}", 404)
    if row["status"] != "armed":
        raise ApiError(
            "plan_not_armed",
            f"Apply plan {plan_id} is in status '{row['status']}' and cannot be committed",
            409,
        )
    await get_db().execute(
        "UPDATE apply_plans SET status = 'committed', committed_at = datetime('now') WHERE id = ?",
        (plan_id,),
    )
    return {"ok": True, "committed": True}


async def rollback(
    plan_id: int,
    reason: str = "manual",
    *,
    username: str = "system",
    ip: str = "127.0.0.1",
) -> dict[str, bool]:
    """Roll back a previously-armed apply plan.

    ``username``/``ip`` identify who triggered the rollback. The watchdog
    passes the defaults (system / 127.0.0.1) since no human is involved;
    a manual router call passes the requesting admin so the audit log
    reflects the actual operator, not "system" (#48).
    """
    row = await get_db().fetch_one("SELECT * FROM apply_plans WHERE id = ?", (plan_id,))
    if row is None:
        raise ApiError("plan_not_found", f"Unknown apply plan {plan_id}", 404)
    if row["status"] not in {"armed", "expired"}:
        raise ApiError(
            "plan_not_armed",
            f"Apply plan {plan_id} is in status '{row['status']}' and cannot be rolled back",
            409,
        )
    plan = _row_to_plan(row)
    await _apply_rollback(plan)
    await get_db().execute(
        "UPDATE apply_plans SET status = 'rolled_back', rolled_back_at = datetime('now') WHERE id = ?",
        (plan_id,),
    )
    await audit.record(
        username=username,
        ip=ip,
        action="apply_rollback",
        target=plan.target,
        new_value=reason,
    )
    return {"ok": True, "rolled_back": True}


async def get(plan_id: int) -> ApplyPlan:
    row = await get_db().fetch_one("SELECT * FROM apply_plans WHERE id = ?", (plan_id,))
    if row is None:
        raise ApiError("plan_not_found", f"Unknown apply plan {plan_id}", 404)
    return _row_to_plan(row)


# --- bundles: atomic multi-section staging (#99) ---------------------------
# A bundle is a group of apply_plans sharing the same ``bundle_id`` and
# ``armed_until``. The watchdog treats a bundle atomically: if any plan
# in the bundle fails to commit (e.g. heartbeat stale), every plan in
# the bundle is rolled back. /api/apply/bundle creates / commits /
# rolls back bundles via these helpers.


def _new_bundle_id() -> str:
    """Generate a fresh bundle id. Random UUID4 hex; the column is TEXT."""
    return uuid.uuid4().hex


async def arm_bundle(
    items: list[dict[str, Any]],
    rollback_armed_s: int,
    bundle_id: str | None = None,
) -> dict[str, Any]:
    """Arm a list of (target, before, after, rollback_kind) plans under
    a shared ``bundle_id`` and ``armed_until`` (#99).

    Returns the bundle_id and the list of plan_ids. If any individual
    arm fails the whole bundle is rolled back so the caller never sees
    a half-armed bundle.
    """
    if not items:
        raise ApiError("empty_bundle", "A bundle must contain at least one plan", 400)
    bid = bundle_id or _new_bundle_id()
    armed_until = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() + rollback_armed_s))
    plan_ids: list[int] = []
    try:
        for item in items:
            pid = await get_db().execute(
                """INSERT INTO apply_plans
                   (target, before_json, after_json, rollback_kind, armed_until, status, bundle_id)
                   VALUES (?, ?, ?, ?, ?, 'armed', ?)""",
                (
                    item["target"],
                    json.dumps(item["before"]),
                    json.dumps(item["after"]),
                    item["rollback_kind"],
                    armed_until,
                    bid,
                ),
            )
            plan_ids.append(pid)
    except Exception:
        # Atomicity: best-effort rollback any plans that did get
        # inserted so a partial bundle never lingers.
        for pid in plan_ids:
            with contextlib.suppress(Exception):
                await get_db().execute(
                    "UPDATE apply_plans SET status = 'rolled_back', rolled_back_at = datetime('now') WHERE id = ?",
                    (pid,),
                )
        raise
    return {"bundle_id": bid, "armed_until": armed_until, "plan_ids": plan_ids, "rollback_armed_s": rollback_armed_s}


async def commit_bundle(
    bundle_id: str,
    *,
    username: str = "system",
    ip: str = "127.0.0.1",
) -> dict[str, Any]:
    """Commit every armed plan in a bundle. Atomic: a plan already in
    'committed' or 'rolled_back' is treated as no-op; if any plan is
    missing we record the failure and continue, so partial commits
    surface visibly (#99, #48)."""
    rows = await get_db().fetch_all("SELECT id, status FROM apply_plans WHERE bundle_id = ?", (bundle_id,))
    if not rows:
        raise ApiError("bundle_not_found", f"Unknown bundle {bundle_id}", 404)
    committed: list[int] = []
    for row in rows:
        if row["status"] != "armed":
            continue
        await get_db().execute(
            "UPDATE apply_plans SET status = 'committed', committed_at = datetime('now') WHERE id = ?",
            (row["id"],),
        )
        committed.append(int(row["id"]))
    await audit.record(
        username=username,
        ip=ip,
        action="apply_bundle_commit",
        target=bundle_id,
        new_value=",".join(str(i) for i in committed) if committed else None,
    )
    return {"ok": True, "bundle_id": bundle_id, "committed": committed}


async def rollback_bundle(
    bundle_id: str,
    reason: str = "manual",
    *,
    username: str = "system",
    ip: str = "127.0.0.1",
) -> dict[str, Any]:
    """Roll back every plan in a bundle, applying each handler in turn.
    A handler failure on one plan is logged and the loop continues so
    the rest of the bundle is still restored (#99)."""
    rows = await get_db().fetch_all(
        "SELECT * FROM apply_plans WHERE bundle_id = ? AND status IN ('armed', 'expired')", (bundle_id,)
    )
    if not rows:
        raise ApiError("bundle_not_found_or_done", f"No active plans in bundle {bundle_id}", 404)
    rolled: list[int] = []
    for row in rows:
        try:
            plan = _row_to_plan(row)
            await _apply_rollback(plan)
            await get_db().execute(
                "UPDATE apply_plans SET status = 'rolled_back', rolled_back_at = datetime('now') WHERE id = ?",
                (plan.id,),
            )
            rolled.append(plan.id)
        except ApiError:
            # Handler missing / row gone — skip and continue so the rest
            # of the bundle is still restored.
            continue
    await audit.record(
        username=username,
        ip=ip,
        action="apply_bundle_rollback",
        target=bundle_id,
        new_value=reason,
    )
    return {"ok": True, "bundle_id": bundle_id, "rolled_back": rolled}


async def bundle_plans(bundle_id: str) -> list[ApplyPlan]:
    rows = await get_db().fetch_all("SELECT * FROM apply_plans WHERE bundle_id = ? ORDER BY id", (bundle_id,))
    return [_row_to_plan(r) for r in rows]


# --- heartbeat: device checked in healthy with new config (Guardrail 2) ----
async def record_heartbeat(source: str | None = None) -> dict[str, str]:
    """Stamp the system_heartbeat table with the current time.

    Called from ``POST /api/system/health/heartbeat``. Upserts the
    single row (id=1) so a fresh DB and a long-running DB end up with
    one row each. The watchdog reads ``last_seen_at`` to decide
    auto-commit vs. auto-rollback (#93).
    """
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    await get_db().execute(
        """INSERT INTO system_heartbeat (id, last_seen_at, source)
           VALUES (1, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
               last_seen_at = excluded.last_seen_at,
               source = excluded.source""",
        (now_iso, source),
    )
    return {"last_seen_at": now_iso, "source": source or ""}


async def last_heartbeat() -> dict[str, Any] | None:
    row = await get_db().fetch_one("SELECT last_seen_at, source FROM system_heartbeat WHERE id = 1")
    if row is None:
        return None
    return {"last_seen_at": row["last_seen_at"], "source": row.get("source")}


async def heartbeat_recent(threshold_s: int) -> bool:
    """True iff the last heartbeat is within ``threshold_s`` of now.

    Used by :class:`ApplyPlanWatchdog` to distinguish "device rebooted
    fine and called back" (auto-commit) from "device never came back"
    (auto-rollback). The threshold is supplied by the watchdog so it
    stays aligned with the rolling ``armed_until`` window.
    """
    row = await get_db().fetch_one("SELECT last_seen_at FROM system_heartbeat WHERE id = 1")
    if row is None:
        return False
    last = row["last_seen_at"]
    # system_heartbeat.last_seen_at is stored as datetime('now')
    # (UTC, second-granular); compare against the same shape so the
    # subtraction is well-defined.
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    fmt = "%Y-%m-%dT%H:%M:%S"
    try:
        last_t = time.strptime(last, fmt)
        now_t = time.strptime(now_iso, fmt)
    except ValueError:
        return False
    return (time.mktime(now_t) - time.mktime(last_t)) <= threshold_s


# --- rollback dispatch -----------------------------------------------------
# Each ``rollback_kind`` registers a handler that restores state from the
# ``before`` snapshot. A missing handler used to surface as a 500 from
# _apply_rollback; now the watchdog's ``_tick`` skips unknown kinds with
# a logged warning instead of swallowing them silently (#109).

_RollbackHandler = Callable[[ApplyPlan], Awaitable[None]]
_ROLLBACK_HANDLERS: dict[str, _RollbackHandler] = {}


def register_rollback_kind(name: str) -> Callable[[_RollbackHandler], _RollbackHandler]:
    """Decorator: register an async handler for ``rollback_kind == name``.

    Used by tests to inject mock handlers without touching production
    dispatch. Production handlers live alongside this module.
    """

    def deco(fn: _RollbackHandler) -> _RollbackHandler:
        _ROLLBACK_HANDLERS[name] = fn
        return fn

    return deco


def known_rollback_kinds() -> set[str]:
    """Return the set of registered rollback kinds (handy for diagnostics
    and the watchdog's unknown-kind warning)."""
    return set(_ROLLBACK_HANDLERS)


@register_rollback_kind("net_interfaces")
async def _rollback_net_interfaces(plan: ApplyPlan) -> None:
    before = plan.before
    # The before snapshot carries {name, method, address, netmask, gateway, dns, up}
    await get_db().execute(
        """UPDATE net_interfaces
           SET method=?, address=?, netmask=?, gateway=?, dns=?, up=?,
               updated_at=datetime('now')
           WHERE name=?""",
        (
            before.get("method"),
            before.get("address"),
            before.get("netmask"),
            before.get("gateway"),
            before.get("dns"),
            1 if before.get("up") else 0,
            before.get("name"),
        ),
    )


@register_rollback_kind("ota_slot")
async def _rollback_ota_slot(plan: ApplyPlan) -> None:
    """Revert an OTA slot switch by writing the previous slot back to the
    ``ota_slot`` table. The HAL layer is responsible for actually applying
    the change on next boot; here we just restore the persisted target."""
    before = plan.before
    target = before.get("target")
    if target in {"A", "B"}:
        await get_db().execute(
            "UPDATE ota_slot SET target = ?, updated_at = datetime('now') WHERE id = 1",
            (target,),
        )


@register_rollback_kind("ota_install")
async def _rollback_ota_install(plan: ApplyPlan) -> None:
    """Mark an in-flight OTA install as aborted and restore the previous
    active slot if recorded."""
    before = plan.before
    previous_slot = before.get("previous_slot")
    if previous_slot in {"A", "B"}:
        await get_db().execute(
            "UPDATE ota_slot SET target = ?, updated_at = datetime('now') WHERE id = 1",
            (previous_slot,),
        )
    # Mark the install job row as aborted if we have its id.
    install_id = before.get("install_id")
    if isinstance(install_id, int):
        await get_db().execute(
            "UPDATE ota_installs SET status = 'aborted', updated_at = datetime('now') WHERE id = ?",
            (install_id,),
        )


@register_rollback_kind("boot_env")
async def _rollback_boot_env(plan: ApplyPlan) -> None:
    """Restore a u-boot env variable to its previous value. The before
    snapshot carries ``{key, value}``; on the device we'd shell out to
    ``fw_setenv``, here we record the target in an audit-friendly
    ``boot_env_kv`` table so the action log shows the previous value."""
    before = plan.before
    key = before.get("key")
    value = before.get("value", "")
    if isinstance(key, str) and key:
        await get_db().execute(
            """INSERT INTO boot_env_kv (key, value, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET
                   value = excluded.value,
                   updated_at = excluded.updated_at""",
            (key, str(value)),
        )


@register_rollback_kind("factory_reset")
async def _rollback_factory_reset(plan: ApplyPlan) -> None:
    """Re-apply the pre-reset snapshot. The before snapshot is a small
    subset of ``system_settings_kv`` rows; here we restore the values that
    were carried in the snapshot."""
    before = plan.before
    rows = before.get("rows")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = row.get("key")
            value = row.get("value", "")
            if not isinstance(key, str) or not key:
                continue
            await get_db().execute(
                """INSERT INTO system_settings_kv (key, value, updated_at)
                   VALUES (?, ?, datetime('now'))
                   ON CONFLICT(key) DO UPDATE SET
                       value = excluded.value,
                       updated_at = excluded.updated_at""",
                (key, str(value)),
            )


@register_rollback_kind("service_unit")
async def _rollback_service_unit(plan: ApplyPlan) -> None:
    """Restore a service unit's enable/disable state to its previous value."""
    before = plan.before
    name = before.get("name")
    enabled = bool(before.get("enabled", True))
    if isinstance(name, str) and name:
        await get_db().execute(
            """UPDATE service_state
               SET enabled = ?, updated_at = datetime('now')
               WHERE name = ?""",
            (1 if enabled else 0, name),
        )


@register_rollback_kind("restore_apply")
async def _rollback_restore_apply(plan: ApplyPlan) -> None:
    """Restore the prior configuration state after a ``restore_apply``
    (e.g. config-snapshot restore). The before snapshot is a dict of
    ``{table: {pk: row}}`` we write back row-by-row."""
    before = plan.before
    tables = before.get("tables")
    if not isinstance(tables, dict):
        return
    for table, rows in tables.items():
        if not isinstance(table, str) or not isinstance(rows, dict):
            continue
        for pk, row in rows.items():
            if not isinstance(row, dict):
                continue
            cols = ", ".join(f"{k} = ?" for k in row)
            values: tuple[Any, ...] = (*row.values(), pk)
            await get_db().execute(
                f"UPDATE {table} SET {cols}, updated_at = datetime('now') WHERE id = ?",  # noqa: S608
                values,
            )


async def _apply_rollback(plan: ApplyPlan) -> None:
    """Dispatch to the registered handler for ``plan.rollback_kind``.

    Unknown kinds raise ``unknown_rollback_kind`` (500) so callers
    (watchdog, manual rollback) see the failure explicitly instead of
    silently skipping (#109).
    """
    handler = _ROLLBACK_HANDLERS.get(plan.rollback_kind)
    if handler is None:
        raise ApiError(
            "unknown_rollback_kind",
            f"No rollback handler for '{plan.rollback_kind}'",
            500,
        )
    await handler(plan)


# --- watchdog: expire any plan past its armed_until -----------------------
# How stale may the last heartbeat be (in seconds) before the watchdog
# considers the device "never came back" and auto-rolls back. Set wide
# enough that a slow SPA click after a reboot still counts as healthy,
# tight enough that a truly lost device is rolled back promptly. One
# hour matches the longest plausible reboot + reconnect window.
HEARTBEAT_STALE_S = 3600


class ApplyPlanWatchdog:
    def __init__(self, interval_s: float = 5.0) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._interval = interval_s

    def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        while not self._stop.is_set():
            with contextlib.suppress(Exception):
                await self._tick()
            await asyncio.sleep(self._interval)

    async def _tick(self) -> None:
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        rows = await get_db().fetch_all(
            "SELECT id, rollback_kind FROM apply_plans WHERE status = 'armed' AND armed_until <= ?",
            (now_iso,),
        )
        for row in rows:
            plan_id = int(row["id"])
            kind = row["rollback_kind"]
            try:
                # Guardrail 2 (audit #93): a recent heartbeat means the
                # device is healthy with the new config, so commit
                # instead of rolling back. A stale / missing heartbeat
                # means the device never came back — fall back to
                # rollback.
                if await heartbeat_recent(HEARTBEAT_STALE_S):
                    await commit(plan_id)
                else:
                    await rollback(plan_id, reason="expired")
            except ApiError as exc:
                if exc.code == "unknown_rollback_kind":
                    # Surface missing handlers visibly (#109). The plan
                    # is left armed; an operator can resolve it manually.
                    import logging

                    logging.getLogger(__name__).warning(
                        "apply plan %s has no rollback handler for kind '%s'", plan_id, kind
                    )
                    continue
                # Already committed/rolled back by another worker — fine.
                continue


apply_watchdog = ApplyPlanWatchdog()

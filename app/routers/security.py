"""Group 8 — Security & Users (SSH keys, certs, audit, compliance)."""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse

from app.auth.deps import CurrentUser, require_admin, require_operator, require_viewer
from app.db import audit
from app.db.database import get_db
from app.hal.registry import get_hal
from app.util.confirm import require_confirm
from app.util.models import SshPolicyUpdate

router = APIRouter(prefix="/api/security", tags=["security"])


def _fingerprint(key: str) -> str:
    parts = key.split()
    blob = parts[1] if len(parts) > 1 else key
    digest = hashlib.sha256(blob.encode()).digest()
    import base64

    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


# Allow-list of SSH key types the SSH authorized_keys file recognizes.
# Anything outside this set is rejected so a stray non-key string can't end
# up in the store (and, later, in ~deviceuser/.ssh/authorized_keys).
_VALID_SSH_KEY_TYPES: frozenset[str] = frozenset(
    {
        "ssh-rsa",
        "ssh-ed25519",
        "ecdsa-sha2-nistp256",
        "ecdsa-sha2-nistp384",
        "ecdsa-sha2-nistp521",
        "ssh-dss",
    }
)


def _validate_ssh_key(key: str) -> tuple[str, str]:
    """Return (key_type, comment) for a well-formed key, else raise ApiError.

    The form is ``<type> <base64-blob> [comment]``. The base64 blob must be
    non-empty; the comment is optional.
    """
    from app.errors import ApiError

    parts = key.split()
    if len(parts) < 2:
        raise ApiError(
            "bad_key",
            "Key must be in 'ssh-<type> <base64> [comment]' form",
            400,
        )
    key_type, blob = parts[0], parts[1]
    if key_type not in _VALID_SSH_KEY_TYPES:
        raise ApiError(
            "bad_key_type",
            f"Unknown key type '{key_type}'; allowed: {sorted(_VALID_SSH_KEY_TYPES)}",
            400,
        )
    # Base64 alphabet: A-Z a-z 0-9 + / = (and = padding only at end).
    import re as _re

    if not _re.fullmatch(r"[A-Za-z0-9+/]+=*", blob):
        raise ApiError("bad_key_blob", "Key payload is not valid base64", 400)
    if len(blob) < 16:
        # Anything shorter is suspiciously short for a real key (an
        # ed25519 key alone is ~68 chars). The check is deliberately
        # lax; we don't want to false-positive on tiny test fixtures.
        raise ApiError("bad_key_blob", "Key payload is suspiciously short", 400)
    comment = parts[2] if len(parts) >= 3 else "added"
    return key_type, comment


# --- SSH keys --------------------------------------------------------------
# Authorized keys live in the DB (migration v4) so all uvicorn workers share
# state and the keys survive a restart. A real deployment would additionally
# sync to ~deviceuser/.ssh/authorized_keys; that sync is the deployer's job.
_SEED_SSH_KEY = {
    "comment": "ops@laptop",
    "key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleKeyData ops@laptop",
}


async def _ensure_ssh_seed() -> None:
    db = get_db()
    row = await db.fetch_one("SELECT COUNT(*) AS n FROM ssh_keys")
    if row and row["n"]:
        return
    await db.execute(
        "INSERT OR IGNORE INTO ssh_keys (comment, key) VALUES (?, ?)",
        (_SEED_SSH_KEY["comment"], _SEED_SSH_KEY["key"]),
    )


@router.get("/ssh-keys")
async def list_ssh_keys(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    await _ensure_ssh_seed()
    rows = await get_db().fetch_all("SELECT comment, key, added_at FROM ssh_keys ORDER BY id")
    return {"keys": [{"comment": r["comment"], "fingerprint": _fingerprint(r["key"]), "key": r["key"]} for r in rows]}


@router.post("/ssh-keys")
async def add_ssh_key(body: dict[str, str], user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    from app.errors import ApiError

    await _ensure_ssh_seed()
    key = body.get("key", "").strip()
    _, comment = _validate_ssh_key(key)
    if body.get("comment"):
        comment = str(body["comment"])
    try:
        await get_db().execute(
            "INSERT INTO ssh_keys (comment, key) VALUES (?, ?)",
            (comment, key),
        )
    except Exception as exc:  # IntegrityError on UNIQUE(key)
        raise ApiError("duplicate_key", "Key already present", 409) from exc
    await audit.record(username=user.username, ip=user.ip, action="ssh_key_add", target=comment)
    return {"ok": True, "fingerprint": _fingerprint(key)}


@router.delete("/ssh-keys/{fingerprint}")
async def remove_ssh_key(fingerprint: str, user: CurrentUser = Depends(require_admin)) -> dict[str, bool]:
    rows = await get_db().fetch_all("SELECT key FROM ssh_keys")
    target_key = None
    for r in rows:
        if _fingerprint(r["key"]) == fingerprint:
            target_key = r["key"]
            break
    if target_key is None:
        return {"ok": False}
    await get_db().execute("DELETE FROM ssh_keys WHERE key = ?", (target_key,))
    await audit.record(username=user.username, ip=user.ip, action="ssh_key_remove", target=fingerprint)
    return {"ok": True}


# --- certificates ----------------------------------------------------------
@router.get("/certificates")
async def certificates(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    now = dt.date.today()
    certs = [
        {
            "name": "device.crt",
            "subject": "CN=device-001",
            "issuer": "CN=Lab CA",
            "expires": str(now + dt.timedelta(days=120)),
            "days_remaining": 120,
        },
        {
            "name": "mqtt-client.crt",
            "subject": "CN=mqtt-client",
            "issuer": "CN=Lab CA",
            "expires": str(now + dt.timedelta(days=18)),
            "days_remaining": 18,
        },
    ]
    return {"certificates": certs}


# --- audit trail -----------------------------------------------------------
# Cap the audit `limit` so a request can't ask for 10M rows (#39). The
# audit log is append-only, so the table can grow large over time; 5k
# is more than the dashboard needs for any one view.
_AUDIT_MAX_LIMIT = 5_000


@router.get("/audit")
async def audit_trail(
    user_filter: str | None = None,
    action: str | None = None,
    target: str | None = None,
    limit: int = Query(200, ge=1, le=_AUDIT_MAX_LIMIT),
    _: CurrentUser = Depends(require_viewer),
) -> dict[str, Any]:
    clauses: list[str] = []
    params: list[Any] = []
    if user_filter:
        clauses.append("username = ?")
        params.append(user_filter)
    if action:
        clauses.append("action = ?")
        params.append(action)
    if target:
        clauses.append("target = ?")
        params.append(target)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = await get_db().fetch_all(
        f"SELECT * FROM audit_log{where} ORDER BY ts DESC, id DESC LIMIT ?", (*params, limit)
    )
    return {"entries": rows}


@router.get("/audit/export", response_class=PlainTextResponse)
async def audit_export(_: CurrentUser = Depends(require_viewer)) -> str:
    rows = await get_db().fetch_all("SELECT * FROM audit_log ORDER BY ts DESC, id DESC")
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["ts", "username", "ip", "action", "target", "old_value", "new_value"])
    for r in rows:
        writer.writerow([r["ts"], r["username"], r["ip"], r["action"], r["target"], r["old_value"], r["new_value"]])
    return buf.getvalue()


# --- login security --------------------------------------------------------
@router.get("/login-attempts")
async def login_attempts(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    rows = await get_db().fetch_all(
        "SELECT * FROM login_attempts WHERE success = 0 ORDER BY ts DESC, id DESC LIMIT 100"
    )
    return {"attempts": rows}


# --- compliance / SBOM / CVE ----------------------------------------------
def _load_data_file(name: str) -> Any:
    path = Path("data") / name
    if not path.exists():
        path = Path("compliance") / name
    if not path.exists():
        return None
    text = path.read_text()
    if name.endswith((".yaml", ".yml")):
        return yaml.safe_load(text)
    return json.loads(text)


@router.get("/compliance")
async def compliance(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    data = _load_data_file("compliance.yaml") or {
        "device": "Generic ARM Dev Board",
        "certifications": [{"type": "FCC", "id": "FCC-PENDING"}, {"type": "CE", "id": "CE-PENDING"}],
        "last_audit": "2026-01-15",
    }
    return {"compliance": data}


@router.get("/licenses")
async def licenses(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    data = _load_data_file("licenses.json") or [
        {"name": "fastapi", "version": "0.115.6", "license": "MIT"},
        {"name": "vue", "version": "3.4", "license": "MIT"},
    ]
    return {"licenses": data}


@router.get("/sbom")
async def sbom(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    data = _load_data_file("sbom.json")
    if data is None:
        data = {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "components": [
                {"name": "fastapi", "version": "0.115.6", "type": "library"},
                {"name": "uvicorn", "version": "0.34.0", "type": "library"},
            ],
        }
    return {"sbom": data}


@router.get("/cve")
async def cve(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """CVE matches with operator ack state overlay (audit §9, #133).

    The CVE *list* is read from ``data/cve-scan.json`` (read-only).
    The ack state comes from the ``cve_acknowledged`` table. Every
    row in the response carries an ``acknowledged`` flag plus the
    ``acknowledged_at`` / ``acknowledged_by`` / ``mitigation_note``
    fields when present, so the SPA can render one merged list.
    """
    data = _load_data_file("cve-scan.json")
    matches = (data or {}).get("matches", []) if data is not None else []
    if not matches:
        return {"available": False, "matches": []}
    # Pull the live ack overlay in one query.
    rows = await get_db().fetch_all("SELECT * FROM cve_acknowledged")
    ack_by_id: dict[str, dict[str, Any]] = {r["cve_id"]: r for r in rows}
    merged = []
    for m in matches:
        cve_id = str(m.get("id") or m.get("cve_id") or "")
        ack = ack_by_id.get(cve_id)
        merged.append(
            {
                **m,
                "cve_id": cve_id,
                "acknowledged": ack is not None,
                "acknowledged_at": ack["acknowledged_at"] if ack else None,
                "acknowledged_by": ack["acknowledged_by"] if ack else None,
                "mitigation_note": ack["mitigation_note"] if ack else None,
            }
        )
    return {"available": True, "matches": merged}


@router.post("/cve/{cve_id}/acknowledge")
async def cve_acknowledge(
    cve_id: str,
    body: dict[str, Any] | None = None,
    user: CurrentUser = Depends(require_operator),
) -> dict[str, bool]:
    """Acknowledge a CVE match (audit §9, #133).

    Persists a row in ``cve_acknowledged`` (UPSERT) so a second ack
    on the same CVE updates the note / ack time / user rather than
    erroring out.
    """
    note = (body or {}).get("note")
    note_str = str(note) if note is not None else None
    await get_db().execute(
        """INSERT INTO cve_acknowledged (cve_id, acknowledged_by, mitigation_note)
           VALUES (?, ?, ?)
           ON CONFLICT(cve_id) DO UPDATE SET
               acknowledged_at = datetime('now'),
               acknowledged_by = excluded.acknowledged_by,
               mitigation_note = excluded.mitigation_note""",
        (cve_id, user.username, note_str),
    )
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="cve_acknowledge",
        target=cve_id,
        new_value=f"note={note_str}" if note_str else "note=",
    )
    return {"ok": True, "acknowledged": True}


@router.delete("/cve/{cve_id}/acknowledge")
async def cve_unacknowledge(cve_id: str, user: CurrentUser = Depends(require_operator)) -> dict[str, bool]:
    """Remove the ack state for a CVE (audit §9, #133).

    Returns 404 if there is no ack row to remove — the caller
    distinguishes "unacked" (200 from a state query) from
    "no-op unack" (404 here).
    """
    from app.errors import ApiError

    db = get_db()
    row = await db.fetch_one("SELECT * FROM cve_acknowledged WHERE cve_id = ?", (cve_id,))
    if row is None:
        raise ApiError("not_acknowledged", f"No ack row for {cve_id}", 404)
    await db.execute("DELETE FROM cve_acknowledged WHERE cve_id = ?", (cve_id,))
    await audit.record(username=user.username, ip=user.ip, action="cve_unacknowledge", target=cve_id)
    return {"ok": True, "unacknowledged": True}


# --- status-only: fuses / secure-boot / enrolled keys (audit §4, §9, #94) -
# These three surfaces are *read-only* by design. There are deliberately no
# POST/PUT/DELETE handlers here: per audit.md §9, "key enrollment is
# status-only" and the dashboard must not be able to enroll or rotate keys
# from the SPA. The corresponding HAL lives in ``app/hal/security.py`` and
# exposes three readers, all returning ``envelope({...})`` payloads.


@router.get("/secure-boot")
async def secure_boot(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """Status-only read of the device's secure-boot posture."""
    return get_hal("security").secure_boot_status()


@router.get("/fuses")
async def fuses(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """Status-only enumeration of the fuse/OTP layout (no writes)."""
    return get_hal("security").fuses()


@router.get("/keys/enrolled")
async def enrolled_keys(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """Status-only list of enrolled secure-boot / signing keys."""
    return get_hal("security").enrolled_keys()


# --- TPM status (#98) ------------------------------------------------------
# Read-only by design (audit.md §9: "TPM state" is exposed for
# monitoring, not modification). No POST/PUT/DELETE handler exists.


@router.get("/tpm")
async def tpm(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """Status-only read of the device's TPM state."""
    return get_hal("security").tpm_status()


# --- SSH policy (#95) ------------------------------------------------------
# The read endpoint merges the live sshd_config (HAL) with operator-set
# overrides persisted in ``system_settings_kv`` — persisted values win so
# the audit row and the next SPA view agree (#42 audit-state-alignment).
_KV_SSH_POLICY_PREFIX = "ssh_policy."


def _ssh_kv_keys() -> tuple[str, ...]:
    return (
        f"{_KV_SSH_POLICY_PREFIX}permit_root_login",
        f"{_KV_SSH_POLICY_PREFIX}password_authentication",
        f"{_KV_SSH_POLICY_PREFIX}kbd_interactive_authentication",
        f"{_KV_SSH_POLICY_PREFIX}x11_forwarding",
        f"{_KV_SSH_POLICY_PREFIX}port",
        f"{_KV_SSH_POLICY_PREFIX}allow_users",
    )


@router.get("/ssh-policy")
async def ssh_policy(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    """Effective SSH policy: live sshd_config + persisted overrides (#95)."""
    live = get_hal("security").sshd_config()
    rows = await get_db().fetch_all(
        f"SELECT key, value FROM system_settings_kv WHERE key IN ({','.join('?' * len(_ssh_kv_keys()))})",
        _ssh_kv_keys(),
    )
    overrides = {r["key"]: r["value"] for r in rows}
    # Map persisted keys back to the policy field names; the live dict
    # already excludes the ``simulated`` envelope field we want to keep.
    policy = {k: v for k, v in live.items() if k != "simulated"}
    if f"{_KV_SSH_POLICY_PREFIX}permit_root_login" in overrides:
        policy["permit_root_login"] = overrides[f"{_KV_SSH_POLICY_PREFIX}permit_root_login"]
    if f"{_KV_SSH_POLICY_PREFIX}password_authentication" in overrides:
        policy["password_authentication"] = overrides[f"{_KV_SSH_POLICY_PREFIX}password_authentication"]
    if f"{_KV_SSH_POLICY_PREFIX}kbd_interactive_authentication" in overrides:
        policy["kbd_interactive_authentication"] = overrides[f"{_KV_SSH_POLICY_PREFIX}kbd_interactive_authentication"]
    if f"{_KV_SSH_POLICY_PREFIX}x11_forwarding" in overrides:
        policy["x11_forwarding"] = overrides[f"{_KV_SSH_POLICY_PREFIX}x11_forwarding"] == "1"
    if f"{_KV_SSH_POLICY_PREFIX}port" in overrides:
        policy["port"] = int(overrides[f"{_KV_SSH_POLICY_PREFIX}port"])
    if f"{_KV_SSH_POLICY_PREFIX}allow_users" in overrides:
        policy["allow_users"] = overrides[f"{_KV_SSH_POLICY_PREFIX}allow_users"]
    return {"policy": policy, "simulated": live["simulated"]}


@router.put("/ssh-policy")
async def put_ssh_policy(
    body: SshPolicyUpdate,
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """Persist operator-set SSH policy overrides (#95).

    Each field is optional; only the ones present are written. The
    router enforces ``confirm: true`` (privileged write — 428 without
    it). The ``extra='forbid'`` model rejects typos with 422.
    """
    require_confirm(body.model_dump())
    written: list[str] = []
    if body.permit_root_login is not None:
        await _kv_set("ssh_policy.permit_root_login", body.permit_root_login)
        written.append("permit_root_login")
    if body.password_authentication is not None:
        await _kv_set("ssh_policy.password_authentication", body.password_authentication)
        written.append("password_authentication")
    if body.kbd_interactive_authentication is not None:
        await _kv_set("ssh_policy.kbd_interactive_authentication", body.kbd_interactive_authentication)
        written.append("kbd_interactive_authentication")
    if body.x11_forwarding is not None:
        await _kv_set("ssh_policy.x11_forwarding", "1" if body.x11_forwarding else "0")
        written.append("x11_forwarding")
    if body.port is not None:
        await _kv_set("ssh_policy.port", str(body.port))
        written.append("port")
    if body.allow_users is not None:
        await _kv_set("ssh_policy.allow_users", body.allow_users)
        written.append("allow_users")
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="ssh_policy_update",
        target="sshd",
        new_value=",".join(written) if written else None,
    )
    return {"ok": True, "applied": written}


async def _kv_set(key: str, value: str) -> None:
    """Persist a single key/value in ``system_settings_kv`` (helper for #95).

    Mirrors ``app/routers/settings.py::_kv_set`` so we keep the same
    UPSERT semantics in one place; the helper is duplicated here
    intentionally — a shared module would couple the security router to
    the settings module's lifecycle.
    """
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

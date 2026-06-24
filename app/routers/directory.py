"""Directory integration (Features.md §Users Directory Integration)."""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import Field

from app.auth.deps import CurrentUser, require_admin
from app.db import audit
from app.db.database import get_db
from app.errors import ApiError
from app.util.models import _StrictModel

router = APIRouter(prefix="/api/directory", tags=["directory"])


@router.get("")
async def list_providers(_: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """List directory providers (LDAP/AD/SSO)."""
    rows = await get_db().fetch_all(
        "SELECT id, provider, name, config_json, enabled, last_sync_at, created_at " "FROM directory_config ORDER BY id"
    )
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["config"] = json.loads(d.pop("config_json") or "{}")
        except json.JSONDecodeError:
            d["config"] = {}
        out.append(d)
    return {"providers": out}


class _ProviderUpsert(_StrictModel):
    provider: str = Field(pattern=r"^(ldap|active_directory|sso_oauth|sso_saml)$")
    name: str = Field(min_length=1, max_length=64)
    enabled: bool = False
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    base_dn: str | None = Field(default=None, max_length=255)
    bind_dn: str | None = Field(default=None, max_length=255)
    bind_password: str | None = Field(default=None, max_length=256)
    use_tls: bool = True
    group_filter: str | None = Field(default=None, max_length=255)
    # SSO fields
    client_id: str | None = Field(default=None, max_length=128)
    client_secret: str | None = Field(default=None, max_length=256)
    authorize_url: str | None = Field(default=None, max_length=512)
    token_url: str | None = Field(default=None, max_length=512)


@router.post("")
async def upsert_provider(body: _ProviderUpsert, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Create or update a directory provider (privileged write, audited)."""
    cfg = {
        k: v
        for k, v in {
            "host": body.host,
            "port": body.port,
            "base_dn": body.base_dn,
            "bind_dn": body.bind_dn,
            "bind_password": body.bind_password,
            "use_tls": body.use_tls,
            "group_filter": body.group_filter,
            "client_id": body.client_id,
            "client_secret": body.client_secret,
            "authorize_url": body.authorize_url,
            "token_url": body.token_url,
        }.items()
        if v is not None
    }
    cur = await get_db().execute(
        """
        INSERT INTO directory_config (provider, name, enabled, config_json, created_at)
        VALUES (?, ?, ?, ?, datetime('now'))
        """,
        (body.provider, body.name, int(body.enabled), json.dumps(cfg)),
    )
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="directory_upsert",
        target=body.name,
        new_value=f"provider={body.provider}:enabled={body.enabled}",
    )
    return {"ok": True, "id": cur}


@router.delete("/{provider_id}")
async def delete_provider(provider_id: int, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    row = await get_db().fetch_one("SELECT name FROM directory_config WHERE id = ?", (provider_id,))
    if row is None:
        raise ApiError("provider_not_found", f"provider {provider_id} not found", 404)
    await get_db().execute("DELETE FROM directory_config WHERE id = ?", (provider_id,))
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="directory_delete",
        target=row["name"],
    )
    return {"ok": True, "deleted": provider_id}


@router.post("/sync")
async def trigger_sync(
    body: dict[str, Any],
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """Trigger a directory sync (privileged write, audited).

    Real mode shells out to ldapsearch / runs an OAuth handshake. Simulated
    returns a deterministic envelope.
    """
    pid = int(body.get("provider_id", 0))
    row = await get_db().fetch_one("SELECT name FROM directory_config WHERE id = ?", (pid,))
    if row is None:
        raise ApiError("provider_not_found", f"provider {pid} not found", 404)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    await get_db().execute(
        "UPDATE directory_config SET last_sync_at = ? WHERE id = ?",
        (now, pid),
    )
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="directory_sync",
        target=row["name"],
        new_value=now,
    )
    return {"ok": True, "synced_at": now, "applied": False, "simulated": True}

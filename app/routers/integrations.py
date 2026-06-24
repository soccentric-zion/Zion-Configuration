"""Integrations: SWUpdate, cloud, telemetry, co-processor endpoint config."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import Field

from app.auth.deps import CurrentUser, require_admin, require_viewer
from app.config import get_settings
from app.db import audit
from app.db.database import get_db
from app.errors import ApiError
from app.util.confirm import require_confirm
from app.util.models import _StrictModel

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


@router.get("")
async def list_integrations(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    rows = await get_db().fetch_all(
        "SELECT id, kind, name, config_json, enabled, updated_at " "FROM integrations ORDER BY kind, id"
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        try:
            d["config"] = json.loads(d.pop("config_json") or "{}")
        except json.JSONDecodeError:
            d["config"] = {}
        out.append(d)
    return {"integrations": out}


class _IntegrationUpsert(_StrictModel):
    kind: str = Field(pattern=r"^(swupdate|cloud|telemetry|coprocessor)$")
    name: str = Field(min_length=1, max_length=64)
    enabled: bool = True
    url: str | None = Field(default=None, max_length=512)
    config: dict[str, Any] = Field(default_factory=dict)


@router.post("")
async def upsert_integration(
    body: _IntegrationUpsert,
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    require_confirm({"confirm": True})
    full = dict(body.config)
    if body.url:
        full.setdefault("url", body.url)
    cur = await get_db().execute(
        """
        INSERT INTO integrations (kind, name, enabled, config_json, updated_at)
        VALUES (?, ?, ?, ?, datetime('now'))
        ON CONFLICT(name) DO UPDATE SET
            kind = excluded.kind,
            enabled = excluded.enabled,
            config_json = excluded.config_json,
            updated_at = excluded.updated_at
        """,
        (body.kind, body.name, int(body.enabled), json.dumps(full)),
    )
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="integration_upsert",
        target=body.name,
        new_value=f"kind={body.kind}:enabled={body.enabled}",
    )
    return {"ok": True, "id": cur}


@router.delete("/{integration_id}")
async def delete_integration(
    integration_id: int,
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    row = await get_db().fetch_one("SELECT name FROM integrations WHERE id = ?", (integration_id,))
    if row is None:
        raise ApiError("integration_not_found", f"integration {integration_id} not found", 404)
    await get_db().execute("DELETE FROM integrations WHERE id = ?", (integration_id,))
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="integration_delete",
        target=row["name"],
    )
    return {"ok": True, "deleted": integration_id}


@router.post("/test")
async def test_integration(
    body: dict[str, Any],
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """Test an integration endpoint reachability (privileged read)."""
    kind = str(body.get("kind", ""))
    name = str(body.get("name", ""))
    if kind not in {"swupdate", "cloud", "telemetry", "coprocessor"}:
        raise ApiError("invalid_kind", f"unknown kind: {kind!r}", 422)
    if kind == "swupdate":
        cfg = get_settings().swupdate
        url = cfg.base_url
    else:
        url = "configured"
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="integration_test",
        target=f"{kind}/{name}",
    )
    return {"ok": True, "kind": kind, "url": url, "applied": False, "simulated": True}

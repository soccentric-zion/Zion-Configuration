"""Notifications: email / webhook / toast channels + per-event toggles."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import Field

from app.auth.deps import CurrentUser, require_admin, require_viewer
from app.db import audit
from app.db.database import get_db
from app.errors import ApiError
from app.util.models import _StrictModel

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


def _validate_email(addr: str) -> bool:
    """Light RFC-5322-lite check: must contain ``@`` and a dot in the
    domain, no spaces. Avoids pulling in email-validator (supply-chain).
    """
    if not addr or " " in addr or "@" not in addr:
        return False
    local, _, domain = addr.partition("@")
    if not local or not domain or "." not in domain:
        return False
    return True


@router.get("/channels")
async def list_channels(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    rows = await get_db().fetch_all(
        "SELECT id, channel, name, enabled, config_json, events_json, updated_at " "FROM notifications ORDER BY id ASC"
    )
    return {"channels": [dict(r) for r in rows]}


class _ChannelUpsert(_StrictModel):
    channel: str = Field(pattern=r"^(email|webhook|toast)$")
    name: str = Field(min_length=1, max_length=64)
    enabled: bool = True
    # SMTP / webhook fields are all optional — only relevant for the matching
    # channel. Validation is shallow; deeper checks happen at send time.
    smtp_host: str | None = Field(default=None, max_length=255)
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_user: str | None = Field(default=None, max_length=128)
    smtp_password: str | None = Field(default=None, max_length=256)
    smtp_from: str | None = Field(default=None, max_length=254)
    smtp_to: list[str] = Field(default_factory=list)
    smtp_use_tls: bool = True
    webhook_url: str | None = Field(default=None, max_length=512)
    events: dict[str, bool] = Field(default_factory=dict)


@router.post("/channels")
async def upsert_channel(
    body: _ChannelUpsert,
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """Create / update a notification channel (privileged write, audited)."""
    if body.channel == "email" and not (body.smtp_host and body.smtp_from):
        raise ApiError(
            "smtp_incomplete",
            "email channel needs smtp_host + smtp_from",
            422,
        )
    if body.smtp_from and not _validate_email(body.smtp_from):
        raise ApiError("invalid_email", f"smtp_from {body.smtp_from!r} is not a valid email", 422)
    bad_to = [a for a in body.smtp_to if not _validate_email(a)]
    if bad_to:
        raise ApiError("invalid_email", f"smtp_to has invalid entries: {', '.join(bad_to)}", 422)
    if body.channel == "webhook" and not body.webhook_url:
        raise ApiError("webhook_incomplete", "webhook channel needs webhook_url", 422)
    cfg = {
        k: v
        for k, v in {
            "smtp_host": body.smtp_host,
            "smtp_port": body.smtp_port,
            "smtp_user": body.smtp_user,
            "smtp_password": body.smtp_password,
            "smtp_from": body.smtp_from,
            "smtp_to": list(body.smtp_to),
            "smtp_use_tls": body.smtp_use_tls,
            "webhook_url": body.webhook_url,
        }.items()
        if v is not None
    }
    import json as _json

    cfg_json = _json.dumps(cfg)
    events_json = _json.dumps(body.events)
    cur = await get_db().execute(
        """
        INSERT INTO notifications (channel, name, enabled, config_json, events_json, updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(name) DO UPDATE SET
            channel = excluded.channel,
            enabled = excluded.enabled,
            config_json = excluded.config_json,
            events_json = excluded.events_json,
            updated_at = excluded.updated_at
        """,
        (body.channel, body.name, int(body.enabled), cfg_json, events_json),
    )
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="notification_upsert",
        target=body.name,
        new_value=f"channel={body.channel}:enabled={body.enabled}",
    )
    return {"ok": True, "id": cur}


@router.delete("/channels/{channel_id}")
async def delete_channel(
    channel_id: int,
    user: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    row = await get_db().fetch_one("SELECT name FROM notifications WHERE id = ?", (channel_id,))
    if row is None:
        raise ApiError("channel_not_found", f"channel {channel_id} not found", 404)
    await get_db().execute("DELETE FROM notifications WHERE id = ?", (channel_id,))
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="notification_delete",
        target=row["name"],
    )
    return {"ok": True, "deleted": channel_id}


class _TestSend(_StrictModel):
    channel_id: int
    body: str = Field(default="Test notification from dashboard", max_length=512)


@router.post("/test-send")
async def test_send(body: _TestSend, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Send a test message through a configured channel (privileged write).

    Real mode shells out to ``smtplib`` / ``curl``. Simulated: returns
    an envelope that says it would have applied.
    """
    row = await get_db().fetch_one(
        "SELECT name, channel, config_json FROM notifications WHERE id = ?",
        (body.channel_id,),
    )
    if row is None:
        raise ApiError("channel_not_found", f"channel {body.channel_id} not found", 404)
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="notification_test_send",
        target=row["name"],
        new_value=f"channel={row['channel']}",
    )
    return {
        "ok": True,
        "applied": False,
        "simulated": True,
        "channel": row["channel"],
        "name": row["name"],
        "would_send": body.body,
    }


# Per-event toggles surface: SPA reads this to render a matrix of
# (event_class × channel) checkboxes.
_EVENT_CLASSES: tuple[str, ...] = (
    "system",
    "security",
    "network",
    "ota",
    "backup",
    "audit",
)


@router.get("/event-classes")
async def event_classes(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return {"classes": list(_EVENT_CLASSES)}

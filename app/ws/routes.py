"""WebSocket endpoints: metrics, logs, terminal (PTY), OTA, CAN, alerts.

All sockets authenticate via a ``token`` query parameter that is validated
*before* ``accept()``. Live-data channels simply join the broadcast hub; the
terminal and CAN sockets stream their own data.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.auth.deps import CurrentUser, user_from_token
from app.errors import ApiError
from app.hal.registry import get_hal
from app.ws.manager import hub

router = APIRouter()


async def _authenticate(ws: WebSocket, min_role: str = "viewer") -> CurrentUser | None:
    """Validate the token query param before accepting. Returns user or None."""
    token = ws.query_params.get("token", "")
    ip = ws.client.host if ws.client else "unknown"
    try:
        user = user_from_token(token, ip)
    except ApiError:
        await ws.close(code=4401)
        return None
    from app.auth.deps import ROLE_RANK

    if ROLE_RANK.get(user.role, -1) < ROLE_RANK[min_role]:
        await ws.close(code=4403)
        return None
    return user


async def _channel_socket(ws: WebSocket, channel: str) -> None:
    user = await _authenticate(ws)
    if user is None:
        return
    await ws.accept()
    await hub.join(channel, ws)
    try:
        while True:
            # Keep the socket open; ignore client pings/messages.
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await hub.leave(channel, ws)


@router.websocket("/ws/metrics")
async def ws_metrics(ws: WebSocket) -> None:
    await _channel_socket(ws, "metrics")


@router.websocket("/ws/ota")
async def ws_ota(ws: WebSocket) -> None:
    await _channel_socket(ws, "ota")


@router.websocket("/ws/alerts")
async def ws_alerts(ws: WebSocket) -> None:
    await _channel_socket(ws, "alerts")


@router.websocket("/ws/logs")
async def ws_logs(ws: WebSocket) -> None:
    """Live journal follow (simulated stream)."""
    user = await _authenticate(ws)
    if user is None:
        return
    unit = ws.query_params.get("unit", "system")
    await ws.accept()
    try:
        i = 0
        while True:
            line = f"{time.strftime('%b %d %H:%M:%S')} host {unit}: live log line {i}"
            await ws.send_json({"line": line})
            i += 1
            await asyncio.sleep(1.0)
    except (WebSocketDisconnect, RuntimeError):
        pass


@router.websocket("/ws/can")
async def ws_can(ws: WebSocket) -> None:
    """Live CAN frame stream."""
    user = await _authenticate(ws)
    if user is None:
        return
    await ws.accept()
    try:
        while True:
            frames = get_hal("hardware").can_frames(1)["frames"]
            await ws.send_json(frames[0])
            await asyncio.sleep(0.5)
    except (WebSocketDisconnect, RuntimeError):
        pass


@router.websocket("/ws/camera")
async def ws_camera(ws: WebSocket) -> None:
    """Live CSI camera feed (simulated frames) for the Camera & Display section.

    The dashboard never touches the camera device directly — it subscribes here
    and renders the streamed frames. The camera name is a query parameter.
    """
    user = await _authenticate(ws)
    if user is None:
        return
    name = ws.query_params.get("name", "csi0")
    await ws.accept()
    media = get_hal("media")
    config = {"width": 1920, "height": 1080, "format": "YUV420"}
    try:
        seq = 0
        while True:
            await ws.send_json(media.frame(name, seq, config))
            seq += 1
            await asyncio.sleep(0.1)
    except (WebSocketDisconnect, RuntimeError):
        pass


@router.websocket("/ws/terminal")
async def ws_terminal(ws: WebSocket) -> None:
    """xterm.js <-> PTY bridge. Admin only; session start/end is audited."""
    user = await _authenticate(ws, min_role="admin")
    if user is None:
        return
    await ws.accept()

    from app.db import audit

    await audit.record(username=user.username, ip=user.ip, action="terminal_open", target="pty")

    if os.name != "posix":
        await ws.send_text("PTY not available on this platform.\r\n")
        await ws.close()
        return

    import fcntl
    import pty
    import struct
    import termios

    pid, master_fd = pty.fork()
    if pid == 0:  # child
        shell = os.environ.get("SHELL", "/bin/bash")
        os.execvp(shell, [shell])
        return  # pragma: no cover

    loop = asyncio.get_event_loop()

    async def pty_to_ws() -> None:
        with contextlib.suppress(Exception):
            while True:
                data = await loop.run_in_executor(None, _read_fd, master_fd)
                if not data:
                    break
                await ws.send_bytes(data)

    def _set_winsize(rows: int, cols: int) -> None:
        with contextlib.suppress(Exception):
            fcntl.ioctl(master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    reader = asyncio.create_task(pty_to_ws())
    try:
        while True:
            msg = await ws.receive_text()
            if msg.startswith("\x00resize:"):
                with contextlib.suppress(Exception):
                    payload = json.loads(msg[len("\x00resize:") :])
                    _set_winsize(int(payload["rows"]), int(payload["cols"]))
                continue
            os.write(master_fd, msg.encode())
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        reader.cancel()
        with contextlib.suppress(Exception):
            os.close(master_fd)
        with contextlib.suppress(Exception):
            os.kill(pid, 9)
        await audit.record(username=user.username, ip=user.ip, action="terminal_close", target="pty")


def _read_fd(fd: int) -> bytes:
    try:
        return os.read(fd, 4096)
    except OSError:
        return b""

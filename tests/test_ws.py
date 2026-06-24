"""WebSocket channels: auth gating, live metrics, logs, and OTA simulation."""

from __future__ import annotations

import pytest
from starlette.websockets import WebSocketDisconnect


def test_metrics_requires_token(client: object) -> None:
    # No token -> the server closes before accept; the client sees a disconnect.
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/metrics") as ws:  # type: ignore[attr-defined]
            ws.receive_text()


def test_metrics_stream_schema(client: object, tokens: dict[str, str]) -> None:
    url = f"/ws/metrics?token={tokens['viewer']}"
    with client.websocket_connect(url) as ws:  # type: ignore[attr-defined]
        msg = ws.receive_json()
    assert msg["type"] == "health"
    assert "data" in msg and "ts" in msg


def test_logs_stream_emits_lines(client: object, tokens: dict[str, str]) -> None:
    url = f"/ws/logs?token={tokens['viewer']}&unit=test"
    with client.websocket_connect(url) as ws:  # type: ignore[attr-defined]
        msg = ws.receive_json()
    assert "line" in msg


def test_can_stream_emits_frames(client: object, tokens: dict[str, str]) -> None:
    url = f"/ws/can?token={tokens['viewer']}"
    with client.websocket_connect(url) as ws:  # type: ignore[attr-defined]
        frame = ws.receive_json()
    assert frame  # a CAN frame dict


def test_alerts_channel_accepts_subscriber(client: object, tokens: dict[str, str]) -> None:
    # Joining the broadcast channel must succeed; no immediate close.
    url = f"/ws/alerts?token={tokens['viewer']}"
    with client.websocket_connect(url) as ws:  # type: ignore[attr-defined]
        ws.send_text("ping")  # ignored keep-alive
    # Reaching here without a WebSocketDisconnect on connect means success.


def test_terminal_requires_admin(client: object, tokens: dict[str, str]) -> None:
    # Viewer must be rejected (close code 4403) before the socket is usable.
    with pytest.raises(WebSocketDisconnect):
        url = f"/ws/terminal?token={tokens['viewer']}"
        with client.websocket_connect(url) as ws:  # type: ignore[attr-defined]
            ws.receive_text()


def test_ota_simulation_completes_and_writes_history(client: object, tokens: dict[str, str]) -> None:
    headers = {"Authorization": f"Bearer {tokens['admin']}"}
    url = f"/ws/ota?token={tokens['admin']}"
    with client.websocket_connect(url) as ws:  # type: ignore[attr-defined]
        resp = client.post(  # type: ignore[attr-defined]
            "/api/software/ota/upload",
            files={"file": ("update-9.9.9.swu", b"FAKE-SWU-PAYLOAD")},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text

        completed = False
        for _ in range(30):
            msg = ws.receive_json()
            if msg.get("percent") == 100:
                completed = True
                break
        assert completed, "OTA never reached 100%"

    history = client.get(  # type: ignore[attr-defined]
        "/api/software/ota/history", headers=headers
    ).json()["history"]
    assert any(row["version"] == "update-9.9.9" for row in history)

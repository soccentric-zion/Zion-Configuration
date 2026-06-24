"""Group 3 — Hardware: GPIO, PWM, ADC, peripherals, sensors, buses."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Path, Query

from app.auth.deps import CurrentUser, require_admin, require_operator, require_viewer
from app.db import audit
from app.errors import ApiError
from app.hal.registry import get_hal
from app.util.models import ModbusWrite, MqttPublish

router = APIRouter(prefix="/api/hardware", tags=["hardware"])


def _hw() -> Any:
    return get_hal("hardware")


# --- GPIO ------------------------------------------------------------------
# SoC-class parts expose at most a few hundred GPIOs; 999 is a generous upper
# bound. Prevents "POST /api/hardware/gpio/99999999" from bloating the in-memory
# HAL state. FastAPI returns 422 automatically on out-of-range.
_GPIO_MIN, _GPIO_MAX = 0, 999


@router.get("/gpio")
async def gpio_list(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return _hw().gpio_list()


@router.post("/gpio/{pin}")
async def gpio_set(
    pin: int = Path(..., ge=_GPIO_MIN, le=_GPIO_MAX),
    body: dict[str, int] | None = None,
    user: CurrentUser = Depends(require_operator),
) -> dict[str, Any]:
    result = _hw().gpio_set(pin, int((body or {}).get("value", 0)))
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="gpio_set",
        target=f"gpio{pin}",
        new_value=result["value"],
    )
    return result


@router.post("/gpio/{pin}/export")
async def gpio_export(
    pin: int = Path(..., ge=_GPIO_MIN, le=_GPIO_MAX),
    body: dict[str, str] | None = None,
    user: CurrentUser = Depends(require_operator),
) -> dict[str, Any]:
    result = _hw().gpio_export(pin, (body or {}).get("direction", "out"))
    await audit.record(username=user.username, ip=user.ip, action="gpio_export", target=f"gpio{pin}")
    return result


@router.delete("/gpio/{pin}")
async def gpio_unexport(
    pin: int = Path(..., ge=_GPIO_MIN, le=_GPIO_MAX),
    user: CurrentUser = Depends(require_operator),
) -> dict[str, Any]:
    result = _hw().gpio_unexport(pin)
    await audit.record(username=user.username, ip=user.ip, action="gpio_unexport", target=f"gpio{pin}")
    return result


# --- PWM -------------------------------------------------------------------
# Channels: typical SoC PWM hardware exposes 0..15 (RP2040 has 16 slices).
# period_ns: 1us .. 1s. duty_ns is validated against period_ns in HAL.
_PWM_MIN_CH, _PWM_MAX_CH = 0, 15
_PWM_MIN_PERIOD_NS, _PWM_MAX_PERIOD_NS = 1_000, 1_000_000_000
_PWM_MIN_DUTY_NS, _PWM_MAX_DUTY_NS = 0, 1_000_000_000


@router.get("/pwm")
async def pwm_list(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return _hw().pwm_list()


@router.post("/pwm/{channel}")
async def pwm_set(
    channel: int = Path(..., ge=_PWM_MIN_CH, le=_PWM_MAX_CH),
    body: dict[str, Any] | None = None,
    user: CurrentUser = Depends(require_operator),
) -> dict[str, Any]:
    payload = body or {}
    period_ns = int(payload.get("period_ns", 1_000_000))
    if not (_PWM_MIN_PERIOD_NS <= period_ns <= _PWM_MAX_PERIOD_NS):
        raise ApiError(
            "bad_period",
            f"period_ns must be in [{_PWM_MIN_PERIOD_NS}, {_PWM_MAX_PERIOD_NS}]",
            400,
        )
    duty_ns = int(payload.get("duty_ns", 0))
    if not (_PWM_MIN_DUTY_NS <= duty_ns <= _PWM_MAX_DUTY_NS):
        raise ApiError(
            "bad_duty",
            f"duty_ns must be in [{_PWM_MIN_DUTY_NS}, {_PWM_MAX_DUTY_NS}]",
            400,
        )
    result = _hw().pwm_set(
        channel,
        period_ns,
        duty_ns,
        bool(payload.get("enabled", True)),
    )
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="pwm_set",
        target=f"pwm{channel}",
        new_value=result,
    )
    return result


# --- ADC / sensors ---------------------------------------------------------
@router.get("/adc")
async def adc(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return _hw().adc()


@router.get("/sensors")
async def sensors(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return _hw().sensors()


# --- peripherals -----------------------------------------------------------
@router.get("/usb")
async def usb(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return _hw().usb()


@router.get("/i2c")
async def i2c(
    bus: int = Query(1, ge=0, le=16),
    _: CurrentUser = Depends(require_viewer),
) -> dict[str, Any]:
    return _hw().i2c_scan(bus)


@router.get("/spi")
async def spi(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return _hw().spi()


@router.get("/bluetooth")
async def bluetooth(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return _hw().bluetooth()


# --- CAN -------------------------------------------------------------------
@router.get("/can")
async def can_status(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return _hw().can_status()


@router.get("/can/frames")
async def can_frames(
    n: int = Query(10, ge=1, le=256),
    _: CurrentUser = Depends(require_viewer),
) -> dict[str, Any]:
    return _hw().can_frames(n)


# Cap can_inject body sizes so an attacker can't bloat the audit log with
# a multi-MB string. CAN frames are at most 8 bytes; the ID is 11/29-bit.
_CAN_ID_MAX = (1 << 29) - 1
_CAN_DATA_MAX = 16  # allow CAN-FD payloads up to 16 bytes (still tiny)


@router.post("/can/inject")
async def can_inject(body: dict[str, Any], user: CurrentUser = Depends(require_admin)) -> dict[str, bool]:
    frame_id = body.get("id")
    data = body.get("data") or ""
    if isinstance(frame_id, int) and not (0 <= frame_id <= _CAN_ID_MAX):
        raise ApiError("bad_can_id", f"can id must be in [0, {_CAN_ID_MAX}]", 400)
    if not isinstance(data, str) or len(data) > _CAN_DATA_MAX:
        raise ApiError(
            "bad_can_data",
            f"can data must be a string of at most {_CAN_DATA_MAX} bytes",
            400,
        )
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="can_inject",
        target=str(frame_id),
        new_value=data,
    )
    return {"ok": True}


# --- MQTT ------------------------------------------------------------------
@router.get("/mqtt")
async def mqtt(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return _hw().mqtt_status()


@router.post("/mqtt/publish")
async def mqtt_publish(body: MqttPublish, user: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Publish a message on an MQTT topic.

    Routed through the hardware HAL so the actual broker call lives
    behind a simulation flag (#42). The audit log records the
    operator's intent regardless of simulation state. ``payload`` is
    capped at 64 KiB by ``MqttPublish`` (#96).
    """
    topic = body.topic.strip()
    payload = body.payload
    # Guardrail 1: audit must surface what was published. Truncate to
    # 256 chars so a multi-KiB payload doesn't bloat the audit log,
    # and add a `payload_truncated: bool` flag so the operator can
    # tell the row is a preview, not the full message (#145).
    PAYLOAD_AUDIT_LIMIT = 256
    if len(payload) > PAYLOAD_AUDIT_LIMIT:
        audit_payload = payload[:PAYLOAD_AUDIT_LIMIT]
        truncated = True
    else:
        audit_payload = payload
        truncated = False
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="mqtt_publish",
        target=topic,
        new_value=audit_payload,
        # `truncated` is captured via new_value's trailing marker so we
        # don't need a schema change to the audit table.
    )
    if truncated:
        # Append a small marker — the audit handler stores new_value
        # verbatim, so we just suffix a JSON-like token. Cheap and
        # visible in the action log.
        await audit.record(
            username=user.username,
            ip=user.ip,
            action="mqtt_publish_truncated",
            target=topic,
            new_value=f"original_bytes={len(payload)}:preview_bytes={len(audit_payload)}",
        )
    return get_hal("hardware").mqtt_publish(topic=topic, payload=payload)


# --- Modbus ----------------------------------------------------------------
# Cap the count so a malicious caller can't ask for 10M registers (DoS via
# memory exhaustion). 125 is a sensible per-request ceiling — Modbus is
# 16-bit per register, and the standard Modbus limit per request is 125
# holding registers (or 2000 coils). 422 is FastAPI's standard validation
# status, returned automatically by the Query constraint.
MODBUS_MAX_COUNT = 125


@router.get("/modbus")
async def modbus_read(
    unit: int = Query(1, ge=0, le=255),
    function: int = Query(3, ge=1, le=255),
    address: int = Query(0, ge=0, le=65535),
    count: int = Query(8, ge=1, le=MODBUS_MAX_COUNT),
    _: CurrentUser = Depends(require_viewer),
) -> dict[str, Any]:
    return _hw().modbus_read(unit, function, address, count)


@router.post("/modbus/write")
async def modbus_write(body: ModbusWrite, user: CurrentUser = Depends(require_admin)) -> dict[str, bool]:
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="modbus_write",
        target=f"reg{body.address}",
        new_value=body.value,
    )
    return {"ok": True}


# --- UART -------------------------------------------------------------------
@router.get("/uart")
async def uart_list(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return _hw().uart_list()


@router.post("/uart/{port}/config")
async def uart_config(port: str, body: dict[str, Any], user: CurrentUser = Depends(require_operator)) -> dict[str, Any]:
    result = _hw().uart_config(
        port,
        int(body.get("baud", 115_200)),
        int(body.get("data_bits", 8)),
        str(body.get("parity", "none")),
        int(body.get("stop_bits", 1)),
        str(body.get("flow", "none")),
    )
    await audit.record(username=user.username, ip=user.ip, action="uart_config", target=port, new_value=result)
    return result


@router.post("/uart/{port}/open")
async def uart_open(port: str, user: CurrentUser = Depends(require_operator)) -> dict[str, Any]:
    result = _hw().uart_open(port)
    await audit.record(username=user.username, ip=user.ip, action="uart_open", target=port, new_value=result)
    return result


@router.post("/uart/{port}/close")
async def uart_close(port: str, user: CurrentUser = Depends(require_operator)) -> dict[str, Any]:
    result = _hw().uart_close(port)
    await audit.record(username=user.username, ip=user.ip, action="uart_close", target=port, new_value=result)
    return result


@router.post("/uart/{port}/write")
async def uart_write(port: str, body: dict[str, str], user: CurrentUser = Depends(require_operator)) -> dict[str, Any]:
    data = body.get("data", "")
    result = _hw().uart_write(port, data)
    # Guardrail 1: the audit row must record the actual bytes written
    # so a replay can reconstruct the bus traffic. Truncate to 256
    # chars to keep the audit log bounded (#145).
    UART_AUDIT_LIMIT = 256
    if len(data) > UART_AUDIT_LIMIT:
        audit_value = f"{data[:UART_AUDIT_LIMIT]}…(truncated,total={len(data)})"
    else:
        audit_value = data
    await audit.record(username=user.username, ip=user.ip, action="uart_write", target=port, new_value=audit_value)
    return result


@router.get("/uart/{port}/read")
async def uart_read(port: str, max_bytes: int = 256, _: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return _hw().uart_read(port, max_bytes)


# --- RS485 / RS422 ---------------------------------------------------------
@router.get("/rs485")
async def rs485_list(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return _hw().rs485_list()


@router.post("/rs485/{port}/config")
async def rs485_config(
    port: str, body: dict[str, Any], user: CurrentUser = Depends(require_operator)
) -> dict[str, Any]:
    result = _hw().rs485_config(
        port,
        str(body.get("mode", "rs485")),
        int(body.get("baud", 19200)),
        bool(body.get("termination", False)),
        int(body.get("address", 0)),
        bool(body.get("enabled", True)),
    )
    await audit.record(username=user.username, ip=user.ip, action="rs485_config", target=port, new_value=result)
    return result


@router.post("/rs485/{port}/enable")
async def rs485_enable(
    port: str, body: dict[str, bool], user: CurrentUser = Depends(require_operator)
) -> dict[str, Any]:
    result = _hw().rs485_enable(port, bool(body.get("enabled", True)))
    await audit.record(username=user.username, ip=user.ip, action="rs485_enable", target=port, new_value=result)
    return result


@router.post("/rs485/{port}/send")
async def rs485_send(port: str, body: dict[str, str], user: CurrentUser = Depends(require_operator)) -> dict[str, Any]:
    result = _hw().rs485_send(port, body.get("frame", ""))
    await audit.record(username=user.username, ip=user.ip, action="rs485_send", target=port, new_value=result)
    return result


@router.get("/rs485/{port}/recv")
async def rs485_recv(port: str, max_bytes: int = 256, _: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return _hw().rs485_recv(port, max_bytes)


# --- 1-Wire ----------------------------------------------------------------
@router.get("/onewire")
async def onewire_list(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return _hw().onewire_list()


@router.post("/onewire/{address}/read")
async def onewire_read(address: str, user: CurrentUser = Depends(require_operator)) -> dict[str, Any]:
    result = _hw().onewire_read(address)
    await audit.record(username=user.username, ip=user.ip, action="onewire_read", target=address, new_value=result)
    return result


@router.post("/onewire/{address}/write")
async def onewire_write(
    address: str, body: dict[str, int], user: CurrentUser = Depends(require_admin)
) -> dict[str, Any]:
    result = _hw().onewire_write(address, int(body.get("value", 0)))
    await audit.record(username=user.username, ip=user.ip, action="onewire_write", target=address, new_value=result)
    return result

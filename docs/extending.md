# Extending the Dashboard

A walkthrough of the **three-layer pattern** that every feature in this
codebase follows. New contributors should read this end-to-end before
writing code; it's faster than reverse-engineering the conventions from
existing files.

> **TL;DR.** One feature = one HAL method + one router endpoint + one
> SPA page + one test. Each layer is a small, well-defined change.

---

## The pattern in one diagram

```
┌────────────┐   envelope    ┌────────────┐   JSON    ┌────────────┐
│ HAL module │ ────────────► │   Router   │ ────────► │  SPA page  │
│ (sync)     │   simulated   │ (async)    │           │ (Vue 3)    │
└────────────┘   applied     └────────────┘           └────────────┘
       │                            │
       │      seed defaults         │    role guard
       │      register in           │    + audit.record
       │      registry.py           │
       ▼                            ▼
   detect()                     tests/conftest.py
                                (per-test fixture)
```

Three layers, four files. That's the whole shape.

---

## Layer 1 — HAL (Hardware Abstraction)

Every hardware-facing module is a `HalModule` subclass
(`app/hal/base.py`) registered in `app/hal/registry.py`. Two
non-negotiable rules:

1. **`detect()` returns True iff the real interface is present.**
   The constructor sets `self._real = (not force_sim) and self.detect()`.
2. **Every public method returns `self.envelope({...})`** — a dict
   that includes `"simulated": true|false`. The UI banner and
   `GET /api/system/simulation` depend on this envelope; never return
   raw data.

Use the simulation helpers in `app/hal/base.py`:

- `sim_rng(*seed_parts)` — deterministic RNG so simulated topology
  (serials, MACs, addresses) is stable across calls/processes.
- `sim_wave(period_s, lo, hi, phase=0.0)` — smooth time-varying
  value in `[lo, hi]` for animated live readings.

### Add a new HAL module (e.g. "IMU" sensor)

```python
# app/hal/imu.py
"""Inertial measurement unit (IMU) HAL — section 8 sensors."""

from __future__ import annotations

from typing import Any
from app.hal.base import HalModule, path_exists, sim_rng, sim_wave


class ImuHal(HalModule):
    name = "imu"

    def detect(self) -> bool:
        # /dev/iio:device0 is the kernel IIO node for the first IMU
        return path_exists("/dev/iio/device0") or path_exists("/sys/bus/iio/devices/iio:device0")

    def identity(self) -> dict[str, Any]:
        rng = sim_rng("imu", "identity")
        return self.envelope({
            "model": "BMI270",
            "axes": ["x", "y", "z"],
        })

    def reading(self) -> dict[str, Any]:
        if self._real:
            # read /sys/bus/iio/devices/iio:device0/in_accel_x_raw
            x = self._read_iio("in_accel_x_raw")
            y = self._read_iio("in_accel_y_raw")
            z = self._read_iio("in_accel_z_raw")
            return self.envelope({"x": x, "y": y, "z": z, "applied": True})
        # Simulated: smooth time-varying read so the chart animates
        return self.envelope({
            "x": round(sim_wave(2.0, -2.0, 2.0, phase=0.0), 3),
            "y": round(sim_wave(2.0, -2.0, 2.0, phase=1.0), 3),
            "z": round(sim_wave(2.0, -2.0, 2.0, phase=2.0), 3),
            "applied": False,
        })
```

Then register the module:

```python
# app/hal/registry.py  — append to the `mods` list
from app.hal.imu import ImuHal

mods: list[Any] = [
    SystemHal(force),
    # ... existing modules ...
    ImuHal(force),  # <-- new
]
```

The new module is now reachable from any router via
`get_hal("imu")`.

---

## Layer 2 — Router (HTTP surface)

Routers are thin: they validate input, call the HAL, and write an
audit row for any state change. Keep them short — anything more
than ~80 lines is a sign that the logic belongs in the HAL or a
service module.

### Add a new endpoint (e.g. `GET /api/imu/reading`)

```python
# app/routers/imu.py
"""Inertial measurement unit (IMU) — section 8 sensors."""

from __future__ import annotations

from typing import Any
from fastapi import APIRouter, Depends

from app.auth.deps import CurrentUser, require_viewer
from app.hal.registry import get_hal

router = APIRouter(prefix="/api/imu", tags=["imu"])


@router.get("/reading")
async def reading(_: CurrentUser = Depends(require_viewer)) -> dict[str, Any]:
    return get_hal("imu").reading()
```

Then register the router in `app/main.py`:

```python
# app/main.py — inside create_app()
from app.routers import imu

app.include_router(imu.router)
```

### Privileged writes

For any state-changing endpoint (the audit log is the source of
truth, so any write gets one):

```python
from app.auth.deps import require_admin
from app.db import audit
from app.util.confirm import require_confirm

@router.post("/reboot")
async def reboot(
    body: RebootBody | None = None,
    user: CurrentUser = Depends(require_admin),
) -> dict[str, bool]:
    # Lockout-capable actions require explicit confirm (req.md §7).
    require_confirm(body.model_dump() if body is not None else None)
    await get_hal("system").reboot()
    await audit.record(
        username=user.username,
        ip=user.ip,
        action="reboot",
        target="system",
    )
    return {"ok": True}
```

### Per-input pydantic models

Use a pydantic model instead of `dict[str, Any]` for any body. The
project has a shared base class for `extra="forbid"` rejection:

```python
from app.util.models import _StrictModel
from pydantic import Field

class ImuCalibrate(_StrictModel):
    """Body for POST /api/imu/calibrate."""
    offset_x: float = Field(ge=-10.0, le=10.0)
    offset_y: float = Field(ge=-10.0, le=10.0)
    offset_z: float = Field(ge=-10.0, le=10.0)
    confirm: bool = False
```

`extra="forbid"` makes pydantic 422 on unknown keys, so a typo
(`offsetX` vs `offset_x`) fails fast instead of silently landing
in the DB.

---

## Layer 3 — SPA page (Vue 3)

The SPA is a zero-build Vue 3 app served from `static/`. New pages
are added to the hash-router in `static/js/pages/`.

### Add a new page (e.g. "IMU live")

1. **Create the page module:**

   ```js
   // static/js/pages/imu.js
   const ImuPage = {
     template: `
       <section>
         <h1>IMU Live</h1>
         <div v-if="reading" class="card">
           <p>X: {{ reading.x }} g</p>
           <p>Y: {{ reading.y }} g</p>
           <p>Z: {{ reading.z }} g</p>
         </div>
       </section>
     `,
     data() { return { reading: null } },
     async mounted() { await this.refresh(); },
     methods: {
       async refresh() {
         const r = await api.get('/api/imu/reading');
         this.reading = r;
       },
     },
   };
   ```

2. **Register the route in the SPA shell:**

   ```js
   // static/js/app.js (or wherever the router is)
   import { ImuPage } from './pages/imu.js';
   router.addRoute({ path: '/imu', component: ImuPage });
   ```

3. **Add a sidebar link:**

   ```html
   <!-- static/index.html — sidebar nav -->
   <router-link to="/imu">IMU</router-link>
   ```

### No build step

The SPA loads its libraries from `static/vendor/` (vendored at
install time, see `make fetch-vendor`); there is no webpack/vite
step. Edits are picked up on the next page reload.

---

## Layer 4 — Test

Three rules:

1. **One file per feature under `tests/`** — `test_imu.py` for the
   IMU example.
2. **Use the existing `client` and `tokens` fixtures** (see
   `tests/conftest.py`). They give you a fresh hermetic
   environment for every test (#141).
3. **The route-driven smoke test picks up new read endpoints
   automatically**; add a targeted test for new write flows.

### Example: IMU tests

```python
# tests/test_imu.py
"""Tests for the IMU HAL + router (new HAL module example)."""


def test_imu_identity_returns_envelope(client, tokens):
    r = client.get("/api/imu/reading", headers={"Authorization": f"Bearer {tokens['admin']}"})
    assert r.status_code == 200, r.text
    body = r.json()
    # Every HAL method returns an envelope with simulated/applied.
    assert "simulated" in body
    assert "applied" in body


def test_imu_reading_has_three_axes(client, tokens):
    r = client.get("/api/imu/reading", headers={"Authorization": f"Bearer {tokens['admin']}"})
    body = r.json()
    assert "x" in body and "y" in body and "z" in body
    # Simulated: in [-2, 2] g range.
    for axis in ("x", "y", "z"):
        assert -2.0 <= body[axis] <= 2.0
```

---

## End-to-end example: "Add a new sensor"

Suppose you want to add a new environmental sensor (BME280:
temperature, humidity, pressure) to the dashboard.

### Step 1 — HAL (`app/hal/bme280.py`)

```python
class Bme280Hal(HalModule):
    name = "bme280"

    def detect(self) -> bool:
        return path_exists("/dev/i2c-1") and self._probe_i2c(0x76)

    def reading(self) -> dict[str, Any]:
        if self._real:
            t, h, p = self._read_bme280()
            return self.envelope({"temperature_c": t, "humidity_pct": h, "pressure_hpa": p, "applied": True})
        # Simulated: smooth animated values.
        return self.envelope({
            "temperature_c": round(sim_wave(60, 18, 26, phase=0), 2),
            "humidity_pct": round(sim_wave(120, 35, 65, phase=0), 2),
            "pressure_hpa": round(sim_wave(600, 1005, 1025, phase=0), 2),
            "applied": False,
        })
```

### Step 2 — Register

Add to `app/hal/registry.py`:
```python
from app.hal.bme280 import Bme280Hal
mods.append(Bme280Hal(force))
```

### Step 3 — Router (`app/routers/bme280.py`)

```python
from fastapi import APIRouter, Depends
from app.auth.deps import CurrentUser, require_viewer
from app.hal.registry import get_hal

router = APIRouter(prefix="/api/bme280", tags=["bme280"])


@router.get("/reading")
async def reading(_: CurrentUser = Depends(require_viewer)):
    return get_hal("bme280").reading()
```

Add to `app/main.py`:
```python
from app.routers import bme280
app.include_router(bme280.router)
```

### Step 4 — SPA page (`static/js/pages/bme280.js`)

```js
const Bme280Page = {
  template: `
    <section>
      <h1>Environmental Sensor (BME280)</h1>
      <div v-if="r" class="card">
        <p>Temperature: {{ r.temperature_c }} °C</p>
        <p>Humidity: {{ r.humidity_pct }} %</p>
        <p>Pressure: {{ r.pressure_hpa }} hPa</p>
      </div>
    </section>
  `,
  data() { return { r: null } },
  async mounted() {
    setInterval(async () => {
      this.r = await api.get('/api/bme280/reading');
    }, 1000);
  },
};
```

### Step 5 — Test (`tests/test_bme280.py`)

```python
def test_bme280_reading(client, tokens):
    r = client.get("/api/bme280/reading", headers={"Authorization": f"Bearer {tokens['admin']}"})
    assert r.status_code == 200
    body = r.json()
    assert "simulated" in body
    assert 15 <= body["temperature_c"] <= 30
    assert 30 <= body["humidity_pct"] <= 70
    assert 1000 <= body["pressure_hpa"] <= 1030
```

### Step 6 — Validate

```bash
make validate  # ruff + mypy + pytest
```

That's the full loop. ~5 file additions, ~100 lines of code.

---

## Conventions to match

These apply to *every* new file you create; the project enforces
them via the linters and the validate gate.

| Area | Rule |
| --- | --- |
| Imports | `from __future__ import annotations` at the top of every `.py` file. |
| Docstrings | First paragraph is the *what*, second is the *why*. |
| Pydantic | All request/response bodies are `BaseModel` (or `_StrictModel` for `extra="forbid"`). |
| Auth | Use `Depends(require_viewer / require_operator / require_admin)`. |
| Audit | Any state change must call `await audit.record(...)` with the requesting user. |
| Errors | Raise `ApiError(code, message, status_code)` — never raw `HTTPException`. |
| HAL | Sync methods, return `self.envelope({...})` with `"simulated"` + `"applied"`. |
| Tests | Use the shared `client` and `tokens` fixtures; one file per feature. |
| Lint | `make validate` must pass — ruff check + format + mypy strict + pytest 85% coverage. |

---

## Where to look for examples

| Pattern | Best example file |
| --- | --- |
| New HAL module (sync, simulated + real) | `app/hal/thermal.py` |
| New router with audit | `app/routers/sysadmin.py` |
| Pydantic body with bounds + `extra="forbid"` | `app/util/models.py::_ThresholdsUpdate` |
| Per-kind enum dispatch (interfaces.configure) | `app/routers/interfaces.py` |
| Rollback plan / two-phase commit | `app/tasks/apply_state.py` |
| WebSocket broadcast | `app/ws/routes.py` |
| SPA page with live polling | `static/js/pages/overview.js` |



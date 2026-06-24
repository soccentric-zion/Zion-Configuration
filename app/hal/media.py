"""Media HAL: CSI/DSI camera & display + BCM2712 codec/compute blocks.

Covers requirement sections 2 (Camera & Display over MIPI CSI/DSI, including a
live feed) and 3 (SoC media/codec/compute blocks: GPU VideoCore VII, HEVC/H.265
hardware decoder, V4L2 *software* encode — there is no hardware H.264 encoder on
this SoC — ISP and HVS).

The router persists enable state + config in ``media_blocks``; this HAL provides
detection, capability/status reporting, live frame generation for the camera
stream, and start/stop of decode/encode jobs (simulated, deterministic).
"""

from __future__ import annotations

import base64
import time
from typing import Any

from app.hal.base import HalModule, path_exists, sim_rng, sim_wave

# Canonical CSI/DSI ports and codec/compute blocks.
CAMERA_DEFAULTS: list[dict[str, Any]] = [
    {
        "name": "csi0",
        "kind": "camera",
        "config": {"sensor": "imx708", "width": 1920, "height": 1080, "fps": 30, "format": "YUV420", "isp": True},
    },
    {
        "name": "csi1",
        "kind": "camera",
        "config": {"sensor": "ov5647", "width": 1280, "height": 720, "fps": 30, "format": "YUV420", "isp": True},
    },
]
DISPLAY_DEFAULTS: list[dict[str, Any]] = [
    {
        "name": "dsi0",
        "kind": "display",
        "config": {"panel": "7inch-touch", "width": 800, "height": 480, "refresh_hz": 60},
    },
    {"name": "dsi1", "kind": "display", "config": {"panel": "none", "width": 1280, "height": 720, "refresh_hz": 60}},
]
BLOCK_DEFAULTS: list[dict[str, Any]] = [
    {"name": "gpu", "kind": "gpu", "config": {}},
    {"name": "hevc", "kind": "decoder", "config": {}},
    {"name": "encode", "kind": "encoder", "config": {}},
    {
        "name": "isp",
        "kind": "isp",
        "config": {"exposure": "auto", "awb": "auto", "lens_shading": True, "denoise": "cdn_fast"},
    },
    {"name": "hvs", "kind": "scaler", "config": {"planes": 3}},
]


class MediaHal(HalModule):
    name = "media"

    def __init__(self, force_sim: bool = False) -> None:
        super().__init__(force_sim)
        self._jobs: dict[str, dict[str, Any]] = {}

    def detect(self) -> bool:
        return path_exists("/dev/video0") or path_exists("/dev/dri/card0")

    def camera_defaults(self) -> list[dict[str, Any]]:
        return [dict(d, config=dict(d["config"])) for d in CAMERA_DEFAULTS]

    def display_defaults(self) -> list[dict[str, Any]]:
        return [dict(d, config=dict(d["config"])) for d in DISPLAY_DEFAULTS]

    def block_defaults(self) -> list[dict[str, Any]]:
        return [dict(d, config=dict(d["config"])) for d in BLOCK_DEFAULTS]

    # --- capability / status ----------------------------------------------
    def block_status(self, name: str, kind: str) -> dict[str, Any]:
        """Capability + live usage for a codec/compute block."""
        rng = sim_rng("block", name)
        if kind == "gpu":
            return self.envelope(
                {
                    "model": "VideoCore VII",
                    "apis": ["OpenGL ES 3.1", "Vulkan 1.2"],
                    "usage_pct": round(sim_wave(15, 2, 65), 1),
                    "freq_mhz": 800,
                    "temperature_c": round(sim_wave(40, 40, 70), 1),
                }
            )
        if kind == "decoder":
            return self.envelope(
                {
                    "codec": "HEVC/H.265",
                    "hardware": True,
                    "max": "4Kp60",
                    "usage_pct": round(sim_wave(20, 0, 80), 1),
                }
            )
        if kind == "encoder":
            # Pi 5 / CM5 removed the dedicated H.264 hardware encoder.
            return self.envelope(
                {
                    "codec": "H.264 / H.265",
                    "hardware": False,
                    "path": "software (V4L2 stateful)",
                    "note": "No dedicated hardware H.264 encoder on BCM2712; encode runs in software.",
                    "usage_pct": round(sim_wave(18, 0, 90), 1),
                }
            )
        if kind == "isp":
            return self.envelope(
                {
                    "pipeline": "rp1-cfe / pisp",
                    "stages": ["black-level", "lens-shading", "demosaic", "awb", "denoise", "tone-map"],
                    "throughput_mpix_s": round(sim_wave(12, 100, 600), 1),
                }
            )
        if kind == "scaler":
            return self.envelope(
                {
                    "block": "HVS (Hardware Video Scaler)",
                    "planes": 3,
                    "compositing": True,
                    "usage_pct": round(sim_wave(22, 1, 40), 1),
                }
            )
        return self.envelope({"status": "unknown", "rng": rng.random()})

    def camera_status(self, name: str, config: dict[str, Any]) -> dict[str, Any]:
        return self.envelope(
            {
                "name": name,
                "sensor": config.get("sensor", "unknown"),
                "streaming": True,
                "fps_actual": round(sim_wave(5, config.get("fps", 30) - 2, config.get("fps", 30)), 1),
                "dropped": 0,
            }
        )

    # --- live camera frame (streamed over /ws/camera) ----------------------
    def frame(self, name: str, seq: int, config: dict[str, Any]) -> dict[str, Any]:
        """Return one simulated camera frame as a base64 SVG data payload.

        A real deployment routes V4L2 capture (through the ISP) here; for the
        simulation we synthesize a lightweight SVG so the dashboard renders a
        moving live feed with zero binary-codec dependencies.
        """
        w = int(config.get("width", 1920))
        h = int(config.get("height", 1080))
        vw, vh = 480, int(480 * h / w) if w else 270
        x = int((seq * 7) % vw)
        bar = int(sim_wave(3, 20, vw - 60))
        ts = time.strftime("%H:%M:%S")
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{vw}" height="{vh}" viewBox="0 0 {vw} {vh}">'
            f'<rect width="{vw}" height="{vh}" fill="#11161d"/>'
            f'<rect x="{bar}" y="0" width="40" height="{vh}" fill="#1d2733"/>'
            f'<circle cx="{x}" cy="{vh // 2}" r="26" fill="#3e7c7b"/>'
            f'<text x="10" y="22" fill="#e4ded2" font-family="monospace" font-size="14">{name} {ts} f{seq}</text>'
            f'<text x="10" y="{vh - 12}" fill="#9aa4b2" font-family="monospace" font-size="12">'
            f'{w}x{h} {config.get("format", "YUV420")} (simulated)</text>'
            f"</svg>"
        )
        b64 = base64.b64encode(svg.encode()).decode()
        return {"name": name, "seq": seq, "ts": time.time(), "mime": "image/svg+xml", "data": b64}

    # --- decode / encode jobs ---------------------------------------------
    def start_job(self, kind: str, source: str, sink: str) -> dict[str, Any]:
        rng = sim_rng("job", kind, source, time.time())
        job_id = f"{kind}-{rng.randrange(0, 1 << 24):06x}"
        self._jobs[job_id] = {
            "job_id": job_id,
            "kind": kind,
            "source": source,
            "sink": sink,
            "status": "running",
            "started": time.time(),
            "hardware": kind == "decode",
        }
        return self.envelope(dict(self._jobs[job_id]))

    def job_status(self, job_id: str) -> dict[str, Any]:
        job = self._jobs.get(job_id)
        if job is None:
            return self.envelope({"job_id": job_id, "status": "unknown"})
        elapsed = time.time() - job["started"]
        fps = round(sim_wave(4, 24, 62 if job["kind"] == "decode" else 30), 1)
        return self.envelope(
            {**job, "elapsed_s": round(elapsed, 1), "fps": fps, "throughput_mbps": round(fps * 0.4, 1)}
        )

    def stop_job(self, job_id: str) -> dict[str, Any]:
        job = self._jobs.pop(job_id, None)
        return self.envelope({"job_id": job_id, "stopped": job is not None})

    def jobs(self) -> dict[str, Any]:
        return self.envelope({"jobs": list(self._jobs.values())})

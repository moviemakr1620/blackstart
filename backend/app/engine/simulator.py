from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app.adapters.nut import classify_status

# EcoFlow Delta 3 Plus (approx) — used only by the built-in simulator
DELTA3_PLUS_WH = 1024.0
DELTA3_PLUS_MAX_W = 1800.0


@dataclass
class Simulator:
    charge: float = 100.0
    load_watts: float = 280.0
    capacity_wh: float = DELTA3_PLUS_WH
    grid: bool = True
    last_tick: float = field(default_factory=time.monotonic)

    def tick(self) -> None:
        now = time.monotonic()
        dt = max(0.0, now - self.last_tick)
        self.last_tick = now
        if self.grid:
            # Charge toward 100%
            if self.charge < 100:
                # ~400W charge rate equivalent
                added = (400.0 * dt / 3600.0) / self.capacity_wh * 100.0
                self.charge = min(100.0, self.charge + added)
        else:
            drain_wh = self.load_watts * dt / 3600.0
            self.charge = max(0.0, self.charge - (drain_wh / self.capacity_wh * 100.0))

    def pull_plug(self) -> None:
        self.tick()
        self.grid = False

    def restore_grid(self) -> None:
        self.tick()
        self.grid = True

    def set_charge(self, value: float) -> None:
        self.charge = max(0.0, min(100.0, float(value)))
        self.last_tick = time.monotonic()

    def set_load(self, watts: float) -> None:
        self.load_watts = max(0.0, min(DELTA3_PLUS_MAX_W, float(watts)))

    def snapshot(self) -> dict[str, Any]:
        self.tick()
        if self.grid and self.charge < 99.5:
            raw = "OL CHRG"
        elif self.grid:
            raw = "OL"
        elif self.charge <= 15:
            raw = "OB LB DISCHRG"
        else:
            raw = "OB DISCHRG"
        status, on_battery, charging = classify_status(raw)
        remaining_wh = self.capacity_wh * (self.charge / 100.0)
        runtime = (remaining_wh / self.load_watts * 3600.0) if self.load_watts > 0 and not self.grid else None
        if self.grid:
            runtime = remaining_wh / max(self.load_watts, 1.0) * 3600.0
        load_pct = (self.load_watts / DELTA3_PLUS_MAX_W) * 100.0
        return {
            "status_raw": raw,
            "status": status,
            "on_battery": on_battery,
            "charging": charging,
            "charge": round(self.charge, 2),
            "runtime_sec": round(runtime, 1) if runtime is not None else None,
            "load_pct": round(load_pct, 2),
            "watts": round(self.load_watts, 1),
            "input_voltage": 120.0 if self.grid else 0.0,
            "output_voltage": 120.0,
            "battery_voltage": 48.0 + (self.charge / 100.0) * 6.0,
            "temperature": 29.0,
            "model": "DELTA 3 Plus (simulator)",
            "device": "ecoflow-sim",
            "raw": {
                "ups.status": raw,
                "battery.charge": self.charge,
                "device.model": "DELTA 3 Plus",
            },
        }

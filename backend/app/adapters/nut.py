from __future__ import annotations

import asyncio
import re
from typing import Any

VAR_RE = re.compile(r'^VAR\s+\S+\s+(\S+)\s+"(.*)"\s*$')
UPS_RE = re.compile(r'^UPS\s+(\S+)\s+"(.*)"\s*$')


class NutError(RuntimeError):
    pass


class NutClient:
    """Minimal NUT (Network UPS Tools) upsd client — TCP port 3493."""

    def __init__(
        self,
        host: str,
        port: int = 3493,
        username: str = "",
        password: str = "",
        timeout: float = 6.0,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.timeout = timeout

    async def _session(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port),
            timeout=self.timeout,
        )
        return reader, writer

    async def _cmd(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        command: str,
    ) -> list[str]:
        writer.write((command + "\n").encode())
        await writer.drain()
        lines: list[str] = []
        while True:
            raw = await asyncio.wait_for(reader.readline(), timeout=self.timeout)
            if not raw:
                break
            line = raw.decode(errors="replace").rstrip("\r\n")
            if line.startswith("ERR "):
                raise NutError(line)
            if line.startswith("BEGIN LIST"):
                lines = []
                continue
            if line.startswith("END LIST"):
                return lines
            if line.startswith("OK"):
                return lines or [line]
            if line.startswith("VAR ") or line.startswith("UPS ") or line.startswith("CMD "):
                # Single GET VAR returns one VAR line without BEGIN/END
                if command.startswith("GET ") and not command.startswith("GET LIST"):
                    return [line]
                lines.append(line)
                continue
            if line:
                lines.append(line)
                if command.startswith("GET "):
                    return lines
        return lines

    async def list_ups(self) -> list[tuple[str, str]]:
        reader, writer = await self._session()
        try:
            await self._auth(reader, writer)
            lines = await self._cmd(reader, writer, "LIST UPS")
            out: list[tuple[str, str]] = []
            for line in lines:
                m = UPS_RE.match(line)
                if m:
                    out.append((m.group(1), m.group(2)))
            return out
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def list_vars(self, ups_name: str) -> dict[str, str]:
        reader, writer = await self._session()
        try:
            await self._auth(reader, writer, ups_name)
            lines = await self._cmd(reader, writer, f"LIST VAR {ups_name}")
            vars_: dict[str, str] = {}
            for line in lines:
                m = VAR_RE.match(line)
                if m:
                    vars_[m.group(1)] = m.group(2)
            return vars_
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _auth(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, ups_name: str = "") -> None:
        if not self.username:
            return
        await self._cmd(reader, writer, f"USERNAME {self.username}")
        if self.password:
            await self._cmd(reader, writer, f"PASSWORD {self.password}")
        if ups_name:
            try:
                await self._cmd(reader, writer, f"LOGIN {ups_name}")
            except NutError:
                # Some upsd builds do not require / accept LOGIN for read-only.
                pass


def flatten_peanut_or_nut(vars_: dict[str, Any]) -> dict[str, Any]:
    """Normalize dotted NUT keys, whether already flat or nested."""
    flat: dict[str, Any] = {}

    def walk(prefix: str, value: Any) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                key = f"{prefix}.{k}" if prefix else str(k)
                walk(key, v)
        else:
            if prefix:
                flat[prefix] = value

    walk("", vars_)
    # If the payload was already flat, walk still works.
    if not flat and vars_:
        return {str(k): v for k, v in vars_.items()}
    return flat


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def classify_status(raw: str) -> tuple[str, bool, bool]:
    """Return (status, on_battery, charging) from a NUT ups.status string."""
    tokens = set((raw or "").upper().split())
    on_battery = "OB" in tokens
    charging = "CHRG" in tokens
    if "LB" in tokens:
        status = "low_battery"
    elif on_battery:
        status = "on_battery"
    elif "OL" in tokens and charging:
        status = "charging"
    elif "OL" in tokens:
        status = "online"
    elif "FSD" in tokens:
        status = "low_battery"
    elif raw:
        status = raw.lower()
    else:
        status = "unknown"
    return status, on_battery, charging


def _lookup(flat: dict[str, Any], *keys: str) -> Any:
    lower = {str(k).lower().replace("_", "."): v for k, v in flat.items()}
    for key in keys:
        if key in flat and flat[key] not in (None, ""):
            return flat[key]
        alt = key.lower().replace("_", ".")
        if alt in lower and lower[alt] not in (None, ""):
            return lower[alt]
    return None


def snapshot_from_vars(vars_: dict[str, Any]) -> dict[str, Any]:
    flat = flatten_peanut_or_nut(vars_)
    raw_status = str(_lookup(flat, "ups.status", "ups_status") or "")
    status, on_battery, charging = classify_status(raw_status)
    charge = _num(_lookup(flat, "battery.charge"))
    runtime = _num(_lookup(flat, "battery.runtime"))
    watts = _num(
        _lookup(
            flat,
            "ups.realpower",
            "output.realpower",
            "ups.power",
            "output.power",
        )
    )
    nominal = _num(
        _lookup(
            flat,
            "ups.power.nominal",
            "ups.realpower.nominal",
            "output.realpower.nominal",
        )
    )
    load = _num(_lookup(flat, "ups.load", "output.load", "ups.load.percent"))
    # EcoFlow HID reports watts + rated watts, not a load percent.
    if load is None and watts is not None and nominal and nominal > 0:
        load = max(0.0, min(100.0, watts / nominal * 100.0))
    if watts is None and load is not None and nominal:
        watts = load / 100.0 * nominal

    eco_runtime = _num(_lookup(flat, "experimental.ecoflow.battery.charge.runtime"))
    if runtime is None:
        # Prefer energy math when we have charge + rated Wh + live watts.
        if charge is not None and nominal and watts and watts > 1:
            runtime = (charge / 100.0) * nominal * 3600.0 / watts
        elif eco_runtime is not None and 0 < eco_runtime < 7 * 24 * 3600:
            runtime = eco_runtime

    input_voltage = _num(
        _lookup(
            flat,
            "input.voltage",
            "input.L1-N.voltage",
            "input.L1.voltage",
        )
    )
    input_watts = _num(_lookup(flat, "input.realpower", "input.power"))
    return {
        "status_raw": raw_status,
        "status": status,
        "on_battery": on_battery,
        "charging": charging,
        "charge": charge,
        "runtime_sec": runtime,
        "load_pct": load,
        "watts": watts,
        "input_voltage": input_voltage,
        "input_watts": input_watts,
        "output_voltage": _num(_lookup(flat, "output.voltage", "output.L1-N.voltage")),
        "battery_voltage": _num(_lookup(flat, "battery.voltage")),
        "temperature": _num(_lookup(flat, "ups.temperature", "battery.temperature")),
        "model": str(_lookup(flat, "device.model", "ups.model") or ""),
        "device": str(_lookup(flat, "device.serial", "ups.id") or ""),
        "raw": flat,
        "reported_keys": sorted(flat.keys()),
    }

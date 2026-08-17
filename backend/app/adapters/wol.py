from __future__ import annotations

import asyncio
import socket


def _packet(mac: str) -> bytes:
    cleaned = mac.replace(":", "").replace("-", "").replace(".", "").strip().lower()
    if len(cleaned) != 12:
        raise ValueError(f"Invalid MAC address: {mac}")
    hw = bytes.fromhex(cleaned)
    return b"\xff" * 6 + hw * 16


def _send(mac: str, broadcast: str = "255.255.255.255", port: int = 9) -> None:
    packet = _packet(mac)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, (broadcast, port))
    finally:
        sock.close()


async def wake_on_lan(mac: str, broadcast: str = "255.255.255.255", port: int = 9) -> str:
    await asyncio.to_thread(_send, mac, broadcast, port)
    return f"WOL sent to {mac} via {broadcast}:{port}"

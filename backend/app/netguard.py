"""Outbound URL checks.

Blackstart has to reach private LAN addresses (that is its job), so it does not
block RFC1918. It does refuse link-local and cloud-metadata targets and odd
schemes, which have no legitimate use here and are the usual SSRF prizes.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

_BLOCKED_NETS = [
    ipaddress.ip_network("169.254.0.0/16"),  # link-local incl. 169.254.169.254 metadata
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("100.100.100.200/32"),  # Alibaba metadata
    ipaddress.ip_network("0.0.0.0/8"),
]
_BLOCKED_HOSTS = {"metadata.google.internal", "metadata", "instance-data"}


def _blocked_ip(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in net for net in _BLOCKED_NETS)


def check_host(host: str) -> None:
    h = (host or "").strip().strip("[]").lower()
    if not h:
        raise ValueError("Missing host")
    if h in _BLOCKED_HOSTS or _blocked_ip(h):
        raise ValueError(f"Refusing to contact {host}: link-local / metadata addresses are not allowed")
    try:
        infos = socket.getaddrinfo(h, None)
    except OSError:
        return  # unresolvable now; the request itself will fail cleanly
    for info in infos:
        if _blocked_ip(info[4][0]):
            raise ValueError(f"Refusing to contact {host}: it resolves to a link-local / metadata address")


def check_outbound_url(url: str) -> None:
    parts = urlsplit((url or "").strip())
    if parts.scheme not in {"http", "https"}:
        raise ValueError("URL must start with http:// or https://")
    check_host(parts.hostname or "")

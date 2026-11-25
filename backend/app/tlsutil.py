"""Build a TLS context from a pasted lab CA or server certificate.

Homelab certificates are often self-signed, missing modern CA extensions, or
issued to a DNS name while Blackstart connects by IP. A pasted PEM is treated
as a trust anchor (CA or pinned leaf) without Python 3.13's STRICT flags, and
hostname checking is skipped when connecting to an IP with a custom cert.
"""

from __future__ import annotations

import ipaddress
import re
import ssl
from typing import Any
from urllib.parse import urlsplit

PEM_BLOCK = re.compile(
    r"-----BEGIN ([A-Z0-9 ]+)-----(?:[A-Za-z0-9+/=\s]+)-----END \1-----"
)


def extract_certificates(text: str) -> list[str]:
    """Pull PEM certificate blocks out of openssl/browser dump text."""
    blocks: list[str] = []
    for match in PEM_BLOCK.finditer(text or ""):
        if "CERTIFICATE" in match.group(1).upper() and "REQUEST" not in match.group(1).upper():
            blocks.append(re.sub(r"\r\n?", "\n", match.group(0)).strip())
    return blocks


def host_from_address(address: str) -> str:
    raw = (address or "").strip()
    if "://" not in raw:
        raw = f"https://{raw}"
    host = urlsplit(raw).hostname or address
    return (host or "").strip("[]")


def is_ip_address(address: str) -> bool:
    host = host_from_address(address)
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def ssl_context_from_pem(pem: str, *, check_hostname: bool) -> ssl.SSLContext:
    certs = extract_certificates(pem)
    if not certs:
        raise ValueError(
            "The pasted text doesn't look like a PEM certificate. "
            "It should include at least one -----BEGIN CERTIFICATE----- block."
        )
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.check_hostname = check_hostname
    # Allow a pasted server (leaf) certificate to be the trust anchor, like pinning.
    if hasattr(ssl, "VERIFY_X509_PARTIAL_CHAIN"):
        ctx.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    # Python 3.13's create_default_context() also sets STRICT, which rejects
    # typical Proxmox / DSM lab CAs that have no Authority Key Identifier.
    if hasattr(ssl, "VERIFY_X509_STRICT"):
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
    ctx.load_verify_locations(cadata="\n".join(certs))
    return ctx


def verify_setting(
    *,
    verify_tls: bool,
    ca_pem: str = "",
    address: str = "",
    skip_hostname: bool = False,
) -> Any:
    """Value for httpx `verify=`: False, True, or an SSLContext."""
    if not verify_tls:
        return False
    pem = (ca_pem or "").strip()
    if not pem:
        return True
    check = not skip_hostname and not is_ip_address(address)
    return ssl_context_from_pem(pem, check_hostname=check)


def is_tls_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    name = type(exc).__name__.lower()
    if "ssl" in name or "certificate" in name:
        return True
    needles = (
        "certificate verify failed",
        "sslcertverificationerror",
        "ssl: certificate",
        "ssl handshake",
        "unable to get local issuer",
        "self signed certificate",
        "hostname mismatch",
        "ip address mismatch",
        "certificate is not valid for",
        "missing authority key identifier",
        "certificate_verify_failed",
    )
    return any(n in text for n in needles)


def explain_tls_error(
    exc: BaseException,
    *,
    address: str,
    verify: Any,
    has_custom_ca: bool | None = None,
) -> str:
    """Rewrite OpenSSL's wording into something a person can act on."""
    if not is_tls_error(exc):
        return str(exc)
    if has_custom_ca is None:
        has_custom_ca = not isinstance(verify, bool)
    verify_on = verify is not False
    text = str(exc)
    low = text.lower()
    target = host_from_address(address) or address or "this host"

    if not verify_on:
        return text

    hostname_fail = any(
        s in low
        for s in (
            "hostname mismatch",
            "ip address mismatch",
            "certificate is not valid for",
            "doesn't match either of",
            "does not match",
        )
    )
    if hostname_fail:
        return (
            f"The certificate is trusted, but it was issued for a different name than '{target}'. "
            "Connect with the name on the certificate, or paste the server certificate and leave the "
            "address as the IP — Blackstart will pin that cert and not require the name to match."
        )

    if not has_custom_ca:
        return (
            f"The HTTPS certificate for {target} is not trusted by the system "
            f"(unable to verify the issuer). Paste the lab CA or the server's certificate (PEM) on this "
            "host, save, then Test again. For Proxmox use /etc/pve/pve-root-ca.pem on the node. "
            "Or turn off 'Verify the HTTPS certificate' if you accept the risk of someone impersonating "
            "this host on the network."
        )

    return (
        f"The pasted certificate did not authenticate {target}. Use the CA that signed the server cert "
        "(Proxmox: /etc/pve/pve-root-ca.pem) or paste the server certificate itself from "
        f"`openssl s_client -connect {target}:PORT -showcerts`. Save, then Test again. "
        f"OpenSSL said: {text.split('certificate verify failed:')[-1].strip() or text}"
    )

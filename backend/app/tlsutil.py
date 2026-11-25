"""Build a TLS context from a pasted lab CA or server certificate.

Homelab certificates are often self-signed, missing modern CA extensions, or
issued to a DNS name while Blackstart connects by IP. A pasted PEM is treated
as a trust anchor (CA or pinned leaf). Hostname checking is skipped when
connecting to an IP with a custom cert.

If the peer's leaf certificate exactly equals a pasted certificate, the
connection is accepted (pinning) even when that cert is not a CA. On failure,
the error names the served cert's subject, issuer, and SHA256 fingerprint
alongside the pasted certs so a mismatch (for example a regenerated Proxmox
root vs an older node cert) is obvious.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import ssl
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import ExtensionOID

PEM_BLOCK = re.compile(
    r"-----BEGIN ([A-Z0-9 ]+)-----(?:[A-Za-z0-9+/=\s]+)-----END \1-----"
)

# Named constants are missing on some older Python builds; the OpenSSL bits are stable.
X509_V_FLAG_PARTIAL_CHAIN = getattr(ssl, "VERIFY_X509_PARTIAL_CHAIN", 0x80000)
X509_V_FLAG_STRICT = getattr(ssl, "VERIFY_X509_STRICT", 0x20)

_ALREADY_EXPLAINED = (
    "the pasted certificate did not authenticate",
    "the https certificate for",
    "the certificate is trusted, but",
    "served certificate:",
)


class TLSVerificationError(RuntimeError):
    """Human-readable TLS failure. Callers must not wrap this again."""


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


def is_http_url(address: str) -> bool:
    return (address or "").strip().lower().startswith("http://")


def uses_tls(address: str, use_https: bool = True) -> bool:
    """False when the target is plain HTTP, so Verify HTTPS must not apply."""
    raw = (address or "").strip().lower()
    if raw.startswith("http://"):
        return False
    if raw.startswith("https://"):
        return True
    return bool(use_https)


def _fingerprint(der: bytes) -> str:
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2))


@dataclass(frozen=True)
class CertInfo:
    der: bytes
    subject: str
    issuer: str
    sha256: str

    def line(self) -> str:
        return f"subject={self.subject}; issuer={self.issuer}; SHA256={self.sha256}"


def cert_info_from_pem(pem: str) -> CertInfo:
    cert = x509.load_pem_x509_certificate(pem.encode())
    der = cert.public_bytes(Encoding.DER)
    return CertInfo(der=der, subject=cert.subject.rfc4514_string(), issuer=cert.issuer.rfc4514_string(), sha256=_fingerprint(der))


def cert_info_from_der(der: bytes) -> CertInfo:
    cert = x509.load_der_x509_certificate(der)
    return CertInfo(der=der, subject=cert.subject.rfc4514_string(), issuer=cert.issuer.rfc4514_string(), sha256=_fingerprint(der))


def _leaf_signed_by(leaf: x509.Certificate, ca: x509.Certificate) -> bool:
    if leaf.issuer != ca.subject:
        return False
    pub = ca.public_key()
    algo = leaf.signature_hash_algorithm
    if algo is None:
        return False
    try:
        if isinstance(pub, rsa.RSAPublicKey):
            pub.verify(leaf.signature, leaf.tbs_certificate_bytes, padding.PKCS1v15(), algo)
            return True
        if isinstance(pub, ec.EllipticCurvePublicKey):
            pub.verify(leaf.signature, leaf.tbs_certificate_bytes, ec.ECDSA(algo))
            return True
    except (InvalidSignature, ValueError, TypeError):
        return False
    return False


def _hostname_matches(cert: x509.Certificate, hostname: str) -> bool:
    host = (hostname or "").strip("[]")
    if not host:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    try:
        san = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value
        dns_names = [n.lower() for n in san.get_values_for_type(x509.DNSName)]
        ips = list(san.get_values_for_type(x509.IPAddress))
    except x509.ExtensionNotFound:
        dns_names, ips = [], []
    if ip is not None:
        return any(ip == value for value in ips)
    host_l = host.lower()
    for name in dns_names:
        if name.startswith("*."):
            suffix = name[1:]  # .example.com
            rest = host_l.split(".", 1)
            if host_l.endswith(suffix) and len(rest) == 2 and f".{rest[1]}" == suffix:
                return True
        elif name == host_l:
            return True
    return False


def describe_mismatch(peer_der: bytes, pasted: list[CertInfo], address: str) -> str:
    target = host_from_address(address) or address or "this host"
    served = cert_info_from_der(peer_der)
    lines = [
        f"The pasted certificate did not authenticate {target}.",
        f"Served certificate: {served.line()}",
    ]
    if pasted:
        label = "Pasted certificate:" if len(pasted) == 1 else "Pasted certificates:"
        lines.append(label)
        for i, item in enumerate(pasted, 1):
            lines.append(f"  {i}. {item.line()}")
    else:
        lines.append("No pasted certificates could be parsed.")
    lines.append(
        "The served leaf does not match a pasted certificate and is not signed by a pasted CA. "
        "Paste the certificate the server actually presents (the first PEM block from "
        f"`openssl s_client -connect {target}:PORT -showcerts`), or paste the CA that signed that leaf. "
        "If a Proxmox root was regenerated, the node certificate may still be signed by the old CA."
    )
    return "\n".join(lines)


class _CheckingSSLObject:
    """Proxy around SSLObject that runs a pin/CA check after a successful handshake."""

    def __init__(self, obj: Any, accept: Callable[[Any, str | None], None], server_hostname: str | None) -> None:
        object.__setattr__(self, "_obj", obj)
        object.__setattr__(self, "_accept", accept)
        object.__setattr__(self, "_server_hostname", server_hostname)
        object.__setattr__(self, "_checked", False)

    def do_handshake(self, *args: Any, **kwargs: Any) -> None:
        self._obj.do_handshake(*args, **kwargs)
        if not self._checked:
            self._accept(self._obj, self._server_hostname)
            object.__setattr__(self, "_checked", True)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._obj, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {"_obj", "_accept", "_server_hostname", "_checked"}:
            object.__setattr__(self, name, value)
        else:
            setattr(self._obj, name, value)


def _install_pinning(ctx: ssl.SSLContext, accept: Callable[[Any, str | None], None]) -> None:
    orig_wrap_socket = ctx.wrap_socket
    orig_wrap_bio = ctx.wrap_bio

    def wrap_socket(
        sock: Any,
        server_side: bool = False,
        do_handshake_on_connect: bool = True,
        suppress_ragged_eofs: bool = True,
        server_hostname: str | None = None,
        session: Any = None,
    ) -> Any:
        ssock = orig_wrap_socket(
            sock,
            server_side=server_side,
            do_handshake_on_connect=False,
            suppress_ragged_eofs=suppress_ragged_eofs,
            server_hostname=server_hostname,
            session=session,
        )
        orig_hs = ssock.do_handshake

        def do_handshake(*args: Any, **kwargs: Any) -> None:
            orig_hs(*args, **kwargs)
            if not getattr(ssock, "_bs_pin_checked", False):
                accept(ssock, server_hostname)
                ssock._bs_pin_checked = True  # type: ignore[attr-defined]

        ssock.do_handshake = do_handshake  # type: ignore[method-assign]
        if do_handshake_on_connect:
            ssock.do_handshake()
        return ssock

    def wrap_bio(
        incoming: Any,
        outgoing: Any,
        server_side: bool = False,
        server_hostname: str | None = None,
        session: Any = None,
    ) -> Any:
        obj = orig_wrap_bio(
            incoming,
            outgoing,
            server_side=server_side,
            server_hostname=server_hostname,
            session=session,
        )
        return _CheckingSSLObject(obj, accept, server_hostname)

    ctx.wrap_socket = wrap_socket  # type: ignore[method-assign]
    ctx.wrap_bio = wrap_bio  # type: ignore[method-assign]


def ssl_context_from_pem(pem: str, *, check_hostname: bool, address: str = "") -> ssl.SSLContext:
    certs = extract_certificates(pem)
    if not certs:
        raise ValueError(
            "The pasted text doesn't look like a PEM certificate. "
            "It should include at least one -----BEGIN CERTIFICATE----- block."
        )
    pasted = [cert_info_from_pem(block) for block in certs]
    pinned_ders = {item.der for item in pasted}
    ca_certs = [x509.load_pem_x509_certificate(block.encode()) for block in certs]

    # Handshake with CERT_NONE so we can always read the peer leaf, then pin or
    # check a CA signature ourselves. The channel is still TLS; the server must
    # prove possession of the presented certificate's private key.
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    # Keep these flags on the context for tests/inspection; they also apply if
    # something uses the context as a normal trust store.
    ctx.verify_flags |= X509_V_FLAG_PARTIAL_CHAIN
    ctx.verify_flags &= ~X509_V_FLAG_STRICT
    try:
        ctx.load_verify_locations(cadata="\n".join(certs))
    except ssl.SSLError:
        pass

    def accept(sslobj: Any, server_hostname: str | None) -> None:
        peer_der = sslobj.getpeercert(binary_form=True)
        if not peer_der:
            raise ssl.SSLCertVerificationError("The server presented no certificate.")
        ok = peer_der in pinned_ders
        if not ok:
            leaf = x509.load_der_x509_certificate(peer_der)
            ok = any(_leaf_signed_by(leaf, ca) for ca in ca_certs)
        if not ok:
            raise ssl.SSLCertVerificationError(
                describe_mismatch(peer_der, pasted, address or server_hostname or "")
            )
        if check_hostname:
            host = server_hostname or host_from_address(address)
            if host and not _hostname_matches(x509.load_der_x509_certificate(peer_der), host):
                raise ssl.SSLCertVerificationError(
                    f"The certificate is trusted, but it was issued for a different name than '{host}'. "
                    "Connect with the name on the certificate, or paste the server certificate and leave the "
                    "address as the IP — Blackstart will pin that cert and not require the name to match."
                )

    _install_pinning(ctx, accept)
    return ctx


def verify_setting(
    *,
    verify_tls: bool,
    ca_pem: str = "",
    address: str = "",
    skip_hostname: bool = False,
    use_https: bool = True,
) -> Any:
    """Value for httpx `verify=`: False, True, or an SSLContext."""
    if not verify_tls or not uses_tls(address, use_https):
        return False
    pem = (ca_pem or "").strip()
    if not pem:
        return True
    check = not skip_hostname and not is_ip_address(address)
    return ssl_context_from_pem(pem, check_hostname=check, address=address)


def exception_text(exc: BaseException) -> str:
    """SSLError stringifies multiline messages as a tuple repr; unwrap that."""
    if exc.args and isinstance(exc.args[0], str):
        return exc.args[0]
    return str(exc)


def is_tls_error(exc: BaseException) -> bool:
    if isinstance(exc, TLSVerificationError):
        return True
    text = exception_text(exc).lower()
    if any(p in text for p in _ALREADY_EXPLAINED):
        return True
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
        "the pasted certificate did not authenticate",
        "the served leaf does not match",
    )
    return any(n in text for n in needles)


def explain_tls_error(
    exc: BaseException,
    *,
    address: str,
    verify: Any,
    has_custom_ca: bool | None = None,
) -> str:
    """Rewrite OpenSSL's wording into something a person can act on. Idempotent."""
    text = exception_text(exc)
    if any(p in text.lower() for p in _ALREADY_EXPLAINED):
        return text
    if not is_tls_error(exc):
        return text
    if has_custom_ca is None:
        has_custom_ca = not isinstance(verify, bool)
    verify_on = verify is not False
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
            "host, save, then Test again. For Proxmox use /etc/pve/pve-root-ca.pem on the node, or paste "
            "the first certificate from `openssl s_client -connect HOST:8006 -showcerts`. "
            "Or turn off 'Verify the HTTPS certificate' if you accept the risk of someone impersonating "
            "this host on the network."
        )

    return (
        f"The pasted certificate did not authenticate {target}. Use the CA that signed the server cert "
        "(Proxmox: /etc/pve/pve-root-ca.pem) or paste the server certificate itself from "
        f"`openssl s_client -connect {target}:PORT -showcerts`. Save, then Test again. "
        f"OpenSSL said: {text.split('certificate verify failed:')[-1].strip() or text}"
    )

"""TLS verification with a pasted lab CA or pinned server certificate, over an IP."""

from __future__ import annotations

import ssl
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from app.engine.executor import tls_verify
from app.main import app
from app.models import Host
from app.tlsutil import explain_tls_error, extract_certificates, ssl_context_from_pem, verify_setting
from conftest import ADMIN_PASSWORD
from tests.test_api import _login


def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _pem(cert: x509.Certificate) -> str:
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def make_lab_pki(*, with_ip_san: bool = False):
    """A Proxmox-like CA (CA:TRUE, no AKID) and a leaf issued to a DNS name."""
    now = datetime.now(timezone.utc)
    ca_key = _key()
    ca = (
        x509.CertificateBuilder()
        .subject_name(_name("Proxmox Virtual Environment"))
        .issuer_name(_name("Proxmox Virtual Environment"))
        .public_key(ca_key.public_key())
        .serial_number(1)
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    leaf_key = _key()
    sans: list[x509.GeneralName] = [x509.DNSName("pve.lab.local")]
    if with_ip_san:
        import ipaddress

        sans.append(x509.IPAddress(ipaddress.ip_address("127.0.0.1")))
    leaf = (
        x509.CertificateBuilder()
        .subject_name(_name("pve.lab.local"))
        .issuer_name(ca.subject)
        .public_key(leaf_key.public_key())
        .serial_number(2)
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    key_pem = leaf_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    return _pem(ca), _pem(leaf), key_pem


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"data":{"version":"lab-pve"}}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a) -> None:  # noqa: ARG002
        return


@pytest.fixture
def tls_server(tmp_path: Path):
    ca_pem, leaf_pem, key_pem = make_lab_pki(with_ip_san=False)
    cert = tmp_path / "leaf.pem"
    key = tmp_path / "key.pem"
    cert.write_text(leaf_pem)
    key.write_bytes(key_pem)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert), str(key))
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    port = httpd.server_address[1]
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.05)
    yield {"port": port, "url": f"https://127.0.0.1:{port}", "ca": ca_pem, "leaf": leaf_pem}
    httpd.shutdown()
    httpd.server_close()


def test_extracts_pem_from_openssl_s_client_junk():
    ca, leaf, _ = make_lab_pki()
    dump = (
        "Connecting to 192.168.1.10\n"
        "depth=1 CN = Proxmox Virtual Environment\n"
        + ca
        + "\n 0 s:CN=pve.lab.local\n"
        + leaf
        + "\nDONE\n"
    )
    blocks = extract_certificates(dump)
    assert len(blocks) == 2
    assert all("BEGIN CERTIFICATE" in b for b in blocks)


def test_lab_ca_without_akid_and_leaf_pin_work_over_ip(tls_server):
    url = tls_server["url"] + "/api2/json/version"
    # System trust must fail (self-signed lab CA).
    with pytest.raises(httpx.ConnectError):
        httpx.get(url, verify=True, timeout=3)

    ca_ctx = verify_setting(verify_tls=True, ca_pem=tls_server["ca"], address="127.0.0.1")
    assert isinstance(ca_ctx, ssl.SSLContext)
    assert ca_ctx.check_hostname is False
    assert httpx.get(url, verify=ca_ctx, timeout=3).status_code == 200

    leaf_ctx = verify_setting(verify_tls=True, ca_pem=tls_server["leaf"], address="127.0.0.1")
    assert httpx.get(url, verify=leaf_ctx, timeout=3).status_code == 200

    # openssl s_client dump containing leaf + junk still works (pin the leaf).
    dump = "depth=0 CN = pve.lab.local\n" + tls_server["leaf"] + "\nDONE\n"
    dump_ctx = verify_setting(verify_tls=True, ca_pem=dump, address="127.0.0.1")
    assert httpx.get(url, verify=dump_ctx, timeout=3).status_code == 200


def test_default_context_is_not_used_so_strict_flags_dont_reject_lab_ca():
    ca, leaf, _ = make_lab_pki()
    ctx = ssl_context_from_pem(ca, check_hostname=False)
    if hasattr(ssl, "VERIFY_X509_STRICT"):
        assert not (ctx.verify_flags & ssl.VERIFY_X509_STRICT)
    if hasattr(ssl, "VERIFY_X509_PARTIAL_CHAIN"):
        assert ctx.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN
    # The 3.13-style default context would reject this CA (missing AKID).
    if hasattr(ssl, "VERIFY_X509_STRICT"):
        default = ssl.create_default_context(cadata=ca)
        assert default.verify_flags & ssl.VERIFY_X509_STRICT


def test_tls_verify_reads_ca_pem_from_host_extra(tls_server):
    host = Host(
        name="pve",
        kind="proxmox",
        address="127.0.0.1",
        port=tls_server["port"],
        verify_tls=True,
        extra_json='{"ca_pem": "placeholder"}',
    )
    # inject real pem after construct
    import json as _json

    host.extra_json = _json.dumps({"ca_pem": tls_server["ca"]})
    ctx = tls_verify(host)
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.check_hostname is False
    url = tls_server["url"] + "/api2/json/version"
    assert httpx.get(url, verify=ctx, timeout=3).status_code == 200


def test_explain_issuer_error_mentions_paste_when_no_ca():
    err = Exception("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate (_ssl.c:992)")
    msg = explain_tls_error(err, address="192.168.1.20", verify=True, has_custom_ca=False)
    assert "not trusted" in msg.lower() or "paste" in msg.lower()
    assert "192.168.1.20" in msg
    assert "unable to get local issuer" not in msg or "Paste" in msg


def test_explain_hostname_mismatch():
    err = Exception("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: IP address mismatch, certificate is not valid for '192.168.1.20'.")
    msg = explain_tls_error(err, address="192.168.1.20", verify=object(), has_custom_ca=True)
    assert "different name" in msg.lower() or "issued" in msg.lower()


def test_host_test_endpoint_uses_saved_ca_and_optional_override(tls_server):
    with TestClient(app) as client:
        headers = _login(client)
        # Saved without a CA — Test should fail with a helpful message, not just raw OpenSSL.
        created = client.post(
            "/api/hosts",
            headers=headers,
            json={
                "name": "pve-lab",
                "kind": "proxmox",
                "address": "127.0.0.1",
                "port": tls_server["port"],
                "verify_tls": True,
                "credentials": {"token_id": "root@pam!bs", "token_secret": "x"},
                "extra": {},
            },
        )
        assert created.status_code == 200, created.text
        host_id = created.json()["id"]
        failed = client.post(f"/api/hosts/{host_id}/test", headers=headers)
        assert failed.status_code == 400
        assert "paste" in failed.json()["detail"].lower() or "trusted" in failed.json()["detail"].lower()
        # Unsaved override (form-style): paste the CA, blank would have meant keep.
        ok = client.post(
            f"/api/hosts/{host_id}/test",
            headers=headers,
            json={"verify_tls": True, "ca_pem": tls_server["ca"]},
        )
        assert ok.status_code == 200, ok.text
        # Override was not persisted.
        listed = client.get("/api/hosts", headers=headers).json()
        row = next(h for h in listed if h["id"] == host_id)
        assert not (row.get("extra") or {}).get("ca_pem")
        # Persist the leaf (pinning) and Test with an empty body (saved values).
        saved = client.put(
            f"/api/hosts/{host_id}",
            headers=headers,
            json={
                "name": "pve-lab",
                "kind": "proxmox",
                "address": "127.0.0.1",
                "port": tls_server["port"],
                "verify_tls": True,
                "credentials": {},
                "extra": {"ca_pem": tls_server["leaf"]},
            },
        )
        assert saved.status_code == 200, saved.text
        assert "BEGIN CERTIFICATE" in (saved.json()["extra"].get("ca_pem") or "")
        ok2 = client.post(f"/api/hosts/{host_id}/test", headers=headers, json={})
        assert ok2.status_code == 200, ok2.text


def test_hostname_mismatch_is_clear_when_address_is_a_dns_name(tls_server):
    # Connecting by name keeps hostname checking on; a cert issued to a
    # different name should fail with a readable error, not a raw OpenSSL line.
    ctx = verify_setting(verify_tls=True, ca_pem=tls_server["ca"], address="other.lab.local")
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.check_hostname is True
    import socket

    sock = socket.create_connection(("127.0.0.1", tls_server["port"]), timeout=3)
    try:
        with pytest.raises(ssl.SSLError) as ei:
            ctx.wrap_socket(sock, server_hostname="other.lab.local")
    finally:
        sock.close()
    msg = explain_tls_error(ei.value, address="other.lab.local", verify=ctx, has_custom_ca=True)
    assert "other.lab.local" in msg
    low = msg.lower()
    assert "name" in low or "issued" in low or "hostname" in low or "does not match" in low

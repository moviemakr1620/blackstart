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
from app.adapters.omv import OmvAdapter
from app.adapters.proxmox import ProxmoxAdapter
from app.tlsutil import (
    cert_info_from_pem,
    describe_mismatch,
    explain_tls_error,
    extract_certificates,
    ssl_context_from_pem,
    verify_setting,
)
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


def _stop_httpd(httpd: HTTPServer) -> None:
    closer = threading.Thread(target=httpd.shutdown, daemon=True)
    closer.start()
    closer.join(timeout=2)
    try:
        httpd.server_close()
    except OSError:
        pass


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
    _stop_httpd(httpd)


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
    import socket

    sock = socket.create_connection(("127.0.0.1", tls_server["port"]), timeout=3)
    wrapped = None
    try:
        with pytest.raises(ssl.SSLError) as ei:
            wrapped = ctx.wrap_socket(sock, server_hostname="other.lab.local")
    finally:
        if wrapped is not None:
            try:
                wrapped.close()
            except OSError:
                pass
        try:
            sock.close()
        except OSError:
            pass
    msg = explain_tls_error(ei.value, address="other.lab.local", verify=ctx, has_custom_ca=True)
    assert "other.lab.local" in msg
    low = msg.lower()
    assert "name" in low or "issued" in low or "hostname" in low or "does not match" in low


def make_rotated_pki():
    """New CA (regenerated) plus a leaf that is still signed only by the previous CA."""
    now = datetime.now(timezone.utc)
    old_key = _key()
    old_ca = (
        x509.CertificateBuilder()
        .subject_name(_name("Proxmox Virtual Environment"))
        .issuer_name(_name("Proxmox Virtual Environment"))
        .public_key(old_key.public_key())
        .serial_number(11)
        .not_valid_before(now - timedelta(days=200))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(old_key, hashes.SHA256())
    )
    new_key = _key()
    new_subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "Proxmox Virtual Environment"),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "efdef4d4-a050-4021-875b-1908c54726b4"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "PVE Cluster Manager CA"),
    ])
    new_ca = (
        x509.CertificateBuilder()
        .subject_name(new_subject)
        .issuer_name(new_subject)
        .public_key(new_key.public_key())
        .serial_number(99)
        .not_valid_before(now - timedelta(days=5))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(new_key, hashes.SHA256())
    )
    leaf_key = _key()
    import ipaddress

    leaf = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([
            x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "PVE Cluster Node"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Proxmox Virtual Environment"),
            x509.NameAttribute(NameOID.COMMON_NAME, "pve.localhost"),
        ]))
        .issuer_name(old_ca.subject)
        .public_key(leaf_key.public_key())
        .serial_number(2)
        .not_valid_before(now - timedelta(days=160))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([
                x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                x509.DNSName("localhost"),
                x509.DNSName("pve"),
                x509.DNSName("pve.localhost"),
            ]),
            critical=False,
        )
        .sign(old_key, hashes.SHA256())
    )
    key_pem = leaf_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    return _pem(old_ca), _pem(new_ca), _pem(leaf), key_pem


@pytest.fixture
def rotated_tls_server(tmp_path: Path):
    old_ca, new_ca, leaf_pem, key_pem = make_rotated_pki()
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
    yield {
        "port": port,
        "url": f"https://127.0.0.1:{port}",
        "old_ca": old_ca,
        "new_ca": new_ca,
        "leaf": leaf_pem,
    }
    _stop_httpd(httpd)


class _OmvHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        import json as _json

        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = _json.loads(raw.decode() or "{}")
        except _json.JSONDecodeError:
            payload = {}
        if payload.get("method") == "login":
            body = _json.dumps({"response": {"sessionid": "test-sid"}, "error": None}).encode()
        else:
            body = _json.dumps({"response": {"hostname": "omv-lab"}, "error": None}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a) -> None:  # noqa: ARG002
        return


@pytest.fixture
def omv_http_server():
    httpd = HTTPServer(("127.0.0.1", 0), _OmvHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.05)
    yield {"port": port, "url": f"http://127.0.0.1:{port}"}
    _stop_httpd(httpd)


async def test_proxmox_adapter_pins_served_leaf_after_ca_rotation(rotated_tls_server):
    """The exact Proxmox client path: a regenerated root must not verify the old leaf,
    but pasting the served leaf (pinning) must work."""
    port = rotated_tls_server["port"]
    new_ctx = verify_setting(verify_tls=True, ca_pem=rotated_tls_server["new_ca"], address="127.0.0.1")
    adapter = ProxmoxAdapter(
        address="127.0.0.1",
        port=port,
        token_id="root@pam!bs",
        token_secret="x",
        verify_tls=new_ctx,
    )
    with pytest.raises(Exception) as ei:
        await adapter.version()
    msg = str(ei.value)
    assert "Served certificate:" in msg
    assert "SHA256=" in msg
    served = cert_info_from_pem(rotated_tls_server["leaf"])
    pasted = cert_info_from_pem(rotated_tls_server["new_ca"])
    assert served.sha256 in msg
    assert pasted.sha256 in msg
    assert served.subject in msg
    # Message must not be wrapped twice.
    assert msg.lower().count("the pasted certificate did not authenticate") == 1
    assert "OpenSSL said: The pasted certificate" not in msg

    pin_ctx = verify_setting(verify_tls=True, ca_pem=rotated_tls_server["leaf"], address="127.0.0.1")
    pinned = ProxmoxAdapter(
        address="127.0.0.1",
        port=port,
        token_id="root@pam!bs",
        token_secret="x",
        verify_tls=pin_ctx,
    )
    data = await pinned.version()
    assert data.get("version") == "lab-pve"

    # The previous CA still authenticates the leaf.
    old_ctx = verify_setting(verify_tls=True, ca_pem=rotated_tls_server["old_ca"], address="127.0.0.1")
    data2 = await ProxmoxAdapter(
        address="127.0.0.1",
        port=port,
        token_id="root@pam!bs",
        token_secret="x",
        verify_tls=old_ctx,
    ).version()
    assert data2.get("version") == "lab-pve"


def test_host_test_does_not_double_wrap_and_shows_fingerprints(rotated_tls_server):
    with TestClient(app) as client:
        headers = _login(client)
        created = client.post(
            "/api/hosts",
            headers=headers,
            json={
                "name": "pve-rotated",
                "kind": "proxmox",
                "address": "127.0.0.1",
                "port": rotated_tls_server["port"],
                "verify_tls": True,
                "credentials": {"token_id": "root@pam!bs", "token_secret": "x"},
                "extra": {"ca_pem": rotated_tls_server["new_ca"]},
            },
        )
        assert created.status_code == 200, created.text
        host_id = created.json()["id"]
        failed = client.post(f"/api/hosts/{host_id}/test", headers=headers)
        assert failed.status_code == 400
        detail = failed.json()["detail"]
        assert "Served certificate:" in detail
        assert cert_info_from_pem(rotated_tls_server["leaf"]).sha256 in detail
        assert cert_info_from_pem(rotated_tls_server["new_ca"]).sha256 in detail
        assert detail.lower().count("the pasted certificate did not authenticate") == 1
        assert "OpenSSL said: The pasted certificate" not in detail
        # Pin the served leaf via unsaved Test body.
        ok = client.post(
            f"/api/hosts/{host_id}/test",
            headers=headers,
            json={"verify_tls": True, "ca_pem": rotated_tls_server["leaf"]},
        )
        assert ok.status_code == 200, ok.text


def test_explain_tls_error_is_idempotent():
    once = (
        "The pasted certificate did not authenticate 192.168.0.177.\n"
        "Served certificate: subject=CN=pve.localhost; issuer=CN=old; SHA256=AA\n"
        "OpenSSL said: unable to get local issuer certificate (_ssl.c:992)"
    )
    again = explain_tls_error(Exception(once), address="192.168.0.177", verify=object(), has_custom_ca=True)
    assert again == once
    assert again.count("The pasted certificate did not authenticate") == 1


async def test_omv_plain_http_ignores_verify(omv_http_server):
    ctx = verify_setting(verify_tls=True, ca_pem="not-a-cert", address="http://127.0.0.1")
    assert ctx is False
    adapter = OmvAdapter(
        address="http://127.0.0.1",
        port=omv_http_server["port"],
        username="admin",
        password="pass",
        verify_tls=True,
        use_https=True,  # should be overridden by http:// address
    )
    assert adapter.base.startswith("http://")
    assert adapter.verify is False
    # Port in a bare http://host URL has no port; pass the full base.
    adapter = OmvAdapter(
        address=omv_http_server["url"],
        port=443,
        username="admin",
        password="pass",
        verify_tls=True,
        use_https=True,
    )
    result = await adapter.ping()
    assert result.get("ok") is True


async def test_omv_https_leaf_pin_works(tls_server):
    pin = verify_setting(verify_tls=True, ca_pem=tls_server["leaf"], address="127.0.0.1")
    # TLS server speaks GET only; we only need the handshake + a POST that may 400/404
    # after TLS succeeds. Use a tiny HTTPS wrapper around the existing leaf server:
    # OmvAdapter posts to /rpc.php which the test PVE handler will still answer 200 JSON.
    adapter = OmvAdapter(
        address="127.0.0.1",
        port=tls_server["port"],
        username="admin",
        password="pass",
        verify_tls=pin,
        use_https=True,
    )
    # The lab HTTPS handler returns PVE JSON, not OMV login, so login may fail after TLS.
    # A TLS failure would be ConnectError / TLSVerificationError before JSON parse.
    try:
        await adapter.login()
    except Exception as exc:
        from app.tlsutil import is_tls_error

        assert not is_tls_error(exc), exc

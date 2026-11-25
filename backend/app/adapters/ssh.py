from __future__ import annotations

import asyncio
import base64
import hashlib
from typing import Any


class HostKeyMismatch(RuntimeError):
    pass


def fingerprint(key: Any) -> str:
    """OpenSSH-style SHA256 fingerprint of a paramiko key."""
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


class SshAdapter:
    """Runs one command per connection.

    Host keys: if `host_key` (an OpenSSH "SHA256:..." fingerprint) is set, the
    server must present that key or the connection is refused. If it is empty
    the key is accepted and its fingerprint returned as `host_key` in the
    result, so the caller can pin it (Inventory → Test does this).
    """

    def __init__(
        self,
        address: str,
        port: int = 22,
        username: str = "",
        password: str = "",
        key_data: str = "",
        timeout: float = 20.0,
        key_passphrase: str = "",
        host_key: str = "",
    ) -> None:
        self.address = address
        self.port = port or 22
        self.username = username
        self.password = password
        self.key_data = key_data
        self.key_passphrase = key_passphrase
        self.host_key = (host_key or "").strip()
        self.timeout = timeout

    def _load_key(self) -> Any:
        import io

        import paramiko

        if not self.key_data.strip():
            return None
        errors = []
        for loader in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
            try:
                return loader.from_private_key(io.StringIO(self.key_data), password=self.key_passphrase or None)
            except paramiko.PasswordRequiredException as exc:
                raise RuntimeError("The SSH private key is encrypted; add its passphrase to the host.") from exc
            except Exception as exc:  # noqa: BLE001
                errors.append(type(exc).__name__)
        raise RuntimeError("Could not read the SSH private key (supported: Ed25519, ECDSA, RSA).")

    def _connect(self) -> tuple[Any, str]:
        import paramiko

        client = paramiko.SSHClient()
        # We check the key ourselves right after the handshake.
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=self.address,
            port=self.port,
            username=self.username or None,
            password=self.password or None,
            pkey=self._load_key(),
            timeout=self.timeout,
            banner_timeout=self.timeout,
            auth_timeout=self.timeout,
            allow_agent=False,
            look_for_keys=False,
        )
        transport = client.get_transport()
        seen = fingerprint(transport.get_remote_server_key()) if transport else ""
        if self.host_key and seen != self.host_key:
            client.close()
            raise HostKeyMismatch(
                f"SSH host key for {self.address} changed (expected {self.host_key}, got {seen}). "
                "If you rebuilt that machine, clear the pinned key on the host in Inventory and test again."
            )
        return client, seen

    async def run(self, command: str, timeout: float | None = None) -> dict[str, Any]:
        def _inner() -> dict[str, Any]:
            client, seen = self._connect()
            try:
                _stdin, stdout, stderr = client.exec_command(command, timeout=timeout or self.timeout)
                out = stdout.read().decode(errors="replace")
                err = stderr.read().decode(errors="replace")
                code = stdout.channel.recv_exit_status()
                return {"exit_code": code, "stdout": out[-8000:], "stderr": err[-4000:], "host_key": seen}
            finally:
                client.close()

        return await asyncio.to_thread(_inner)

    async def ping(self) -> dict[str, Any]:
        return await self.run("uname -a && uptime")

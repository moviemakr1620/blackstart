from __future__ import annotations

import asyncio
from typing import Any


class SshAdapter:
    def __init__(
        self,
        address: str,
        port: int = 22,
        username: str = "",
        password: str = "",
        key_data: str = "",
        timeout: float = 20.0,
    ) -> None:
        self.address = address
        self.port = port or 22
        self.username = username
        self.password = password
        self.key_data = key_data
        self.timeout = timeout

    def _connect(self) -> Any:
        import io

        import paramiko

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        pkey = None
        if self.key_data.strip():
            raw = self.key_data
            for loader in (
                paramiko.RSAKey,
                paramiko.Ed25519Key,
                paramiko.ECDSAKey,
            ):
                try:
                    pkey = loader.from_private_key(io.StringIO(raw))
                    break
                except Exception:
                    continue
        client.connect(
            hostname=self.address,
            port=self.port,
            username=self.username or None,
            password=self.password or None,
            pkey=pkey,
            timeout=self.timeout,
            allow_agent=False,
            look_for_keys=False,
        )
        return client

    async def run(self, command: str, timeout: float | None = None) -> dict[str, Any]:
        def _inner() -> dict[str, Any]:
            client = self._connect()
            try:
                _stdin, stdout, stderr = client.exec_command(command, timeout=timeout or self.timeout)
                out = stdout.read().decode(errors="replace")
                err = stderr.read().decode(errors="replace")
                code = stdout.channel.recv_exit_status()
                return {"exit_code": code, "stdout": out[-8000:], "stderr": err[-4000:]}
            finally:
                client.close()

        return await asyncio.to_thread(_inner)

    async def ping(self) -> dict[str, Any]:
        return await self.run("uname -a && uptime")

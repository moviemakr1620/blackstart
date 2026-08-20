from __future__ import annotations

import json
from typing import Any

from app.adapters.ssh import SshAdapter


class K3sAdapter:
    """k3s via SSH to the control plane. Discovers nodes, drain/uncordon, shutdown."""

    def __init__(self, ssh: SshAdapter, kubeconfig: str = "/etc/rancher/k3s/k3s.yaml") -> None:
        self.ssh = ssh
        self.kubeconfig = kubeconfig

    def _kubectl(self, rest: str) -> str:
        cfg = self.kubeconfig.replace("'", "'\"'\"'")
        return f"sudo KUBECONFIG='{cfg}' kubectl {rest}"

    async def ping(self) -> dict[str, Any]:
        res = await self.ssh.run(self._kubectl("get nodes --no-headers | wc -l"))
        return res

    async def nodes(self) -> list[dict[str, Any]]:
        res = await self.ssh.run(self._kubectl("get nodes -o json"))
        if res.get("exit_code") not in (0, None):
            raise RuntimeError(res.get("stderr") or res.get("stdout") or "kubectl get nodes failed")
        raw = json.loads(res.get("stdout") or "{}")
        out: list[dict[str, Any]] = []
        for item in raw.get("items") or []:
            meta = item.get("metadata") or {}
            status = item.get("status") or {}
            name = str(meta.get("name") or "")
            address = ""
            for addr in status.get("addresses") or []:
                if addr.get("type") == "InternalIP":
                    address = str(addr.get("address") or "")
                    break
            ready = False
            for cond in status.get("conditions") or []:
                if cond.get("type") == "Ready" and str(cond.get("status")).lower() == "true":
                    ready = True
            out.append({"name": name, "address": address, "ready": ready})
        return out

    async def drain(self, node: str) -> dict[str, Any]:
        return await self.ssh.run(
            self._kubectl(
                f"drain {node} --ignore-daemonsets --delete-emptydir-data --force --grace-period=30 --timeout=120s"
            ),
            timeout=150,
        )

    async def uncordon(self, node: str) -> dict[str, Any]:
        return await self.ssh.run(self._kubectl(f"uncordon {node}"))

    async def shutdown_address(self, address: str) -> dict[str, Any]:
        node_ssh = SshAdapter(
            address=address,
            port=self.ssh.port,
            username=self.ssh.username,
            password=self.ssh.password,
            key_data=self.ssh.key_data,
            timeout=self.ssh.timeout,
        )
        return await node_ssh.run("sudo shutdown -h now")

    async def discover(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for n in await self.nodes():
            items.append(
                {
                    "kind": "k3s_node",
                    "name": n["name"],
                    "external_id": n["name"],
                    "extra": {"address": n.get("address"), "ready": n.get("ready")},
                    "last_status": "Ready" if n.get("ready") else "NotReady",
                }
            )
        return items

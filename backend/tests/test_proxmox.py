import asyncio

from app.adapters.pbs import PbsAdapter
from app.adapters.proxmox import ProxmoxAdapter, split_api_token


def test_split_api_token_plain():
    tid, sec = split_api_token("root@pam!blackstart", "abc-uuid")
    assert tid == "root@pam!blackstart"
    assert sec == "abc-uuid"


def test_split_api_token_pve_blob():
    tid, sec = split_api_token("PVEAPIToken=root@pam!blackstart=abc-uuid", "")
    assert tid == "root@pam!blackstart"
    assert sec == "abc-uuid"


def test_split_api_token_pbs_blob():
    tid, sec = split_api_token("root@pbs!api:65554979-e675-4d56-xxxx", "")
    assert tid == "root@pbs!api"
    assert sec == "65554979-e675-4d56-xxxx"


def test_pbs_token_uses_colon():
    adapter = PbsAdapter("192.0.2.20", token_id="root@pam!blackstart", token_secret="secret-uuid")
    assert adapter._headers()["Authorization"] == "PBSAPIToken=root@pam!blackstart:secret-uuid"


def test_pve_token_uses_equals():
    adapter = ProxmoxAdapter("192.0.2.10", token_id="root@pam!blackstart", token_secret="secret-uuid")
    assert adapter._headers()["Authorization"] == "PVEAPIToken=root@pam!blackstart=secret-uuid"


class FakePVE(ProxmoxAdapter):
    def __init__(self) -> None:
        super().__init__("127.0.0.1", token_id="u@pam!t", token_secret="s")

    async def resources(self, typ: str | None = None):
        return [{"type": "qemu", "vmid": 100, "name": "from-cluster", "node": "pve1", "status": "running"}]

    async def nodes(self):
        return [{"node": "pve1"}, {"id": "node/pve2"}]

    async def _request(self, method: str, path: str, data=None):
        if path.endswith("/pve1/qemu"):
            return [
                {"vmid": 100, "name": "from-cluster", "status": "running"},
                {"vmid": 101, "name": "stopped-vm", "status": "stopped"},
            ]
        if path.endswith("/pve1/lxc"):
            return []
        if path.endswith("/pve2/qemu"):
            return [{"vmid": 200, "name": "other-node", "status": "running", "template": 1}]
        if path.endswith("/pve2/lxc"):
            return [{"vmid": 201, "name": "ct-media", "status": "stopped"}]
        return []


def test_discover_walks_every_node():
    guests = asyncio.run(FakePVE().discover())
    keys = {(g["kind"], g["external_id"]) for g in guests}
    assert ("vm", "100") in keys
    assert ("vm", "101") in keys
    assert ("vm", "200") in keys
    assert ("lxc", "201") in keys
    assert len(guests) == 4
    ct = next(g for g in guests if g["external_id"] == "201")
    assert ct["extra"]["node"] == "pve2"


def test_vmid_from_id_field():
    adapter = ProxmoxAdapter("127.0.0.1", token_id="u@pam!t", token_secret="s")
    assert adapter._vmid({"id": "lxc/203", "type": "lxc"}) == "203"
    assert adapter._vmid({"vmid": 100}) == "100"


class PartialPVE(ProxmoxAdapter):
    def __init__(self) -> None:
        super().__init__("127.0.0.1", token_id="u@pam!t", token_secret="s")

    async def resources(self, typ: str | None = None):
        return [
            {"type": "lxc", "id": "lxc/101", "name": "ct1", "node": "pve", "status": "running"},
            {"type": "lxc", "id": "lxc/102", "name": "ct2", "node": "pve", "status": "stopped"},
        ]

    async def nodes(self):
        return [{"node": "pve"}]

    async def _request(self, method: str, path: str, data=None, params=None):
        if path.endswith("/pve/qemu"):
            raise RuntimeError("PVE 403 /api2/json/nodes/pve/qemu: Permission check failed")
        if path.endswith("/pve/lxc"):
            return [
                {"vmid": 101, "name": "ct1", "status": "running"},
                {"vmid": 102, "name": "ct2", "status": "stopped"},
                {"vmid": 103, "name": "ct3", "status": "running"},
            ]
        return []


def test_discover_keeps_lxc_when_qemu_forbidden_and_parses_id():
    adapter = PartialPVE()
    guests = asyncio.run(adapter.discover())
    lxcs = [g for g in guests if g["kind"] == "lxc"]
    assert {g["external_id"] for g in lxcs} == {"101", "102", "103"}
    assert adapter.warnings
    assert any("qemu" in w.lower() or "403" in w for w in adapter.warnings)

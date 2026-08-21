from app.engine.executor import collect_macs, inverse_action, is_synology_ha


class Fake:
    def __init__(self, extra_json: str = "{}"):
        self.extra_json = extra_json


def test_collect_macs_ha_pair():
    host = Fake('{"ha": true, "mac": "AA:BB:CC:00:00:01", "mac_passive": "AA:BB:CC:00:00:02"}')
    macs = collect_macs({}, None, host)  # type: ignore[arg-type]
    assert len(macs) == 2
    assert "AA:BB:CC:00:00:01" in macs
    assert "AA:BB:CC:00:00:02" in macs


def test_is_synology_ha():
    host = Fake('{"ha": true}')
    assert is_synology_ha(host, None)  # type: ignore[arg-type]
    assert not is_synology_ha(Fake("{}"), None)  # type: ignore[arg-type]


def test_inverse_action_host_level():
    assert inverse_action("synology_shutdown") == "wol"
    assert inverse_action("k3s_shutdown_node") == "k3s_wake_node"
    assert inverse_action("k3s_shutdown_cluster") == "k3s_restore_cluster"
    assert inverse_action("k3s_drain") == "k3s_uncordon"
    assert inverse_action("docker_stop_stack") == "docker_start_stack"
    assert inverse_action("pbs_shutdown") == "wol"

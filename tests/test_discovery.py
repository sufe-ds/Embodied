"""机器人发现：用桩定位器验证范围整理与命令行输出，不触碰真实网络。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from embodied import cli, discover
from embodied.domain.entities import RobotAddress
from embodied.domain.errors import RobotUnreachableError, ValidationError
from embodied.service.discovery import DiscoveryService
from embodied.service.network_scan import SubnetRobotLocator, TonyPiLocator
from embodied.service.ports import RobotLocator


class StubLocator(RobotLocator):
    def __init__(
        self, robots: list[RobotAddress], subnets: list[str] | None = None
    ) -> None:
        self.robots = robots
        self.subnets = subnets or ["10.0.0.0/24"]
        self.calls: list[tuple[list[str], float]] = []

    def local_subnets(self) -> list[str]:
        return list(self.subnets)

    def scan(
        self, targets: list[str] | None = None, timeout_s: float = 0.3
    ) -> list[RobotAddress]:
        self.calls.append((list(targets or []), timeout_s))
        return list(self.robots)


def test_default_targets_adds_configured_subnet() -> None:
    service = DiscoveryService(StubLocator([]), "192.168.2.34", 9559)
    assert service.default_targets() == ["10.0.0.0/24", "192.168.2.0/24"]


def test_default_targets_skips_loopback_config() -> None:
    service = DiscoveryService(StubLocator([]), "127.0.0.1", 9559)
    assert service.default_targets() == ["10.0.0.0/24"]


def test_scan_marks_configured_robot() -> None:
    locator = StubLocator(
        [RobotAddress("192.168.2.34", 9559, "nao.local"), RobotAddress("192.168.2.35")]
    )
    service = DiscoveryService(locator, "192.168.2.34", 9559)

    result = service.scan(["192.168.2.0/24"], timeout_s=0.2)

    assert result["targets"] == ["192.168.2.0/24"]
    assert result["configured_ip"] == "192.168.2.34"
    assert [robot["configured"] for robot in result["robots"]] == [True, False]
    assert result["robots"][0]["name"] == "nao.local"
    assert locator.calls == [(["192.168.2.0/24"], 0.2)]


def test_scan_uses_default_targets() -> None:
    locator = StubLocator([])
    DiscoveryService(locator, "10.0.0.7").scan()
    assert locator.calls[0][0] == ["10.0.0.0/24"]


def test_rejects_too_large_range() -> None:
    locator = SubnetRobotLocator(9559, max_hosts=16)
    with pytest.raises(ValidationError):
        locator.scan(["10.0.0.0/16"])


def test_expand_dedupes_targets() -> None:
    locator = SubnetRobotLocator(9559)
    assert locator._expand(["192.168.1.2", "192.168.1.0/30", " 192.168.1.2 "]) == [
        "192.168.1.2",
        "192.168.1.1",
    ]


def _connection(banner: bytes = b"") -> MagicMock:
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.recv.return_value = banner
    return connection


def test_scan_requires_ssh_port(monkeypatch) -> None:
    locator = SubnetRobotLocator(9559)
    attempted = []

    def connect(address, timeout):
        attempted.append(address[1])
        if address[1] == 22:
            raise OSError
        return _connection()

    monkeypatch.setattr(
        "embodied.service.network_scan.socket.create_connection", connect
    )

    assert locator.scan(["192.168.1.2"]) == []
    assert attempted == [9559, 22]


def test_scan_accepts_valid_ssh_banner(monkeypatch) -> None:
    locator = SubnetRobotLocator(9559)
    ssh = _connection(b"SSH-2.0-OpenSSH_9.6\r\n")
    connections = {9559: _connection(), 22: ssh}
    monkeypatch.setattr(
        "embodied.service.network_scan.socket.create_connection",
        lambda address, timeout: connections[address[1]],
    )

    assert locator.scan(["192.168.1.2"]) == [RobotAddress("192.168.1.2", 9559)]
    ssh.settimeout.assert_called_once_with(0.3)


@pytest.mark.parametrize("banner", [b"HTTP/1.1 200 OK\r\n", b""])
def test_scan_rejects_invalid_ssh_banner(monkeypatch, banner: bytes) -> None:
    locator = SubnetRobotLocator(9559)
    connections = {9559: _connection(), 22: _connection(banner)}
    monkeypatch.setattr(
        "embodied.service.network_scan.socket.create_connection",
        lambda address, timeout: connections[address[1]],
    )

    assert locator.scan(["192.168.1.2"]) == []


def test_scan_ignores_ssh_read_timeout(monkeypatch) -> None:
    locator = SubnetRobotLocator(9559)
    ssh = _connection()
    ssh.recv.side_effect = TimeoutError
    connections = {9559: _connection(), 22: ssh}
    monkeypatch.setattr(
        "embodied.service.network_scan.socket.create_connection",
        lambda address, timeout: connections[address[1]],
    )

    assert locator.scan(["192.168.1.2"]) == []


def test_scan_skips_reverse_dns(monkeypatch) -> None:
    locator = SubnetRobotLocator(9559)
    monkeypatch.setattr(locator, "_open", lambda host, timeout: True)

    def fail_reverse_dns(host: str) -> None:
        raise AssertionError(f"扫描不应解析主机名：{host}")

    monkeypatch.setattr(
        "embodied.service.network_scan.socket.gethostbyaddr", fail_reverse_dns
    )

    assert locator.scan(["192.168.1.2"]) == [RobotAddress("192.168.1.2", 9559)]


def test_cli_reports_configured_robot(capsys) -> None:
    locator = StubLocator(
        [RobotAddress("192.168.2.34", 9559, "nao.local"), RobotAddress("192.168.2.35")]
    )
    service = DiscoveryService(locator, "192.168.2.34", 9559)

    code = discover.main(["192.168.2.0/24", "--timeout", "0.2"], service=service)

    out = capsys.readouterr().out
    assert code == 0
    assert "- 192.168.2.34" in out
    assert "- 192.168.2.35" in out
    assert locator.calls == [(["192.168.2.0/24"], 0.2)]


def test_cli_json_output(capsys) -> None:
    service = DiscoveryService(StubLocator([RobotAddress("192.168.2.34")]), "10.0.0.7")

    code = discover.main(["--json"], service=service)

    assert code == 0
    body = json.loads(capsys.readouterr().out)
    assert body["targets"] == ["10.0.0.0/24"]
    assert body["robots"][0]["ip"] == "192.168.2.34"


def test_cli_exits_nonzero_without_robots(capsys) -> None:
    service = DiscoveryService(StubLocator([]), "10.0.0.7")

    code = discover.main([], service=service)

    assert code == 1
    out = capsys.readouterr().out
    assert "NAO（0 个候选）" in out
    assert "TonyPi（0 个候选）" in out


def test_entrypoint_dispatches_discover(capsys) -> None:
    service = DiscoveryService(
        StubLocator([RobotAddress("192.168.2.34", 9559, "nao.local")]), "10.0.0.7"
    )

    code = cli.main(["discover", "192.168.2.0/24"], service=service)

    assert code == 0
    out = capsys.readouterr().out
    assert "扫描 192.168.2.0/24" in out
    assert "- 192.168.2.34" in out


def test_entrypoint_requires_command() -> None:
    with pytest.raises(SystemExit):
        cli.main([])


def _tonypi(ip: str = "192.168.1.104", video_open: bool = True) -> RobotAddress:
    return RobotAddress(
        ip,
        9030,
        robot_type="tonypi",
        video_port=8080,
        video_open=video_open,
    )


def test_default_targets_adds_both_configured_subnets() -> None:
    service = DiscoveryService(
        StubLocator([]),
        "192.168.2.34",
        pi_locator=StubLocator([]),
        pi_ip="192.168.1.104",
    )

    assert service.default_targets() == [
        "10.0.0.0/24",
        "192.168.2.0/24",
        "192.168.1.0/24",
    ]


def test_tonypi_scan_verifies_rpc_and_video(monkeypatch) -> None:
    locator = TonyPiLocator()
    monkeypatch.setattr(locator, "_port_open", lambda host, port, timeout: True)

    def echo(self, method, params=None):
        assert method == "echo"
        return params[0]

    monkeypatch.setattr("embodied.service.network_scan.TonyPiRpcClient.call", echo)

    result = locator.scan(["192.168.1.104"])

    assert result == [_tonypi()]
    assert result[0].robot_type == "tonypi"
    assert result[0].video_open is True


def test_tonypi_scan_rejects_rpc_failure(monkeypatch) -> None:
    locator = TonyPiLocator()
    monkeypatch.setattr(locator, "_port_open", lambda host, port, timeout: True)
    monkeypatch.setattr(
        "embodied.service.network_scan.TonyPiRpcClient.call",
        lambda *args: (_ for _ in ()).throw(RobotUnreachableError("offline")),
    )

    assert locator.scan(["192.168.1.104"]) == []


def test_tonypi_scan_reports_closed_video(monkeypatch) -> None:
    locator = TonyPiLocator()
    monkeypatch.setattr(locator, "_port_open", lambda host, port, timeout: port == 9030)
    monkeypatch.setattr(
        "embodied.service.network_scan.TonyPiRpcClient.call",
        lambda self, method, params=None: params[0],
    )

    result = locator.scan(["192.168.1.104"])

    assert result == [_tonypi(video_open=False)]
    assert result[0].video_open is False


def test_scan_groups_and_marks_both_configured_robots() -> None:
    nao_locator = StubLocator([RobotAddress("192.168.2.34")])
    pi_locator = StubLocator([_tonypi()])
    service = DiscoveryService(
        nao_locator,
        "192.168.2.34",
        pi_locator=pi_locator,
        pi_ip="192.168.1.104",
    )

    result = service.scan(["192.168.1.0/24"])

    assert result["counts"] == {"nao": 1, "tonypi": 1, "total": 2}
    assert result["groups"]["nao"][0]["configured"] is True
    assert result["groups"]["tonypi"][0]["configured"] is True
    assert {item["robot_type"] for item in result["robots"]} == {"nao", "tonypi"}
    assert len(nao_locator.calls) == 1
    assert len(pi_locator.calls) == 1


def test_scan_pi_filter_skips_nao() -> None:
    nao_locator = StubLocator([RobotAddress("192.168.2.34")])
    pi_locator = StubLocator([_tonypi()])
    service = DiscoveryService(nao_locator, "192.168.2.34", pi_locator=pi_locator)

    result = service.scan(["192.168.1.0/24"], robot="pi")

    assert result["counts"] == {"nao": 0, "tonypi": 1, "total": 1}
    assert nao_locator.calls == []
    assert len(pi_locator.calls) == 1


def test_cli_reports_separate_groups(capsys) -> None:
    service = DiscoveryService(
        StubLocator([RobotAddress("192.168.2.34")]),
        "192.168.2.34",
        pi_locator=StubLocator([_tonypi()]),
        pi_ip="192.168.1.104",
    )

    code = discover.main(["192.168.1.0/24"], service=service)

    out = capsys.readouterr().out
    assert code == 0
    assert "NAO（1 个候选）" in out
    assert "TonyPi（1 个候选）" in out
    assert "RPC 9030 已验证" in out
    assert "视频 8080 已开放" in out


def test_cli_json_contains_groups(capsys) -> None:
    service = DiscoveryService(
        StubLocator([]),
        "192.168.2.34",
        pi_locator=StubLocator([_tonypi()]),
        pi_ip="192.168.1.104",
    )

    code = discover.main(["--robot", "pi", "--json"], service=service)

    assert code == 0
    body = json.loads(capsys.readouterr().out)
    assert body["groups"]["nao"] == []
    assert body["groups"]["tonypi"][0]["ip"] == "192.168.1.104"
    assert body["counts"] == {"nao": 0, "tonypi": 1, "total": 1}
    assert body["robots"][0]["robot_type"] == "tonypi"


def test_cli_filter_reports_only_selected_group(capsys) -> None:
    service = DiscoveryService(
        StubLocator([]), "192.168.2.34", pi_locator=StubLocator([_tonypi()])
    )

    code = discover.main(["--robot", "pi"], service=service)

    out = capsys.readouterr().out
    assert code == 0
    assert "TonyPi（1 个候选）" in out
    assert "NAO（" not in out

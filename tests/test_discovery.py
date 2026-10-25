"""机器人发现：用桩定位器验证范围整理与命令行输出，不触碰真实网络。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from embodied import cli, discover
from embodied.domain.entities import RobotAddress
from embodied.domain.errors import ValidationError
from embodied.service.discovery import DiscoveryService
from embodied.service.network_scan import SubnetRobotLocator
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
    assert "未找到同时开放 9559 与 22" in capsys.readouterr().out


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

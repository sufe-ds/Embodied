"""局域网扫描：分别通过 NAOqi/SSH 与 TonyPi RPC 定位机器人候选。"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..domain.entities import RobotAddress
from ..domain.errors import RobotError, ValidationError
from .pi_client import TonyPiRpcClient
from .ports import RobotLocator

_MAX_HOSTS = 4096
_MAX_WORKERS = 256


def local_ipv4_addresses() -> list[str]:
    """本机可路由的 IPv4 地址（跳过回环与链路本地）。"""
    addresses: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addresses.add(info[4][0])
    except OSError:
        pass

    # 容器 / WSL 里主机名常只解析到回环，用 UDP connect 拿默认出口地址
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        addresses.add(probe.getsockname()[0])
    except OSError:
        pass
    finally:
        probe.close()

    usable = []
    for text in addresses:
        address = ipaddress.ip_address(text)
        if address.is_loopback or address.is_link_local or address.is_unspecified:
            continue
        usable.append(text)
    return sorted(usable, key=ipaddress.ip_address)


class SubnetRobotLocator(RobotLocator):
    def __init__(self, port: int = 9559, max_hosts: int = _MAX_HOSTS) -> None:
        self.port = port
        self.max_hosts = max_hosts

    def local_subnets(self) -> list[str]:
        return [
            str(ipaddress.ip_network(f"{ip}/24", strict=False))
            for ip in local_ipv4_addresses()
        ]

    def scan(
        self, targets: list[str] | None = None, timeout_s: float = 0.3
    ) -> list[RobotAddress]:
        hosts = self._expand(targets if targets else self.local_subnets())
        if not hosts:
            return []
        timeout = min(max(timeout_s, 0.05), 5.0)

        found: list[str] = []
        with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(hosts))) as pool:
            futures = {pool.submit(self._open, host, timeout): host for host in hosts}
            for future in as_completed(futures):
                if future.result():
                    found.append(futures[future])

        found.sort(key=ipaddress.ip_address)
        return [RobotAddress(host, self.port) for host in found]

    def _expand(self, targets: Iterable[str]) -> list[str]:
        hosts: list[str] = []
        seen: set[str] = set()
        for raw in targets:
            target = raw.strip()
            if not target:
                continue
            for address in self._addresses(target):
                if address not in seen:
                    seen.add(address)
                    hosts.append(address)
            if len(hosts) > self.max_hosts:
                raise ValidationError(
                    f"扫描范围超过 {self.max_hosts} 个地址，请缩小网段",
                    detail=target,
                )
        return hosts

    def _addresses(self, target: str) -> list[str]:
        if "/" in target:
            try:
                network = ipaddress.ip_network(target, strict=False)
            except ValueError as exc:
                raise ValidationError(
                    f"无法解析网段：{target}", detail=str(exc)
                ) from exc
            if network.version != 4:
                raise ValidationError("暂只支持 IPv4 网段", detail=target)
            if network.num_addresses - 2 > self.max_hosts:
                raise ValidationError(
                    f"扫描范围超过 {self.max_hosts} 个地址，请缩小网段",
                    detail=target,
                )
            return [str(ip) for ip in network.hosts()]

        try:
            return [str(ipaddress.ip_address(target))]
        except ValueError:
            pass
        try:
            return [socket.gethostbyname(target)]
        except OSError as exc:
            raise ValidationError(f"无法解析目标：{target}", detail=str(exc)) from exc

    def _open(self, host: str, timeout: float) -> bool:
        try:
            with socket.create_connection((host, self.port), timeout=timeout):
                pass
            with socket.create_connection((host, 22), timeout=timeout) as ssh:
                ssh.settimeout(timeout)
                return ssh.recv(255).startswith(b"SSH-")
        except OSError:
            return False


class TonyPiLocator(SubnetRobotLocator):
    """通过原生 RPC echo 识别 TonyPi，并单独报告视频端口状态。"""

    def __init__(
        self,
        port: int = 9030,
        video_port: int = 8080,
        max_hosts: int = _MAX_HOSTS,
    ) -> None:
        super().__init__(port, max_hosts)
        self.video_port = video_port

    def scan(
        self, targets: list[str] | None = None, timeout_s: float = 0.3
    ) -> list[RobotAddress]:
        hosts = self._expand(targets if targets else self.local_subnets())
        if not hosts:
            return []
        timeout = min(max(timeout_s, 0.05), 5.0)

        found: list[RobotAddress] = []
        with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(hosts))) as pool:
            futures = {pool.submit(self._probe, host, timeout): host for host in hosts}
            for future in as_completed(futures):
                robot = future.result()
                if robot is not None:
                    found.append(robot)
        return sorted(found, key=lambda robot: ipaddress.ip_address(robot.ip))

    @staticmethod
    def _port_open(host: str, port: int, timeout: float) -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    def _probe(self, host: str, timeout: float) -> RobotAddress | None:
        if not self._port_open(host, self.port, timeout):
            return None
        token = "embodied-tonypi-discovery"
        try:
            result = TonyPiRpcClient(host, self.port, timeout).call("echo", [token])
        except RobotError:
            return None
        if result != token:
            return None
        return RobotAddress(
            host,
            self.port,
            robot_type="tonypi",
            video_port=self.video_port,
            video_open=self._port_open(host, self.video_port, timeout),
        )

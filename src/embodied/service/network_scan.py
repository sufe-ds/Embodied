"""局域网扫描：通过 NAOqi 端口与 SSH 标识定位机器人候选。

NAOqi 没有可用的广播发现协议；WSL2 默认 NAT 也转发不了广播，
因此这里并发探测 9559 与 22，并要求 SSH 服务返回有效标识。
这些条件不代表设备一定是 NAO，结果视为候选。
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..domain.entities import RobotAddress
from ..domain.errors import ValidationError
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

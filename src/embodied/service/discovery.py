"""机器人发现服务：整理扫描范围，并按 NAO / TonyPi 分组。"""

from __future__ import annotations

import ipaddress
import time
from concurrent.futures import ThreadPoolExecutor

from ..domain.entities import RobotAddress
from ..domain.errors import ValidationError
from .ports import RobotLocator


class DiscoveryService:
    def __init__(
        self,
        locator: RobotLocator,
        configured_ip: str,
        configured_port: int = 9559,
        *,
        pi_locator: RobotLocator | None = None,
        pi_ip: str = "",
        pi_rpc_port: int = 9030,
    ) -> None:
        self._locator = locator
        self._pi_locator = pi_locator
        self._configured_ip = configured_ip.strip()
        self._configured_port = configured_port
        self._configured_pi_ip = pi_ip.strip()
        self._configured_pi_rpc_port = pi_rpc_port

    @staticmethod
    def _add_configured_subnet(targets: list[str], configured_ip: str) -> None:
        try:
            configured = ipaddress.ip_address(configured_ip)
        except ValueError:
            return
        if (
            configured.version != 4
            or configured.is_loopback
            or configured.is_unspecified
        ):
            return
        subnet = str(ipaddress.ip_network(f"{configured}/24", strict=False))
        if subnet not in targets:
            targets.append(subnet)

    def default_targets(self) -> list[str]:
        """本机各网段，以及两种当前配置 IP 所在的 /24 网段。"""
        targets = self._locator.local_subnets()
        self._add_configured_subnet(targets, self._configured_ip)
        self._add_configured_subnet(targets, self._configured_pi_ip)
        return targets

    def scan(
        self,
        targets: list[str] | None = None,
        timeout_s: float = 0.3,
        robot: str = "all",
    ) -> dict:
        if robot not in ("all", "nao", "pi"):
            raise ValidationError("发现类型只能是 all、nao 或 pi")
        hosts = [
            item.strip() for item in (targets or self.default_targets()) if item.strip()
        ]
        scanners: dict[str, RobotLocator] = {}
        if robot in ("all", "nao"):
            scanners["nao"] = self._locator
        if robot in ("all", "pi") and self._pi_locator is not None:
            scanners["tonypi"] = self._pi_locator

        started = time.perf_counter()
        if len(scanners) > 1:
            with ThreadPoolExecutor(max_workers=len(scanners)) as pool:
                futures = {
                    kind: pool.submit(locator.scan, hosts, timeout_s)
                    for kind, locator in scanners.items()
                }
                found = {kind: future.result() for kind, future in futures.items()}
        else:
            found = {
                kind: locator.scan(hosts, timeout_s)
                for kind, locator in scanners.items()
            }

        groups = {
            "nao": [self._view(item) for item in found.get("nao", [])],
            "tonypi": [self._view(item) for item in found.get("tonypi", [])],
        }
        robots = sorted(
            groups["nao"] + groups["tonypi"],
            key=lambda item: (ipaddress.ip_address(item["ip"]), item["robot_type"]),
        )
        counts = {
            "nao": len(groups["nao"]),
            "tonypi": len(groups["tonypi"]),
            "total": len(robots),
        }
        return {
            "configured_ip": self._configured_ip,
            "configured_pi_ip": self._configured_pi_ip,
            "selected_robot": robot,
            "targets": hosts,
            "elapsed_s": round(time.perf_counter() - started, 3),
            "robots": robots,
            "groups": groups,
            "counts": counts,
        }

    def _view(self, robot: RobotAddress) -> dict:
        data = robot.to_dict()
        if robot.robot_type == "tonypi":
            data["configured"] = (
                robot.ip == self._configured_pi_ip
                and robot.port == self._configured_pi_rpc_port
            )
        else:
            data["configured"] = (
                robot.ip == self._configured_ip and robot.port == self._configured_port
            )
        return data

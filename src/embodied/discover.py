"""机器人发现工具：启动服务前分别确认 NAO 与 TonyPi 地址。

    uv run embodied discover [目标...] [--robot all|nao|pi] [--timeout 0.3] [--json]

扫描不依赖服务进程，因此可以在配置机器人 IP 之前使用。
退出码：0 找到任一类型候选，1 未找到，2 参数或网络错误。
"""

from __future__ import annotations

import argparse
import json
import sys

from .domain.errors import RobotError
from .service.config import Settings
from .service.discovery import DiscoveryService
from .service.network_scan import SubnetRobotLocator, TonyPiLocator


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "targets",
        nargs="*",
        metavar="目标",
        help="IP 或 CIDR 列表，省略则扫描本机网段与已配置机器人所在网段",
    )
    parser.add_argument(
        "--robot",
        choices=["all", "nao", "pi"],
        default="all",
        help="发现类型：全部、仅 NAO 或仅 TonyPi（默认 all）",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=0.3,
        metavar="秒",
        help="单地址探测超时，默认 0.3",
    )
    parser.add_argument("--json", action="store_true", help="输出完整 JSON")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m embodied.discover",
        description=(
            "分别扫描 NAO（9559 + SSH 标识）与 TonyPi（9030 RPC 验证 + 8080 视频状态）。"
        ),
    )
    add_arguments(parser)
    return parser


def configure_stdio() -> None:
    """Windows 重定向时默认走 ANSI 代码页，统一成 UTF-8 便于 Agent 读取。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def run(args: argparse.Namespace, service: DiscoveryService | None = None) -> int:
    if service is None:
        settings = Settings()
        service = DiscoveryService(
            SubnetRobotLocator(settings.nao_port),
            settings.nao_ip,
            settings.nao_port,
            pi_locator=TonyPiLocator(settings.pi_rpc_port, settings.pi_video_port),
            pi_ip=settings.pi_ip,
            pi_rpc_port=settings.pi_rpc_port,
        )

    try:
        result = service.scan(
            args.targets or None, args.timeout, robot=getattr(args, "robot", "all")
        )
    except RobotError as exc:
        print(f"扫描失败：{exc.message}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _report(result)
    return 0 if result["robots"] else 1


def _report(result: dict) -> None:
    counts = result["counts"]
    targets = ", ".join(result["targets"]) or "无可用网段"
    print(
        f"扫描 {targets}，用时 {result['elapsed_s']}s，发现 {counts['total']} 个候选："
    )

    if result["selected_robot"] in ("all", "nao"):
        nao = result["groups"]["nao"]
        print(f"\nNAO（{len(nao)} 个候选）：")
        if nao:
            for robot in nao:
                mark = "  （当前配置）" if robot["configured"] else ""
                print(f"- {robot['ip']}  NAOqi {robot['port']}  SSH 标识有效{mark}")
        else:
            print("  未发现（要求 9559 与 22 开放，且 SSH 标识有效）")

    if result["selected_robot"] in ("all", "pi"):
        tonypi = result["groups"]["tonypi"]
        print(f"\nTonyPi（{len(tonypi)} 个候选）：")
        if tonypi:
            for robot in tonypi:
                video = "已开放" if robot["video_open"] else "未开放"
                mark = "  （当前配置）" if robot["configured"] else ""
                print(
                    f"- {robot['ip']}  RPC {robot['port']} 已验证  "
                    f"视频 {robot['video_port']} {video}{mark}"
                )
        else:
            print("  未发现（要求 9030 开放且 JSON-RPC echo 验证通过）")

    if not result["robots"]:
        print(
            "\n请确认机器人已开机并与本机处于同一网段；容器或 WSL NAT 下请显式传入目标网段。"
        )


def main(argv: list[str] | None = None, service: DiscoveryService | None = None) -> int:
    configure_stdio()
    return run(build_parser().parse_args(argv), service)


if __name__ == "__main__":
    raise SystemExit(main())

"""命令行入口：``uv run embodied <命令>``。

- ``discover``：启动服务前扫描局域网，确认机器人 IP。
- ``serve``：启动 REST + MCP 服务，参数优先于环境变量 / ``.env``。
"""

from __future__ import annotations

import argparse

from . import __version__
from .discover import add_arguments, configure_stdio, run
from .service.config import Settings
from .service.discovery import DiscoveryService

_SERVE_FIELDS = ("host", "port", "log_level")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="embodied", description="NAO 具身智能交互")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    discover = commands.add_parser(
        "discover",
        help="扫描局域网中的 NAO 机器人",
        description="扫描局域网中的 NAO 机器人（探测 9559 与 22，并校验 SSH 标识）。",
    )
    add_arguments(discover)

    serve = commands.add_parser(
        "serve",
        help="启动 REST + MCP 服务",
        description="启动 REST + MCP 服务；未给出的参数回落到环境变量 / .env。",
    )
    serve.add_argument(
        "--robot",
        choices=["nao", "pi"],
        help="机器人类型：NAO 或 TonyPi（默认取 EMBODIED_ROBOT，否则 nao）",
    )
    serve.add_argument("--robot-ip", dest="robot_ip", metavar="IP", help="机器人 IP")
    serve.add_argument(
        "--robot-port",
        dest="robot_port",
        type=int,
        metavar="PORT",
        help="NAOqi 或 TonyPi RPC 端口（默认分别为 9559 / 9030）",
    )
    serve.add_argument("--host", metavar="HOST", help="服务监听地址（默认 0.0.0.0）")
    serve.add_argument(
        "--port", type=int, metavar="PORT", help="服务监听端口（默认 19559）"
    )
    serve.add_argument(
        "--dry-run", action="store_true", help="不连接机器人，使用假实现"
    )
    serve.add_argument(
        "--log-level",
        dest="log_level",
        type=str.lower,
        choices=["critical", "error", "warning", "info", "debug", "trace"],
        help="日志级别（默认 info）",
    )
    return parser


def build_settings(args: argparse.Namespace) -> Settings:
    overrides = {
        field: value
        for field in _SERVE_FIELDS
        if (value := getattr(args, field, None)) is not None
    }
    selected_robot = getattr(args, "robot", None) or Settings().robot
    if getattr(args, "robot", None) is not None:
        overrides["robot"] = selected_robot
    if (ip := getattr(args, "robot_ip", None)) is not None:
        overrides["pi_ip" if selected_robot == "pi" else "nao_ip"] = ip
    if (port := getattr(args, "robot_port", None)) is not None:
        overrides["pi_rpc_port" if selected_robot == "pi" else "nao_port"] = port
    if getattr(args, "dry_run", False):
        overrides["dry_run"] = True
    return Settings(**overrides)


def serve(args: argparse.Namespace) -> int:
    import logging

    import uvicorn

    from .app import create_app

    settings = build_settings(args)
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO)
    )
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )
    return 0


def main(argv: list[str] | None = None, service: DiscoveryService | None = None) -> int:
    configure_stdio()
    args = build_parser().parse_args(argv)
    if args.command == "discover":
        return run(args, service)
    return serve(args)


if __name__ == "__main__":
    raise SystemExit(main())

"""CLI 装配：参数到 Settings 的映射与子命令分发。"""

from __future__ import annotations

import argparse

from embodied import cli


def test_serve_options_override_settings() -> None:
    args = cli.build_parser().parse_args(
        [
            "serve",
            "--robot-ip",
            "192.168.2.34",
            "--robot-port",
            "9560",
            "--host",
            "127.0.0.1",
            "--port",
            "9000",
            "--dry-run",
            "--log-level",
            "DEBUG",
        ]
    )

    settings = cli.build_settings(args)

    assert settings.nao_ip == "192.168.2.34"
    assert settings.nao_port == 9560
    assert settings.host == "127.0.0.1"
    assert settings.port == 9000
    assert settings.dry_run is True
    assert settings.log_level == "debug"


def test_serve_without_options_keeps_defaults(monkeypatch) -> None:
    for name in (
        "NAO_IP",
        "NAO_PORT",
        "EMBODIED_HOST",
        "EMBODIED_PORT",
        "EMBODIED_DRY_RUN",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = cli.build_settings(cli.build_parser().parse_args(["serve"]))

    assert settings.nao_ip == "127.0.0.1"
    assert settings.nao_port == 9559
    assert settings.host == "0.0.0.0"
    assert settings.port == 19559
    assert settings.dry_run is False


def test_entrypoint_dispatches_serve(monkeypatch) -> None:
    seen: dict[str, argparse.Namespace] = {}

    def fake_serve(args: argparse.Namespace) -> int:
        seen["args"] = args
        return 0

    monkeypatch.setattr(cli, "serve", fake_serve)

    assert cli.main(["serve", "--robot-ip", "192.168.2.34"]) == 0
    assert seen["args"].command == "serve"
    assert seen["args"].robot_ip == "192.168.2.34"


def test_serve_pi_options_override_settings() -> None:
    args = cli.build_parser().parse_args(
        [
            "serve",
            "--robot",
            "pi",
            "--robot-ip",
            "192.168.1.104",
            "--robot-port",
            "9031",
            "--dry-run",
        ]
    )

    settings = cli.build_settings(args)

    assert settings.robot == "pi"
    assert settings.pi_ip == "192.168.1.104"
    assert settings.pi_rpc_port == 9031
    assert settings.pi_video_port == 8080
    assert settings.dry_run is True


def test_robot_ip_uses_type_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("EMBODIED_ROBOT", "pi")
    args = cli.build_parser().parse_args(["serve", "--robot-ip", "192.168.1.104"])

    settings = cli.build_settings(args)

    assert settings.robot == "pi"
    assert settings.pi_ip == "192.168.1.104"


def test_serve_default_robot_is_nao(monkeypatch) -> None:
    monkeypatch.delenv("EMBODIED_ROBOT", raising=False)
    settings = cli.build_settings(cli.build_parser().parse_args(["serve"]))
    assert settings.robot == "nao"

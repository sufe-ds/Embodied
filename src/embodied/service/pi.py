"""TonyPi 动作、移动、云台与视觉模式应用服务。"""

from __future__ import annotations

from .ports import TonyPiPort


class TonyPiService:
    def __init__(self, robot: TonyPiPort) -> None:
        self._robot = robot

    def list_actions(self) -> dict[str, str]:
        return self._robot.list_actions()

    def run_action(self, name: str, times: int = 1) -> str:
        return self._robot.run_action(name, times)

    def move(self, direction: str, steps: int = 1) -> str:
        return self._robot.move_direction(direction, steps)

    def set_head(self, horizontal: int, vertical: int, duration_ms: int = 500) -> None:
        self._robot.set_head(horizontal, vertical, duration_ms)

    def list_vision_modes(self) -> dict[str, str]:
        return self._robot.list_vision_modes()

    def start_vision_mode(self, mode: str) -> str:
        return self._robot.start_vision_mode(mode)

    def stop_vision_mode(self) -> None:
        self._robot.stop_vision_mode()

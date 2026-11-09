"""TonyPi 专属 MCP 工具，参数与原生动作组语义一致。"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from fastmcp.utilities.types import Image

from ..domain.errors import RobotError


def _error(exc: RobotError) -> dict:
    return {
        "ok": False,
        "error": exc.code,
        "message": exc.message,
        "detail": exc.detail,
    }


def build_pi_mcp(container: Any) -> FastMCP:
    mcp = FastMCP(
        "TonyPi Embodied",
        instructions=(
            "操控 TonyPi 的原生动作组、方向移动、双舵机云台、摄像头和视觉模式。"
            "移动按动作次数计量，不使用米或弧度；动作提交不代表执行完成。"
            "times=0 表示循环动作，stop 可停止动作和视觉模式。视觉模式可能引发自主运动。"
        ),
    )

    @mcp.tool
    def robot_status() -> dict:
        """查询 TonyPi 连接状态、原生接口地址、摄像头和当前视觉模式。"""
        data = container.gateway.status()
        data["camera"] = container.camera.info()
        return data

    @mcp.tool
    def stop() -> dict:
        """停止当前动作组及由 Embodied 启动的视觉模式。"""
        try:
            container.gateway.stop_all()
            return {"ok": True}
        except RobotError as exc:
            return _error(exc)

    @mcp.tool
    def list_actions() -> dict:
        """列出常用官方动作组；这不是机器人动作文件的实时枚举。"""
        return {
            "actions": container.tony_pi.list_actions(),
            "custom_actions": "run_action 也接受机器人上已有的字母、数字、下划线动作名",
        }

    @mcp.tool
    def run_action(name: str, times: int = 1) -> dict:
        """提交动作组。times 取 0-100，0 表示循环到 stop；返回接受而非完成状态。"""
        try:
            action = container.tony_pi.run_action(name, times)
            return {"ok": True, "accepted": True, "name": action, "times": times}
        except RobotError as exc:
            return _error(exc)

    @mcp.tool
    def move(direction: str, steps: int = 1) -> dict:
        """按方向执行离散步态：forward/backward/left/right/turn_left/turn_right；steps 取 1-20。"""
        try:
            action = container.tony_pi.move(direction, steps)
            return {
                "ok": True,
                "accepted": True,
                "direction": direction,
                "action": action,
                "steps": steps,
            }
        except RobotError as exc:
            return _error(exc)

    @mcp.tool
    def set_head(
        horizontal: int = 1500, vertical: int = 1500, duration_ms: int = 500
    ) -> dict:
        """设置云台水平/垂直舵机脉宽（500-2500，1500 居中）及运动毫秒数。"""
        try:
            container.tony_pi.set_head(horizontal, vertical, duration_ms)
            return {"ok": True}
        except RobotError as exc:
            return _error(exc)

    @mcp.tool
    def look() -> Any:
        """从 TonyPi 官方视频服务拍一张 JPEG；分辨率由机器人视频服务决定。"""
        try:
            frame = container.camera.snapshot()
        except RobotError as exc:
            return _error(exc)
        return Image(data=frame, format="jpeg")

    @mcp.tool
    def list_vision_modes() -> dict:
        """列出支持的视觉模式；这些模式可能控制机器人自主运动。"""
        return {"modes": container.tony_pi.list_vision_modes()}

    @mcp.tool
    def start_vision_mode(mode: str) -> dict:
        """启动视觉模式并维持官方心跳；确认周围安全后使用。"""
        try:
            started = container.tony_pi.start_vision_mode(mode)
            return {"ok": True, "mode": started}
        except RobotError as exc:
            return _error(exc)

    @mcp.tool
    def stop_vision_mode() -> dict:
        """停止并卸载由 Embodied 启动的视觉模式。"""
        try:
            container.tony_pi.stop_vision_mode()
            return {"ok": True}
        except RobotError as exc:
            return _error(exc)

    return mcp

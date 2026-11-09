"""TonyPi 机器人适配器：原生 JSON-RPC 控制与 MJPG 画面。"""

from __future__ import annotations

import re
import threading
import time
from typing import Any

from ..domain.entities import BehaviorResult, Movement, MovementResult, SpeechResult
from ..domain.errors import (
    RobotCallError,
    RobotError,
    ServiceUnavailableError,
    ValidationError,
)
from ..domain.values import Color
from .config import Settings
from .pi_client import (
    FakeTonyPiRpc,
    FakeTonyPiVideo,
    RpcTransport,
    TonyPiRpcClient,
    TonyPiVideoClient,
    VideoTransport,
)
from .ports import RobotPort, TonyPiPort

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_]+$")
_ACTIONS = {
    "stand": "立正",
    "stand_slow": "缓慢立正",
    "go_forward": "前进",
    "back_fast": "后退",
    "left_move_fast": "左移",
    "right_move_fast": "右移",
    "turn_left": "左转",
    "turn_right": "右转",
    "push_ups": "俯卧撑",
    "sit_ups": "仰卧起坐",
    "wave": "挥手",
    "bow": "鞠躬",
    "squat": "下蹲",
    "chest": "庆祝",
    "left_shot_fast": "左脚踢球",
    "right_shot_fast": "右脚踢球",
    "wing_chun": "咏春",
    "left_uppercut": "左勾拳",
    "right_uppercut": "右勾拳",
    "left_kick": "左侧踢",
    "right_kick": "右侧踢",
    "stand_up_front": "向前跌倒后起立",
    "stand_up_back": "向后跌倒后起立",
    "twist": "扭腰",
    "stepping": "原地踏步",
    "jugong": "鞠躬（另一动作组）",
    "weightlifting": "举重",
}
_DIRECTIONS = {
    "forward": "go_forward",
    "backward": "back_fast",
    "left": "left_move_fast",
    "right": "right_move_fast",
    "turn_left": "turn_left",
    "turn_right": "turn_right",
}
_VISION_MODES = {
    "kick_ball": "自动踢球",
    "line_following": "智能巡线",
    "color_tracking": "颜色追踪",
    "face_detection": "人脸识别",
    "apriltag_detection": "AprilTag 标签识别",
    "transport": "智能搬运",
}
_VISION_MODE_IDS = {
    "kick_ball": 2,
    "line_following": 4,
    "color_tracking": 5,
    "face_detection": 6,
    "apriltag_detection": 7,
    "transport": 8,
}


class PiCameraStreamer:
    """读取官方 multipart 流并按 JPEG 帧边界缓存最新画面。"""

    def __init__(self, video: VideoTransport, fps: int) -> None:
        self.video = video
        self.fps = max(1, fps)
        self._thread: threading.Thread | None = None
        self._response: Any = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._frame: bytes | None = None
        self._frame_ts = 0.0
        self._error: str | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="embodied-pi-camera", daemon=True
        )
        self._thread.start()

    def _save_frame(self, frame: bytes) -> None:
        with self._lock:
            self._frame = frame
            self._frame_ts = time.time()
            self._error = None

    def _run(self) -> None:
        try:
            self._response = self.video.stream()
            if self._response is None:
                self._save_frame(self.video.snapshot())
                self._stop.wait()
                return
            buffer = b""
            for chunk in self._response.iter_content(chunk_size=16384):
                if self._stop.is_set():
                    break
                buffer += chunk
                while True:
                    start = buffer.find(b"\xff\xd8")
                    if start < 0:
                        buffer = buffer[-1:]
                        break
                    end = buffer.find(b"\xff\xd9", start + 2)
                    if end < 0:
                        if start:
                            buffer = buffer[start:]
                        break
                    self._save_frame(buffer[start : end + 2])
                    buffer = buffer[end + 2 :]
                if len(buffer) > 10_000_000:
                    raise ValueError("MJPG 帧超过 10 MB")
        except Exception as exc:  # noqa: BLE001
            if not self._stop.is_set():
                self._error = str(exc)
        finally:
            if self._response is not None:
                self._response.close()
                self._response = None

    def snapshot(self) -> bytes | None:
        with self._lock:
            return self._frame

    def wait_for_frame(self, timeout: float = 3.0) -> bytes | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if (frame := self.snapshot()) is not None:
                return frame
            time.sleep(0.05)
        return self.snapshot()

    def info(self) -> dict:
        with self._lock:
            return {
                "source": "tonypi_mjpg",
                "fps": self.fps,
                "age_s": (
                    None
                    if self._frame is None
                    else round(time.time() - self._frame_ts, 3)
                ),
                "running": self._thread is not None and self._thread.is_alive(),
                "error": self._error,
            }

    def stop(self) -> None:
        self._stop.set()
        if self._response is not None:
            self._response.close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None


class TonyPiRobot(RobotPort, TonyPiPort):
    def __init__(
        self,
        settings: Settings,
        rpc: RpcTransport | None = None,
        video: VideoTransport | None = None,
    ) -> None:
        self.settings = settings
        self._rpc = rpc or (
            FakeTonyPiRpc()
            if settings.dry_run
            else TonyPiRpcClient(
                settings.pi_ip, settings.pi_rpc_port, settings.pi_timeout_s
            )
        )
        self._video = video or (
            FakeTonyPiVideo()
            if settings.dry_run
            else TonyPiVideoClient(
                settings.pi_ip, settings.pi_video_port, settings.pi_timeout_s
            )
        )
        self._connected = False
        self._motion_lock = threading.RLock()
        self._mode_lock = threading.RLock()
        self._camera_lock = threading.Lock()
        self._camera: PiCameraStreamer | None = None
        self._active_mode: str | None = None
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None

    @staticmethod
    def _unsupported(capability: str) -> None:
        raise ServiceUnavailableError(f"TonyPi 原生接口不支持{capability}")

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self) -> None:
        token = "embodied-tonypi-probe"
        result = self._rpc.call("echo", [token])
        if result != token:
            raise RobotCallError("TonyPi RPC 探测响应不匹配", detail=result)
        self._connected = True

    def disconnect(self) -> None:
        self.stop_camera()
        try:
            self.stop_vision_mode()
        except RobotError:
            pass
        self._connected = False

    def ping(self) -> bool:
        try:
            self.connect()
            return True
        except RobotError:
            self._connected = False
            return False

    def status(self) -> dict:
        return {
            "robot": "pi",
            "connected": self.connected,
            "ip": self.settings.pi_ip,
            "rpc_port": self.settings.pi_rpc_port,
            "video_port": self.settings.pi_video_port,
            "active_vision_mode": self._active_mode,
        }

    def list_actions(self) -> dict[str, str]:
        return dict(_ACTIONS)

    def run_action(self, name: str, times: int = 1) -> str:
        name = (name or "").strip()
        if not _SAFE_NAME.fullmatch(name):
            raise ValidationError("动作名只能包含字母、数字和下划线")
        if not 0 <= times <= 100:
            raise ValidationError("动作次数须在 0 到 100 之间，0 表示循环")
        with self._motion_lock:
            self._rpc.call("RunAction", [name, times])
        return name

    def move_direction(self, direction: str, steps: int = 1) -> str:
        if direction not in _DIRECTIONS:
            raise ValidationError("未知移动方向", detail=list(_DIRECTIONS))
        if not 1 <= steps <= 20:
            raise ValidationError("移动次数须在 1 到 20 之间")
        return self.run_action(_DIRECTIONS[direction], steps)

    def set_head(self, horizontal: int, vertical: int, duration_ms: int = 500) -> None:
        if not all(500 <= value <= 2500 for value in (horizontal, vertical)):
            raise ValidationError("云台脉宽须在 500 到 2500 之间")
        if not 100 <= duration_ms <= 10000:
            raise ValidationError("云台运动时长须在 100 到 10000 毫秒之间")
        with self._motion_lock:
            self._rpc.call(
                "SetPWMServo",
                [duration_ms, 2, 1, vertical, 2, horizontal],
            )

    def list_vision_modes(self) -> dict[str, str]:
        return dict(_VISION_MODES)

    def _heartbeat(self) -> None:
        while not self._heartbeat_stop.wait(3.0):
            try:
                self._rpc.call("Heartbeat")
            except RobotError:
                return

    def _start_heartbeat(self) -> None:
        self._heartbeat_stop.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat, name="embodied-pi-heartbeat", daemon=True
        )
        self._heartbeat_thread.start()

    def _stop_heartbeat(self) -> None:
        self._heartbeat_stop.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=1.0)
            self._heartbeat_thread = None

    def start_vision_mode(self, mode: str) -> str:
        if mode not in _VISION_MODE_IDS:
            raise ValidationError("未知视觉模式", detail=list(_VISION_MODE_IDS))
        with self._mode_lock:
            if self._active_mode == mode:
                return mode
            if self._active_mode is not None:
                self._stop_vision_mode()
            self._rpc.call("LoadFunc", [_VISION_MODE_IDS[mode]])
            try:
                self._rpc.call("StartFunc")
            except RobotError:
                self._rpc.call("UnloadFunc")
                raise
            self._active_mode = mode
            self._start_heartbeat()
        return mode

    def _stop_vision_mode(self) -> None:
        self._stop_heartbeat()
        if self._active_mode is None:
            return
        try:
            self._rpc.call("StopFunc")
        finally:
            self._rpc.call("UnloadFunc")
            self._active_mode = None

    def stop_vision_mode(self) -> None:
        with self._mode_lock:
            self._stop_vision_mode()

    def stop_all(self) -> None:
        try:
            self._rpc.call("StopActionGroup", ["stopActionGroup"])
        finally:
            self.stop_vision_mode()

    def start_camera(self) -> None:
        with self._camera_lock:
            if self._camera is None:
                self._camera = PiCameraStreamer(self._video, self.settings.camera_fps)
            self._camera.start()

    def stop_camera(self) -> None:
        with self._camera_lock:
            if self._camera is not None:
                self._camera.stop()
                self._camera = None

    def camera_snapshot(self, resolution: int | None = None) -> bytes:
        return self._video.snapshot()

    def camera_frame(self) -> bytes | None:
        return self._camera.snapshot() if self._camera is not None else None

    def camera_info(self) -> dict:
        if self._camera is None:
            return {
                "source": "tonypi_mjpg",
                "fps": self.settings.camera_fps,
                "age_s": None,
                "running": False,
                "error": None,
            }
        return self._camera.info()

    def say(
        self, text: str, language: str | None = None, animated: bool = False
    ) -> None:
        self._unsupported("语音合成")

    def stop_speaking(self) -> None:
        self._unsupported("停止语音")

    def wake_up(self) -> None:
        self._unsupported("NAO 唤醒语义，请使用 stand 动作")

    def rest(self) -> None:
        self._unsupported("NAO 休息语义")

    def list_postures(self) -> list[str]:
        self._unsupported("NAO 姿态列表")

    def go_to_posture(self, name: str, speed: float = 0.5) -> str:
        self._unsupported("NAO 姿态切换")

    def set_angles(
        self, names: list[str], angles: list[float], speed: float = 0.2
    ) -> None:
        self._unsupported("NAO 关节弧度控制")

    def move(self, x: float, y: float, theta: float) -> None:
        self._unsupported("米/弧度移动，请使用方向与动作次数")

    def play_trajectory(self, movement: Movement) -> MovementResult:
        self._unsupported("NAO LabVIEW 轨迹")

    def list_behaviors(self) -> list[str]:
        self._unsupported("NAO 官方行为库")

    def run_behavior(self, name: str) -> BehaviorResult:
        self._unsupported("NAO 官方行为库")

    def listen(
        self,
        vocabulary: list[str],
        timeout_s: float = 6.0,
        word_spotting: bool = True,
        language: str | None = None,
        min_confidence: float = 0.0,
    ) -> SpeechResult:
        self._unsupported("语音识别")

    def set_leds(self, name: str, color: Color, duration: float) -> None:
        self._unsupported("NAO LED 控制")

    def leds_off(self, name: str) -> None:
        self._unsupported("NAO LED 控制")

    def set_led_intensity(self, name: str, intensity: float) -> None:
        self._unsupported("NAO LED 控制")

    def rasta(self, duration: float) -> None:
        self._unsupported("NAO LED 控制")

    def get_volume(self) -> int:
        self._unsupported("音量查询")

    def set_volume(self, volume: int) -> int:
        self._unsupported("音量设置")

    def call_service(self, service: str, method: str, args: list | None = None) -> Any:
        self._unsupported("NAOqi 逃生舱")

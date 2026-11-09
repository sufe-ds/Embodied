"""TonyPi 原生 RPC、动作、视觉模式与视频适配器测试。"""

from __future__ import annotations

import pytest
import requests

from embodied.domain.errors import (
    RobotCallError,
    RobotUnreachableError,
    ServiceUnavailableError,
    ValidationError,
)
from embodied.service.config import Settings
from embodied.service.pi_client import (
    FakeTonyPiVideo,
    TonyPiRpcClient,
    TonyPiVideoClient,
)
from embodied.service.pi_robot import PiCameraStreamer, TonyPiRobot


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, robot="pi", dry_run=True, camera_fps=5)


@pytest.fixture
def robot(settings: Settings) -> TonyPiRobot:
    return TonyPiRobot(settings)


def test_connect_is_read_only_probe(robot: TonyPiRobot) -> None:
    robot.connect()

    assert robot.connected is True
    assert robot._rpc.calls == [("echo", ["embodied-tonypi-probe"])]
    assert robot.status()["robot"] == "pi"


def test_action_direction_and_head_use_native_semantics(robot: TonyPiRobot) -> None:
    assert robot.run_action("wave", 2) == "wave"
    assert robot.move_direction("forward", 3) == "go_forward"
    robot.set_head(1600, 1400, 600)

    assert ("RunAction", ["wave", 2]) in robot._rpc.calls
    assert ("RunAction", ["go_forward", 3]) in robot._rpc.calls
    assert ("SetPWMServo", [600, 2, 1, 1400, 2, 1600]) in robot._rpc.calls


@pytest.mark.parametrize("name", ["", "../stand", "bad-name", "stand;stop"])
def test_action_rejects_unsafe_names(robot: TonyPiRobot, name: str) -> None:
    with pytest.raises(ValidationError):
        robot.run_action(name)


def test_action_rejects_invalid_count(robot: TonyPiRobot) -> None:
    with pytest.raises(ValidationError):
        robot.run_action("wave", 101)
    with pytest.raises(ValidationError):
        robot.move_direction("forward", 0)


def test_vision_mode_lifecycle_and_stop(robot: TonyPiRobot) -> None:
    assert robot.start_vision_mode("face_detection") == "face_detection"
    assert robot._heartbeat_thread is not None
    assert robot._heartbeat_thread.is_alive()

    robot.stop_all()

    calls = robot._rpc.calls
    assert ("LoadFunc", [6]) in calls
    assert ("StartFunc", []) in calls
    assert ("StopActionGroup", ["stopActionGroup"]) in calls
    assert ("StopFunc", []) in calls
    assert ("UnloadFunc", []) in calls
    assert robot.status()["active_vision_mode"] is None
    assert robot._heartbeat_thread is None


def test_unknown_vision_mode(robot: TonyPiRobot) -> None:
    with pytest.raises(ValidationError):
        robot.start_vision_mode("gesture_control")


def test_fake_camera_snapshot_and_stream(robot: TonyPiRobot) -> None:
    frame = robot.camera_snapshot()
    assert frame[:2] == b"\xff\xd8"

    robot.start_camera()
    try:
        assert robot._camera is not None
        assert robot._camera.wait_for_frame(timeout=1.0) == frame
        assert robot.camera_info()["source"] == "tonypi_mjpg"
    finally:
        robot.stop_camera()


class _Stream:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.closed = False

    def iter_content(self, chunk_size: int):
        yield from self.chunks

    def close(self) -> None:
        self.closed = True


class _ChunkedVideo(FakeTonyPiVideo):
    def __init__(self) -> None:
        super().__init__()
        self.response = _Stream(
            [b"multipart-headers" + self.frame[:11], self.frame[11:] + b"next"]
        )

    def stream(self) -> _Stream:
        return self.response


def test_mjpeg_parser_extracts_jpeg() -> None:
    video = _ChunkedVideo()
    streamer = PiCameraStreamer(video, fps=5)
    streamer.start()
    try:
        assert streamer.wait_for_frame(timeout=1.0) == video.frame
    finally:
        streamer.stop()
    assert video.response.closed is True


def test_video_rejects_invalid_jpeg() -> None:
    with pytest.raises(ServiceUnavailableError):
        TonyPiVideoClient._validate_jpeg(b"not-a-jpeg")


def test_rpc_network_error(monkeypatch) -> None:
    def fail_post(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(requests.Session, "post", fail_post)
    client = TonyPiRpcClient("192.0.2.1", 9030)
    with pytest.raises(RobotUnreachableError):
        client.call("echo", ["probe"])


def test_rpc_rejects_failed_result(monkeypatch) -> None:
    class _Response:
        status_code = 200
        text = ""

        @staticmethod
        def json() -> dict:
            return {"result": [False, "E03", "RunAction"]}

    monkeypatch.setattr(requests.Session, "post", lambda *args, **kwargs: _Response())
    client = TonyPiRpcClient("192.0.2.1", 9030)
    with pytest.raises(RobotCallError):
        client.call("RunAction", ["wave", 1])


def test_nao_only_capabilities_are_not_fake(robot: TonyPiRobot) -> None:
    with pytest.raises(ServiceUnavailableError):
        robot.move(1.0, 0.0, 0.0)
    with pytest.raises(ServiceUnavailableError):
        robot.say("hello")

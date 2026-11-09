"""TonyPi 原生 JSON-RPC 与 MJPG HTTP 客户端。"""

from __future__ import annotations

import io
import threading
from typing import Any, Protocol

import requests
from PIL import Image

from ..domain.errors import (
    RobotCallError,
    RobotUnreachableError,
    ServiceUnavailableError,
)


class RpcTransport(Protocol):
    def call(self, method: str, params: list | None = None) -> Any: ...


class VideoTransport(Protocol):
    def snapshot(self) -> bytes: ...

    def stream(self) -> Any: ...


class TonyPiRpcClient:
    def __init__(self, ip: str, port: int, timeout_s: float = 5.0) -> None:
        self.url = f"http://{ip}:{port}"
        self.timeout_s = timeout_s
        self._session = requests.Session()
        self._session.trust_env = False
        self._lock = threading.Lock()
        self._request_id = 0

    def call(self, method: str, params: list | None = None) -> Any:
        with self._lock:
            self._request_id += 1
            request_id = self._request_id
            try:
                response = self._session.post(
                    self.url,
                    json={
                        "jsonrpc": "2.0",
                        "method": method,
                        "params": params or [],
                        "id": request_id,
                    },
                    timeout=self.timeout_s,
                )
            except requests.RequestException as exc:
                raise RobotUnreachableError(
                    f"无法连接 TonyPi RPC {self.url}", detail=str(exc)
                ) from exc
        if response.status_code != 200:
            raise RobotCallError(
                f"TonyPi RPC {method} 返回 HTTP {response.status_code}",
                detail=response.text[:500],
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RobotCallError(f"TonyPi RPC {method} 返回了非法 JSON") from exc
        if payload.get("error") is not None:
            raise RobotCallError(
                f"TonyPi RPC {method} 调用失败", detail=payload["error"]
            )
        result = payload.get("result")
        if isinstance(result, list) and result and result[0] is False:
            raise RobotCallError(f"TonyPi RPC {method} 调用失败", detail=result)
        return result


class TonyPiVideoClient:
    def __init__(self, ip: str, port: int, timeout_s: float = 5.0) -> None:
        self.url = f"http://{ip}:{port}/"
        self.timeout_s = timeout_s
        self._session = requests.Session()
        self._session.trust_env = False

    def _get(self, **kwargs: Any) -> requests.Response:
        try:
            response = self._session.get(self.url, timeout=self.timeout_s, **kwargs)
        except requests.RequestException as exc:
            raise ServiceUnavailableError(
                f"无法连接 TonyPi 视频服务 {self.url}", detail=str(exc)
            ) from exc
        if response.status_code != 200:
            response.close()
            raise ServiceUnavailableError(
                f"TonyPi 视频服务返回 HTTP {response.status_code}",
                detail="摄像头可能尚未启动或当前没有画面",
            )
        return response

    @staticmethod
    def _validate_jpeg(data: bytes) -> bytes:
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.format != "JPEG":
                    raise ValueError("not JPEG")
                image.verify()
        except (ValueError, OSError) as exc:
            raise ServiceUnavailableError("TonyPi 返回了非法 JPEG 画面") from exc
        return data

    def snapshot(self) -> bytes:
        response = self._get(params={"action": "snapshot"})
        try:
            return self._validate_jpeg(response.content)
        finally:
            response.close()

    def stream(self) -> requests.Response:
        return self._get(stream=True)


def _fake_jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (640, 480), (40, 80, 100)).save(buffer, "JPEG")
    return buffer.getvalue()


class FakeTonyPiRpc:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list]] = []
        self.mode_id = 0

    def call(self, method: str, params: list | None = None) -> Any:
        params = params or []
        self.calls.append((method, params))
        if method == "echo":
            return params[0]
        if method == "GetRunningFunc":
            return [True, [self.mode_id], "GetRunningFunc"]
        if method == "LoadFunc":
            self.mode_id = params[0]
            return [True, [self.mode_id], "LoadFunc"]
        if method in ("StartFunc", "StopFunc"):
            return [True, [self.mode_id], method]
        if method == "UnloadFunc":
            self.mode_id = 0
            return [True, [0], "UnloadFunc"]
        if method in (
            "RunAction",
            "StopActionGroup",
            "SetPWMServo",
            "Heartbeat",
        ):
            return [True, [], method]
        raise RobotCallError(f"假 TonyPi RPC 不支持 {method}")


class FakeTonyPiVideo:
    def __init__(self) -> None:
        self.frame = _fake_jpeg()

    def snapshot(self) -> bytes:
        return self.frame

    def stream(self) -> None:
        return None

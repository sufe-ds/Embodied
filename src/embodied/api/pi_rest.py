"""TonyPi REST 路由：仅暴露原生 RPC 与 MJPG 支持的能力。"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response, StreamingResponse

from ..domain.errors import RobotError, RobotUnreachableError
from . import schemas

pi_router = APIRouter()
_MJPEG_BOUNDARY = "frame"


def _container(request: Request) -> Any:
    container = getattr(request.app.state, "container", None)
    if container is None:
        raise RobotUnreachableError("机器人未初始化")
    return container


@pi_router.get("/health")
def health(request: Request) -> dict:
    container = getattr(request.app.state, "container", None)
    if container is None:
        return {"status": "ok", "robot": {"connected": False}}
    try:
        status = container.gateway.status()
        if not container.gateway.connected():
            container.gateway.connect()
            status = container.gateway.status()
        return {"status": "ok", "robot": status}
    except RobotError as exc:
        return {
            "status": "degraded",
            "robot": {"connected": False, "error": exc.message},
        }


@pi_router.get("/api/robot/status")
def robot_status(request: Request) -> dict:
    data = _container(request).gateway.status()
    data["camera"] = _container(request).camera.info()
    return data


@pi_router.post("/api/robot/stop")
def stop(request: Request) -> dict:
    _container(request).gateway.stop_all()
    return {"ok": True}


@pi_router.get("/api/actions")
def list_actions(request: Request) -> dict:
    return {
        "actions": _container(request).tony_pi.list_actions(),
        "note": "常用官方预设，不是机器人动作文件的实时枚举",
    }


@pi_router.post("/api/actions/{name}", status_code=202)
def run_action(request: Request, name: str, body: schemas.PiActionRequest) -> dict:
    action = _container(request).tony_pi.run_action(name, body.times)
    return {
        "ok": True,
        "accepted": True,
        "name": action,
        "times": body.times,
        "note": "原生 RPC 不提供动作完成状态；times=0 时循环到停止",
    }


@pi_router.post("/api/robot/move", status_code=202)
def move(request: Request, body: schemas.PiMoveRequest) -> dict:
    action = _container(request).tony_pi.move(body.direction, body.steps)
    return {
        "ok": True,
        "accepted": True,
        "direction": body.direction,
        "action": action,
        "steps": body.steps,
    }


@pi_router.post("/api/robot/head")
def set_head(request: Request, body: schemas.PiHeadRequest) -> dict:
    _container(request).tony_pi.set_head(
        body.horizontal, body.vertical, body.duration_ms
    )
    return {"ok": True}


@pi_router.get("/api/vision/modes")
def list_vision_modes(request: Request) -> dict:
    return {"modes": _container(request).tony_pi.list_vision_modes()}


@pi_router.post("/api/vision/modes/{mode}/start")
def start_vision_mode(request: Request, mode: str) -> dict:
    started = _container(request).tony_pi.start_vision_mode(mode)
    return {"ok": True, "mode": started}


@pi_router.post("/api/vision/modes/stop")
def stop_vision_mode(request: Request) -> dict:
    _container(request).tony_pi.stop_vision_mode()
    return {"ok": True}


@pi_router.get("/api/camera/info")
def camera_info(request: Request) -> dict:
    return _container(request).camera.info()


@pi_router.get("/api/camera/snapshot")
def camera_snapshot(request: Request) -> Response:
    return Response(
        content=_container(request).camera.snapshot(), media_type="image/jpeg"
    )


@pi_router.get("/api/camera/mjpeg")
async def camera_mjpeg(request: Request) -> StreamingResponse:
    container = _container(request)
    await run_in_threadpool(container.camera.start)
    interval = 1.0 / max(1, container.settings.camera_fps)

    async def generate():
        while True:
            if await request.is_disconnected():
                break
            frame = await run_in_threadpool(container.camera.frame)
            if frame:
                yield (
                    (
                        f"--{_MJPEG_BOUNDARY}\r\n"
                        "Content-Type: image/jpeg\r\n"
                        f"Content-Length: {len(frame)}\r\n\r\n"
                    ).encode()
                    + frame
                    + b"\r\n"
                )
            await asyncio.sleep(interval)

    return StreamingResponse(
        generate(),
        media_type=f"multipart/x-mixed-replace; boundary={_MJPEG_BOUNDARY}",
    )

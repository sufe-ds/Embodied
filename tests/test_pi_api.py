"""TonyPi REST 与 MCP 工具集测试，全部使用假传输。"""

from __future__ import annotations

import httpx
import pytest

from embodied.api import build_mcp
from embodied.app import create_app
from embodied.service.config import Settings


@pytest.fixture
def app():
    return create_app(
        Settings(
            _env_file=None,
            robot="pi",
            dry_run=True,
            api_token=None,
            camera_fps=5,
        )
    )


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_health_and_status(client) -> None:
    health = await client.get("/health")
    assert health.status_code == 200
    assert health.json()["robot"]["robot"] == "pi"

    status = await client.get("/api/robot/status")
    assert status.status_code == 200
    assert status.json()["rpc_port"] == 9030
    assert status.json()["camera"]["source"] == "tonypi_mjpg"


async def test_action_and_move_are_accepted_not_job_completed(client, app) -> None:
    action = await client.post("/api/actions/wave", json={"times": 2})
    assert action.status_code == 202
    assert action.json()["accepted"] is True
    assert "job_id" not in action.json()

    move = await client.post(
        "/api/robot/move", json={"direction": "forward", "steps": 3}
    )
    assert move.status_code == 202
    assert move.json()["action"] == "go_forward"
    calls = app.state.container.robot._rpc.calls
    assert ("RunAction", ["wave", 2]) in calls
    assert ("RunAction", ["go_forward", 3]) in calls


async def test_action_validation(client) -> None:
    response = await client.post("/api/actions/bad-name", json={})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_head_and_vision_mode(client) -> None:
    head = await client.post(
        "/api/robot/head",
        json={"horizontal": 1600, "vertical": 1400, "duration_ms": 600},
    )
    assert head.status_code == 200

    modes = await client.get("/api/vision/modes")
    assert "face_detection" in modes.json()["modes"]
    started = await client.post("/api/vision/modes/face_detection/start")
    assert started.status_code == 200
    stopped = await client.post("/api/vision/modes/stop")
    assert stopped.status_code == 200


async def test_camera_snapshot(client) -> None:
    response = await client.get("/api/camera/snapshot")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content[:2] == b"\xff\xd8"


async def test_nao_routes_are_not_registered_for_pi(client) -> None:
    assert (
        await client.post("/api/robot/say", json={"text": "hello"})
    ).status_code == 404
    assert (await client.get("/api/movements")).status_code == 404
    assert (await client.get("/api/jobs")).status_code == 404


async def test_pi_mcp_toolset_is_specific(app) -> None:
    mcp = build_mcp(app.state.container)
    tools = {tool.name for tool in await mcp.list_tools()}

    assert tools == {
        "robot_status",
        "stop",
        "list_actions",
        "run_action",
        "move",
        "set_head",
        "look",
        "list_vision_modes",
        "start_vision_mode",
        "stop_vision_mode",
    }
    assert "say" not in tools
    assert "walk" not in tools
    assert "run_behavior" not in tools


async def test_nao_mcp_toolset_is_unchanged() -> None:
    app = create_app(
        Settings(_env_file=None, robot="nao", dry_run=True, api_token=None)
    )
    mcp = build_mcp(app.state.container)
    tools = {tool.name for tool in await mcp.list_tools()}

    assert "say" in tools
    assert "walk" in tools
    assert "run_behavior" in tools
    assert "run_action" not in tools
    assert "start_vision_mode" not in tools

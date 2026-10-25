"""测试共享夹具。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


os.environ["EMBODIED_MCP_PATH"] = "/mcp"


_MOVEMENT_SOURCE = '''\
def main():
    return (
        "Stand",
        1.0,
        [["HeadYaw", [[0.0, 0.1, [3, 0.0, 0.0], [3, 0.0, 0.0]]]]],
    )
'''


@pytest.fixture
def movement_dir(tmp_path: Path) -> Path:
    for name in ("move1", "getlucky", "applaud"):
        (tmp_path / f"{name}.py").write_text(_MOVEMENT_SOURCE, encoding="utf-8")
    return tmp_path

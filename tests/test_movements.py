"""动作仓储与领域展平测试。"""

from __future__ import annotations

import pytest

from embodied.domain.errors import UnknownMovementError
from embodied.domain.trajectory import flatten
from embodied.service.movement_files import FileMovementRepository


@pytest.fixture
def repo(movement_dir) -> FileMovementRepository:
    return FileMovementRepository(movement_dir)


@pytest.mark.parametrize("bad", ["../evil", "a/b", "..", "", "x.py"])
def test_resolve_rejects_bad_names(repo: FileMovementRepository, bad: str) -> None:
    with pytest.raises(UnknownMovementError):
        repo.load(bad)


def test_exists_case_insensitive(repo: FileMovementRepository) -> None:
    assert repo.exists("GETLUCKY")
    assert not repo.exists("nope")


def test_load_move1_shape(repo: FileMovementRepository) -> None:
    movement = repo.load("move1")
    assert movement.posture in ("Stand", "")
    assert movement.names
    assert len(movement.names) == len(movement.times) == len(movement.angles)
    assert len(movement.times[0]) == len(movement.angles[0])
    assert len(movement.angles[0][0]) == 3


def test_list_includes_known_moves(repo: FileMovementRepository) -> None:
    names = {info.name.lower() for info in repo.list()}
    assert "move1" in names
    assert "applaud" in names


def test_flatten_manual() -> None:
    movements = [["HeadYaw", [[1.0, 0.1, [3, 0.0, 0.0], [3, 0.0, 0.0]]]]]
    names, times, angles = flatten(movements)
    assert names == ["HeadYaw"]
    assert times == [[1.0]]
    assert angles == [[[0.1, [3, 0.0, 0.0], [3, 0.0, 0.0]]]]

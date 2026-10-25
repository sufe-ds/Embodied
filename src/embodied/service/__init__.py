"""服务层：应用服务 + 抽象端口 + 具体实现（本项目仅 domain / service / api 三层）。

- ``ports``：抽象接口（``RobotPort`` / ``RobotLocator`` / ``MovementRepository`` / ``JobQueue``）。
- 应用服务（``gateway`` 等）只依赖 ``ports`` 与领域模型。
- 具体实现（``qi_robot`` / ``fake_robot`` / ``network_scan`` / ``movement_files`` /
  ``thread_jobs`` / ``config``）也放在本层，由组装根 ``container.py`` 注入；``api`` 只见应用服务。
- 本项目没有数据库，故不单设基础设施层。
"""

from .audio import AudioService
from .behavior import BehaviorService
from .camera import CameraService
from .discovery import DiscoveryService
from .escape import EscapeHatchService
from .gateway import GatewayService
from .jobs import JobService
from .led import LedService
from .motion import MotionService
from .movement import MovementService
from .ports import JobQueue, MovementRepository, RobotLocator, RobotPort
from .speech import SpeechService

__all__ = [
    "AudioService",
    "BehaviorService",
    "CameraService",
    "DiscoveryService",
    "EscapeHatchService",
    "GatewayService",
    "JobQueue",
    "JobService",
    "LedService",
    "MotionService",
    "MovementRepository",
    "MovementService",
    "RobotLocator",
    "RobotPort",
    "SpeechService",
]

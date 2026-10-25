"""领域层：纯业务模型，不依赖任何外层。

本层只包含实体、值对象、领域函数与领域错误，
不导入 ``qi``、FastAPI、pydantic 或任何基础设施。
"""

from .entities import (
    Job,
    Movement,
    MovementInfo,
    MovementResult,
    RobotAddress,
    SpeechResult,
)
from .errors import (
    ActionTimeoutError,
    ForbiddenCallError,
    JobNotFoundError,
    MotionBusyError,
    RobotCallError,
    RobotError,
    RobotUnreachableError,
    ServiceUnavailableError,
    UnknownMovementError,
    UnknownPostureError,
    UnknownServiceMethodError,
    ValidationError,
)
from .values import Color, JobStatus

__all__ = [
    "ActionTimeoutError",
    "Color",
    "ForbiddenCallError",
    "Job",
    "JobNotFoundError",
    "JobStatus",
    "MotionBusyError",
    "Movement",
    "MovementInfo",
    "MovementResult",
    "RobotAddress",
    "RobotCallError",
    "RobotError",
    "RobotUnreachableError",
    "ServiceUnavailableError",
    "SpeechResult",
    "UnknownMovementError",
    "UnknownPostureError",
    "UnknownServiceMethodError",
    "ValidationError",
]

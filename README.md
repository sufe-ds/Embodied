# Embodied

NAO 与 TonyPi 机器人具身智能交互项目，提供 **FastAPI REST 服务**与 **MCP 工具**，让 LLM Agent 直接操控机器人。

- NAO：Python 3.12 + [qi](https://github.com/aldebaran/libqi-python)（NAOqi 的 Python 3 官方绑定）。
- TonyPi：调用机器人官方自启动的 JSON-RPC（默认 9030）与 MJPG（默认 8080）服务，不需要 SSH，也不在电脑上安装厂商 SDK。

两种机器人使用各自的能力模型和 MCP 工具集；默认仍为 NAO。

## 环境

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- 依赖：`qi` / `fastapi` / `fastmcp` / `pillow` / `requests` 等

`qi` 在 Linux / macOS 上是官方轮子；Windows 上由 [`libqi-windows`](https://pypi.org/project/libqi-windows/)
提供（导入名同样是 `qi`）。`pyproject.toml` 已按 `sys_platform` 分别声明，三个平台都直接 `uv sync`，不需要 WSL 或容器。

```sh
uv sync
```

## 使用

### NAO

先用发现工具确认 NAO IP（并发探测 9559 与 22 端口，并校验 SSH 标识，不需要先启动服务）：

```sh
uv run embodied discover 192.168.2.0/24
uv run embodied discover --json        # 机器可读输出
```

省略目标时扫描本机网段与 `NAO_IP` 所在网段。退出码 0 表示找到候选，1 表示未找到，2 表示参数或网络错误。双端口开放且 SSH 标识有效不代表设备一定是 NAO；该工具不用于发现 TonyPi。

启动服务（参数优先于环境变量与 `.env`）：

```sh
uv run embodied serve --robot nao --robot-ip 192.168.2.34
uv run embodied serve --robot nao --robot-ip 192.168.2.34 --host 0.0.0.0 --port 19559

# 不传 --robot 时默认也是 NAO：
uv run embodied serve --robot-ip 192.168.2.34
```

### TonyPi

确保 TonyPi 官方 `TonyPi.py` 自启动服务正在运行，并将电脑与机器人连接到同一网络。原生 RPC 默认端口为 9030，视频端口为 8080：

```sh
uv run embodied serve --robot pi --robot-ip 192.168.1.104
# 非默认 RPC 端口时：
uv run embodied serve --robot pi --robot-ip 192.168.1.104 --robot-port 9030
```

TonyPi 官方直连热点通常使用 `192.168.149.1`。Embodied 不启动、停止或重启机器人上的官方服务。

### 本地体验与服务入口

```sh
uv run embodied serve --robot nao --dry-run
uv run embodied serve --robot pi --dry-run
```

`serve` 参数：`--robot {nao,pi}` / `--robot-ip` / `--robot-port` / `--host` / `--port` / `--dry-run` / `--log-level`。
未给出的参数回落到环境变量 / `.env` 或默认值。NAO 默认 `127.0.0.1:9559`；TonyPi 默认 `192.168.149.1:9030`，视频端口 8080；HTTP 服务默认 `0.0.0.0:19559`。

- REST 文档：`http://localhost:19559/docs`
- MCP 端点：`http://localhost:19559/mcp/`

### 连接 MCP（Agent 侧）

支持远程 HTTP 的客户端：

```json
{"mcpServers": {"robot": {"type": "http", "url": "http://localhost:19559/mcp/"}}}
```

只支持 stdio 的客户端，用 `uvx` 桥接（无需安装本项目依赖）：

```json
{"mcpServers": {"robot": {"command": "uvx", "args": ["fastmcp-remote", "http://localhost:19559/mcp/", "--auth", "none"]}}}
```

局域网其他机器访问本机服务时，需要在防火墙放行服务端口（默认 19559）。

## 能力

### NAO MCP 工具

| 分类 | 能力 |
| --- | --- |
| 机器人发现 | `uv run embodied discover`（启动服务前确认 IP） |
| 说话 | `say`（可 `animated`）、`stop` |
| 姿态/运动 | `wake` / `rest` / `list_postures` / `go_to_posture` / `set_angles` / `walk` |
| 动作舞蹈 | `list_dances` / `dance`（异步任务，`get_job` 轮询）/ `cancel_job` |
| 官方行为 | `list_behaviors` / `run_behavior`（ALBehaviorManager，异步任务） |
| 摄像头 | `look`，REST 另提供 JPEG / MJPEG / info |
| 语音识别 | `listen`（给定词表，返回识别词与置信度） |
| LED/音量 | `set_leds` / `leds_off` / `set_volume` / `get_volume` |
| 逃生舱 | `call_service`（默认关闭，调用白名单内任意 NAOqi 服务） |

### TonyPi MCP 工具

| 工具 | 语义 |
| --- | --- |
| `robot_status` / `stop` | 查询状态；停止动作组及由 Embodied 启动的视觉模式 |
| `list_actions` / `run_action` | 常用动作预设；提交动作组，`times=0` 表示循环到停止 |
| `move` | `forward/backward/left/right/turn_left/turn_right`，按动作次数计量 |
| `set_head` | 设置双舵机云台脉宽（500–2500，1500 居中）与运动时长 |
| `look` | 从官方视频服务获取 JPEG，分辨率由机器人决定 |
| `list_vision_modes` | 自动踢球、智能巡线、颜色追踪、人脸识别、AprilTag、智能搬运 |
| `start_vision_mode` / `stop_vision_mode` | 切换视觉模式并维持/停止官方心跳 |

TonyPi 的 `RunAction` 只确认动作线程已提交，没有完成状态查询，因此动作返回 **accepted**，不返回 `job_id`。`list_actions` 是常用官方预设，不是机器人动作文件的实时枚举；`run_action` 也接受机器人上已有的自定义动作名。

Pi 模式不会注册 NAO 的说话、关节弧度、米/弧度移动、LabVIEW 轨迹、NAO 行为、ASR、LED、音量或 NAOqi 逃生舱工具。视觉模式可能引发自主运动，启动前必须确保机器人周围安全，并保留独立急停手段。

## 配置

环境变量或项目根目录 `.env`（模板见 `.env.example`）；命令行参数优先于它们。

- `EMBODIED_ROBOT`：`nao`（默认）或 `pi`。
- `NAO_IP` / `NAO_PORT`：NAOqi 地址与端口。
- `EMBODIED_PI_IP` / `EMBODIED_PI_RPC_PORT` / `EMBODIED_PI_VIDEO_PORT`：TonyPi 原生接口地址。
- `EMBODIED_PI_TIMEOUT_S`：TonyPi RPC/视频 HTTP 超时。
- `EMBODIED_HOST` / `EMBODIED_PORT` / `EMBODIED_MCP_PATH`：服务监听。
- `EMBODIED_DRY_RUN`：不连接机器人，使用所选类型的假实现。
- `EMBODIED_MOVEMENTS_DIR`：NAO 动作模块目录（默认 `Robots/NAO/movements`）。
- `EMBODIED_API_TOKEN`：设置后对 `/api`、`/mcp` 强制 `Authorization: Bearer`。
- `EMBODIED_ENABLE_ESCAPE_HATCH` / `EMBODIED_ALLOWED_SERVICES`：NAOqi 逃生舱开关与白名单。

PowerShell 示例：`$env:EMBODIED_ROBOT = "pi"`，`$env:EMBODIED_PI_IP = "192.168.1.104"`。

## 开发

```sh
uv run pre-commit install
uv run pre-commit run --all-files
uv run ruff check .
uv run ruff format --check .
uv run python -m pytest
uv build
```

测试使用 FakeRobot 或假的 TonyPi RPC/视频传输，不连接真实机器人。

### 架构

领域驱动分层，依赖方向单向：`domain ← service ← api`。

- `domain/` — 纯领域层：实体、值对象、NAO 轨迹展平和领域错误。
- `service/` — 应用服务与抽象端口。`qi_robot.py` 封装 NAOqi；`pi_client.py` / `pi_robot.py` 封装 TonyPi 原生 RPC、MJPG、动作与视觉模式；`fake_robot.py` 和 TonyPi 假传输用于 dry-run 与测试。
- `api/` — `rest.py` 是 NAO 路由，`pi_rest.py` 是 TonyPi 路由；`mcp.py` / `pi_mcp.py` 分别注册两种工具集，不直接接触 `qi`。
- `container.py` / `app.py` — 按 `Settings.robot` 构造 NAO `Container` 或 `PiContainer`，挂载对应路由和 MCP。
- `cli.py` / `discover.py` — `serve` 支持两种机器人；`discover` 仍是独立 NAO 发现命令。

### 开发笔记

- NAO 使用 `qi.Session().connect("tcp://<ip>:9559")`；运动类服务由 `QiRobot._motion_lock` 串行化，`stop_all` 故意绕过锁以便随时急停。
- NAO 动作模块是 LabVIEW 导出数据，由 `domain/trajectory.py` 展平后交给 `ALMotion.angleInterpolationBezier`。
- TonyPi 使用官方 JSON-RPC 的 `RunAction`、`StopActionGroup`、`SetPWMServo` 和视觉模式接口；MJPG 读取器按 JPEG 帧边界缓存最新画面。
- TonyPi 视觉模式由后台线程每 3 秒发送 `Heartbeat`，停止或断开时清理心跳并卸载由 Embodied 启动的模式。
- 新增能力时保持领域层不依赖外层；新抽象放 `service/ports.py`，具体实现放 `service/`，由 `container.py` 注入。

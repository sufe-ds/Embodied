# Embodied 项目指南

## 项目概览

Embodied 是 NAO 与 TonyPi 机器人的具身智能交互项目，使用 Python 3.12，把机器人能力同时暴露为 FastAPI REST 接口和 FastMCP 工具，供 LLM Agent 调用；另有一个独立的 NAO Tkinter 摄像头与手动控制程序。

- NAO 使用 NAOqi 的 Python 3 `qi` 绑定，支持说话、语音识别、姿态与移动、LabVIEW 动作轨迹、官方行为、摄像头、LED、音量、局域网发现和默认关闭的 NAOqi 逃生舱。
- TonyPi 使用官方自启动的 JSON-RPC（默认 9030）与 MJPG（默认 8080），支持离散动作组、方向步态、双舵机云台、摄像头和部分视觉模式。它使用独立容器、REST 路由和 MCP 工具集，不套用 NAO 的关节、behavior 或 job 语义。

项目没有数据库。连接状态、摄像头状态、视觉模式和后台任务均保存在进程内，不跨重启持久化。

## 技术栈与关键配置

- Python `>=3.12`，开发版本固定为 3.12（`.python-version`）。
- 使用 `uv` 管理环境、依赖和构建；依赖锁定在 `uv.lock`。
- `pyproject.toml` 使用 `uv_build` 后端，采用 `src` 包布局，控制台入口为 `embodied = "embodied.cli:main"`。
- Web 栈：FastAPI、Uvicorn、Pydantic 2、pydantic-settings、FastMCP、Requests。
- 机器人与图像：`qi`、Pillow。Linux/macOS 安装 `qi`，Windows 安装提供相同导入名的 `libqi-windows`。
- 测试：pytest、pytest-asyncio、HTTPX。
- `scripts/nao_vision.py` 还使用 Python 标准库 Tkinter，不经过 REST/MCP 服务，仅支持 NAO。
- `.env.example` 是配置模板；`.env` 被 Git 忽略。
- `opencode.json` 和 `.kimi-code/mcp.json` 将 Agent 客户端指向本地 MCP 端点。

仓库中没有 CI 工作流、Docker/Compose、Makefile、类型检查配置或自动化部署脚本。代码检查与格式化使用 Ruff，并通过 pre-commit 在提交前运行。

## 架构与模块划分

依赖方向为 `domain ← service ← api`，组装根可以依赖各层。不要在 API 层直接导入或调用 `qi`，也不要让领域层依赖外层。

```text
src/embodied/
├── domain/             纯领域模型、值对象、轨迹展平和领域错误
├── service/            应用服务、抽象端口及其具体实现
├── api/                NAO/Pi REST 与 MCP、请求模型和 HTTP 错误映射
├── container.py        NAO Container / PiContainer 依赖注入组装根
├── app.py              FastAPI 与 MCP 应用装配、认证和生命周期
├── cli.py              embodied serve / discover 命令入口
└── discover.py         NAO / TonyPi 分组发现命令的参数、输出与退出码
```

### 领域层 `domain/`

- `entities.py`：NAO 动作、行为、语音结果、机器人地址和后台任务实体。
- `values.py`：`Color` 值对象与 `JobStatus` 状态枚举。
- `trajectory.py`：把 LabVIEW 数据展平为 `ALMotion.angleInterpolationBezier` 所需的 `names/times/angles`，是纯函数。
- `errors.py`：带稳定错误码和 HTTP 状态码的 `RobotError` 层次。

### 服务层 `service/`

`ports.py` 定义 `RobotPort`、`TonyPiPort`、`RobotLocator`、`MovementRepository`、`JobQueue` 抽象端口。应用服务只依赖这些端口和领域模型。

- `gateway.py`、`speech.py`、`motion.py`、`movement.py`、`behavior.py`、`camera.py`、`audio.py`、`led.py`：NAO 或公共能力的应用服务。
- `pi.py`：TonyPi 动作、方向移动、云台与视觉模式应用服务。
- `jobs.py`：编排 NAO 动作/行为任务和取消；`thread_jobs.py` 是单后台 worker 的内存队列。TonyPi 原生 RPC 没有完成状态，不使用该队列。
- `escape.py`：执行 NAOqi 逃生舱开关、服务白名单和方法拒绝策略。
- `discovery.py`：整理两种配置网段，并行扫描并生成 `groups.nao` / `groups.tonypi` 与数量；保留扁平 `robots` 兼容旧客户端。`network_scan.py` 的 `SubnetRobotLocator` 探测 NAOqi 9559 与 SSH 22，`TonyPiLocator` 通过 9030 RPC echo 验证 TonyPi，并单独记录 8080 `video_open`。
- `qi_robot.py`：NAO 适配器，封装 `qi.Session`、NAOqi 服务缓存、并发锁、摄像头后台订阅和硬件错误转换。
- `pi_client.py`：TonyPi JSON-RPC 与 MJPG HTTP 客户端，以及 dry-run/测试使用的假 RPC 和假视频传输。Requests Session 禁用环境代理，避免局域网请求被代理截获。
- `pi_robot.py`：TonyPi 适配器，实现原生动作、云台、视觉模式心跳、MJPG 帧缓存和公共生命周期/摄像头端口；NAO 专属 `RobotPort` 方法明确返回不支持，但不注册到 Pi API。
- `fake_robot.py`：NAO dry-run 与测试的内存实现，生成假 JPEG 和确定性响应。
- `movement_files.py`：从受信任目录动态加载 NAO 动作 Python 模块，并按修改时间缓存。
- `config.py`：环境变量、`.env` 和默认值的 Pydantic Settings，包含 `robot: nao|pi` 与两种连接配置。

项目没有单独的基础设施目录；具体实现与抽象端口都放在 `service/`，由 `container.py` 注入。

### 交付层与组装

- `api/rest.py`：NAO FastAPI 路由。长动作默认返回 HTTP 202 和 `job_id`；摄像头提供 JPEG 与 MJPEG。
- `api/pi_rest.py`：TonyPi 路由，只暴露状态、停止、动作、方向移动、云台、视觉模式和摄像头。动作返回 202 `accepted`，不伪造 `job_id`。
- `api/mcp.py`：NAO MCP 工具及类型分派；NAO 长动作通过 `get_job` 查询。
- `api/pi_mcp.py`：TonyPi 专属工具 `robot_status`、`stop`、`list_actions`、`run_action`、`move`、`set_head`、`look`、`list_vision_modes`、`start_vision_mode`、`stop_vision_mode`。不要向 Pi 注册 NAO 工具。
- `api/schemas.py`：REST 请求校验；`api/errors.py` 统一错误响应结构。
- `container.py`：NAO 根据 `dry_run` 选择 `FakeRobot` 或 `QiRobot`；Pi 使用 `TonyPiRobot`，其 dry-run 注入假 RPC/视频传输。分别构造 `Container` 或 `PiContainer`。
- `app.py`：创建 FastAPI，按 `Settings.robot` 挂载对应路由和 MCP，组合两者 lifespan。启动时连接失败不会阻止服务启动，后续端点返回相应机器人错误。
- `cli.py`：`serve` 延迟导入应用并用 Uvicorn 启动；`discover` 不创建 `Container`，可在配置机器人 IP 前运行。

`app.py` 模块底部还有 `app = create_app()`，因此导入该模块就会读取配置并创建应用。

## 安装、运行与构建

```sh
uv sync
uv build                         # 生成 dist/ 下的 sdist 和 wheel
uv run embodied --help
uv run embodied discover 192.168.2.0/24
uv run embodied discover --json
uv run embodied serve --robot nao --robot-ip 192.168.2.34
uv run embodied serve --robot pi --robot-ip 192.168.1.104
uv run embodied serve --robot nao --dry-run
uv run embodied serve --robot pi --dry-run
uv run python scripts/nao_vision.py 192.168.2.34
```

`serve` 支持 `--robot {nao,pi}`、`--robot-ip`、`--robot-port`、`--host`、`--port`、`--dry-run`、`--log-level`。CLI 参数优先于环境变量和 `.env`。不传类型时默认 NAO；`--robot-ip` 只给地址时也会参考 `EMBODIED_ROBOT`。NAO 默认 `127.0.0.1:9559`，TonyPi 默认 `192.168.149.1:9030`、视频 8080，HTTP 服务监听 `0.0.0.0:19559`。

启动后：

- OpenAPI 文档：`http://localhost:19559/docs`
- MCP：`http://localhost:19559/mcp/`
- 健康检查：`/health`

`discover` 支持 `--robot {all,nao,pi}`，默认 `all` 并行扫描两类并分节报告。省略目标时扫描本机 IPv4 `/24`、已配置 NAO 与 TonyPi IP 所在 `/24`。NAO 是端口/SSH 标识候选；TonyPi 必须通过 RPC echo，视频端口只作为附加状态。JSON 推荐读 `groups` 和 `counts`，扁平 `robots` 保留兼容。退出码为 0（任一类型发现）、1（全部未发现）、2（参数或网络错误）。每种扫描最多 4096 个地址，仅支持 IPv4。

NAO 动作数据来自独立的外部目录，不会由 `uv build` 自动打包进 wheel。

## 配置要点

完整模板见 `.env.example`，主要变量如下：

- `EMBODIED_ROBOT`：`nao`（默认）或 `pi`。
- `NAO_IP` / `NAO_PORT`：NAOqi 地址。
- `EMBODIED_PI_IP` / `EMBODIED_PI_RPC_PORT` / `EMBODIED_PI_VIDEO_PORT` / `EMBODIED_PI_TIMEOUT_S`：TonyPi 原生接口与 HTTP 超时。
- `EMBODIED_HOST` / `EMBODIED_PORT` / `EMBODIED_MCP_PATH`：HTTP 服务与 MCP 挂载路径；挂载路径必须以 `/` 开头。
- `EMBODIED_API_TOKEN`：设置后启用 Bearer 认证。
- `EMBODIED_DRY_RUN`：使用所选机器人类型的假实现，不连接硬件。
- `EMBODIED_MOVEMENTS_DIR`：NAO 动作模块目录。
- `EMBODIED_ACTION_TIMEOUT_S`：获取 NAO 机器人调用锁的默认超时。
- `EMBODIED_ASR_LANGUAGE`：NAO 默认说话/识别语言。
- `EMBODIED_CAMERA_FPS` / `EMBODIED_CAMERA_RESOLUTION`：摄像头参数；TonyPi 分辨率由官方视频服务决定，不使用 NAO resolution 编号。
- `EMBODIED_ENABLE_ESCAPE_HATCH` / `EMBODIED_ALLOWED_SERVICES`：NAO 低层调用开关与服务白名单。
- `EMBODIED_LOG_LEVEL`：日志级别。

默认动作目录由 `default_movement_dir()` 计算为 `Path(config.py).resolve().parents[5] / "movements"`。该路径对应项目原始布局中的 `Robots/NAO/movements`，不是当前仓库内的目录。移动或单独检出本项目时，应显式配置 `EMBODIED_MOVEMENTS_DIR`，或恢复兼容的目录布局。

## 开发约定

- 文档、模块 docstring 和面向用户的错误消息主要使用中文；新增内容保持这一语言习惯。
- 普遍使用 `from __future__ import annotations`、类型标注、dataclass 和清晰的依赖注入；不可变领域数据使用 `frozen=True`。
- 使用 Ruff 做格式化与 lint，配置位于 `pyproject.toml`；提交前由 `.pre-commit-config.yaml` 中的本地钩子运行 `uv run ruff check --fix` 和 `uv run ruff format`。开发时先执行 `uv run pre-commit install`，可用 `uv run pre-commit run --all-files` 检查整个仓库。
- 新增能力时，领域模型放入 `domain/`，抽象放入 `service/ports.py`，应用服务和具体实现放入 `service/`，REST/MCP 入口放入 `api/`，最后在 `container.py` 装配。
- 同时维护 REST 与 MCP 的语义和错误行为；REST 请求字段在 `api/schemas.py` 校验，MCP 通过工具签名暴露参数。两种机器人只注册各自真实支持的工具。
- 硬件/外部调用错误应在适配边界转换为 `RobotError` 子类，让 API 错误码保持稳定。
- 版本号同时存在于 `pyproject.toml` 与 `src/embodied/__init__.py`，发布修改时保持同步。
- `outputs/` 是生成图像目录，已被 Git 忽略，不应作为源码或测试夹具依赖。

### 并发与机器人控制约束

- `QiRobot._motion_lock` 串行化 `ALMotion`、`ALRobotPosture`、`ALBehaviorManager`；其他 I/O 使用独立的 `_call_lock`，因此长动作期间仍可说话或拍照。
- NAO 长动作和行为执行期间会一直持有运动锁。`stop_all` 故意绕过运动锁，并尽力调用 `ALMotion.killAll`、`ALTextToSpeech.stopAll`、`ALBehaviorManager.stopAllBehaviors`；修改时不要用运动锁包住急停。
- NAO 后台队列只有一个 worker。任务和状态仅存在内存中，不要按多进程或持久任务队列设计。动作取消不等于物理安全急停。
- TonyPi `RunAction` 只提交动作线程，没有完成状态查询；不要在 API 中声称动作已完成。`stop_all` 调用 `StopActionGroup` 并停止由 Embodied 启动的视觉模式，不卸载所有舵机、不杀远程进程。
- TonyPi 视觉模式由后台线程每 3 秒发送 `Heartbeat`；切换、停止或断开时清理线程并卸载模式。心跳失败目前只停止心跳线程，状态中的 `active_vision_mode` 是本地记录，不等于官方运行状态。
- 两种摄像头都由后台线程缓存最新 JPEG；MJPEG 路由在线程池中读取缓存。TonyPi 流按 JPEG SOI/EOI 边界拆帧。

### SSH 直接调试 NAO

需要绕过 Embodied、直接检查 NAO 时，可使用 `embodied discover` 找到的地址登录。机器人上是 Python 2 环境，执行 `qi` 脚本前必须加载 NAOqi 环境：

```sh
ssh nao@<NAO_IP>
source /etc/profile
python2
```

```python
import qi

session = qi.Session()
session.connect("tcp://127.0.0.1:9559")
behaviors = session.service("ALBehaviorManager")
print(behaviors.getRunningBehaviors())
print(behaviors.getDefaultBehaviors())
```

- 用 `ps -u nao -o pid,ppid,args` 检查进程，用 `systemctl --user list-units --type=service --all` 检查 NAOqi 用户级服务，用 `journalctl -n 1000 --no-pager` 检查运行日志。
- 行为包通常位于 `/home/nao/.local/share/PackageManager/apps/`。仅看进程和 `getRunningBehaviors()` 不足以判断代码已停止，还要检查行为脚本中的后台线程、回调和感知订阅；例如 `ALFaceDetection`、`ALSpeechRecognition` 可通过 `getSubscribersInfo()` 检查订阅者。
- `stopBehavior()` 只保证行为退出运行列表。Choregraphe/Python 行为若通过 `thread.start_new_thread` 等方式启动后台线程，且 `onStop` 没有清理逻辑，线程和订阅可能继续运行，仍会响应人脸、语音等事件。遇到这种残留时，需要重启 NAOqi 才能彻底清除：

```sh
systemctl --user restart naoqi.service
```

- 重启 NAOqi 会中断当前机器人动作和客户端调用，执行前应取得用户同意并确保机器人周围安全；SSH 服务本身不需要重启。重启后必须复核运行/默认行为、`ALAutonomousLife.getState()`、感知订阅和相关监听端口，因为自主生活状态和默认行为可能在启动过程中恢复或变化。

## 测试策略

```sh
uv run pytest
# 若 Windows/非 ASCII 路径使 uv 无法定位 pytest 启动脚本：
uv run python -m pytest
```

`pyproject.toml` 设置 `testpaths = ["tests"]` 和 `asyncio_mode = "auto"`，异步测试无需逐个添加 asyncio 标记。

现有测试完全不需要真实机器人：

- NAO REST 测试用 `create_app(Settings(dry_run=True))`、HTTPX `ASGITransport` 和 `FakeRobot`，不启动真实服务器。
- TonyPi 测试使用 `Settings(robot="pi", dry_run=True)`、`FakeTonyPiRpc` / `FakeTonyPiVideo` 或内存 multipart 流，覆盖 RPC、动作、视觉模式、JPEG/MJPEG、REST 和 MCP 工具清单。
- 动作仓储与 REST 测试通过 `tests/conftest.py` 的 `movement_dir` fixture 在 pytest 临时目录生成最小动作模块，不依赖外部动作数据。
- API 测试构造 `Settings` 时禁用 `.env` 并显式固定认证和安全开关；共享测试配置还会在应用导入前固定 MCP 路径，避免本机配置改变测试前提。
- 行为与锁测试通过 `QiRobot`/`FakeRobot` 子类、事件和调用记录验证轮询、取消与急停。
- 发现测试使用 `RobotLocator` 桩和 socket mock，不访问真实网络。
- 摄像头测试直接覆盖后台流对象的锁回归。
- 新测试应沿用这些隔离方式，不要在单元/集成测试中连接真实机器人或扫描真实局域网。

## 安全与部署注意事项

- 默认绑定 `0.0.0.0`，且未设置令牌时没有认证。局域网或不可信网络中应设置强随机 `EMBODIED_API_TOKEN`，并只向受信客户端开放端口。
- 当前认证中间件保护除 `/health` 和 OPTIONS 请求之外的应用路由，包括 REST、MCP 和文档；客户端使用 `Authorization: Bearer <token>`。
- Embodied 的令牌只保护自己的 HTTP 服务，不会保护 TonyPi 原生 9030/8080。官方 RPC 能直接控制硬件且没有本项目认证，应通过防火墙仅向受信主机开放，不能暴露到不可信网络。
- `.env` 可能包含令牌和内部地址，已被忽略；不要提交真实凭据，只更新 `.env.example` 模板。
- NAO 逃生舱默认关闭。开启后仍受服务白名单和方法拒绝规则约束；白名单支持 `"*"`，但应避免在不可信环境使用。
- NAO 动作仓储通过 `importlib` 执行目录中的 Python 文件。动作名正则只防止路径穿越，不能使文件内容变得安全；动作目录必须只包含受信任代码，且不可让未授权用户写入。
- NAO 运动接口使用米、弧度和关节角；TonyPi 使用离散动作组、步态次数和舵机脉宽。两者都会造成真实硬件动作，视觉模式还可能自主运动。真实机器人验证前先使用 dry-run，确保周围空间安全，并保留独立的物理急停手段。
- 网络发现会主动连接目标地址的 9559、22、9030、8080，并向 9030 发送只读 RPC echo；只扫描有权测试的网络。
- 生产运行方式是由 `embodied serve` 启动 Uvicorn；仓库没有进程守护、TLS 终止、反向代理或多 worker 部署方案。远程访问还需自行配置防火墙，MCP HTTP 客户端连接 `/mcp/`，stdio 客户端可按 README 使用 `fastmcp-remote` 桥接。

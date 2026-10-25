# Embodied 项目指南

## 项目概览

Embodied 是 NAO 机器人的具身智能交互项目，使用 Python 3.12 和 NAOqi 的 Python 3 `qi` 绑定。项目把机器人能力同时暴露为 FastAPI REST 接口和 FastMCP 工具，供 LLM Agent 调用；另有一个独立的 Tkinter 摄像头与手动控制程序。

主要能力包括说话、语音识别、姿态与移动、LabVIEW 导出的动作轨迹、NAO 官方行为、摄像头 JPEG/MJPEG、LED、音量、局域网发现，以及默认关闭的低层 NAOqi 调用逃生舱。

项目没有数据库。连接状态、摄像头状态和后台任务均保存在进程内，不跨重启持久化。

## 技术栈与关键配置

- Python `>=3.12`，开发版本固定为 3.12（`.python-version`）。
- 使用 `uv` 管理环境、依赖和构建；依赖锁定在 `uv.lock`。
- `pyproject.toml` 使用 `uv_build` 后端，采用 `src` 包布局，控制台入口为 `embodied = "embodied.cli:main"`。
- Web 栈：FastAPI、Uvicorn、Pydantic 2、pydantic-settings、FastMCP。
- 机器人与图像：`qi`、Pillow。Linux/macOS 安装 `qi`，Windows 安装提供相同导入名的 `libqi-windows`。
- 测试：pytest、pytest-asyncio、HTTPX。
- `scripts/nao_vision.py` 还使用 Python 标准库 Tkinter，不经过 REST/MCP 服务。
- `.env.example` 是配置模板；`.env` 被 Git 忽略。
- `opencode.json` 和 `.kimi-code/mcp.json` 将 Agent 客户端指向本地 MCP 端点。

仓库中没有 CI 工作流、Docker/Compose、Makefile、独立的代码检查或类型检查配置，也没有自动化部署脚本。

## 架构与模块划分

依赖方向为 `domain ← service ← api`，组装根可以依赖各层。不要在 API 层直接导入或调用 `qi`，也不要让领域层依赖外层。

```text
src/embodied/
├── domain/             纯领域模型、值对象、轨迹展平和领域错误
├── service/            应用服务、抽象端口及其具体实现
├── api/                REST、MCP、请求模型和 HTTP 错误映射
├── container.py        依赖注入组装根
├── app.py              FastAPI 与 MCP 应用装配、认证和生命周期
├── cli.py              embodied serve / discover 命令入口
└── discover.py         独立发现命令的参数、输出与退出码
```

### 领域层 `domain/`

- `entities.py`：动作、行为、语音结果、机器人地址和后台任务实体。
- `values.py`：`Color` 值对象与 `JobStatus` 状态枚举。
- `trajectory.py`：把 LabVIEW 数据展平为 `ALMotion.angleInterpolationBezier` 所需的 `names/times/angles`，是纯函数。
- `errors.py`：带稳定错误码和 HTTP 状态码的 `RobotError` 层次。

### 服务层 `service/`

`ports.py` 定义 `RobotPort`、`RobotLocator`、`MovementRepository`、`JobQueue` 四个抽象端口。应用服务只依赖这些端口和领域模型。

- `gateway.py`、`speech.py`、`motion.py`、`movement.py`、`behavior.py`、`camera.py`、`audio.py`、`led.py`：按能力划分的应用服务。
- `jobs.py`：编排动作/行为任务和取消；`thread_jobs.py` 是单后台 worker 的内存队列。
- `escape.py`：执行逃生舱开关、服务白名单和方法拒绝策略。
- `discovery.py`：整理扫描网段并标记当前配置地址；`network_scan.py` 并发探测 NAOqi 9559 与 SSH 22 端口。
- `qi_robot.py`：真实机器人适配器，封装 `qi.Session`、NAOqi 服务缓存、并发锁、摄像头后台订阅和硬件错误转换。
- `fake_robot.py`：供 dry-run 与测试使用的内存实现，生成假 JPEG 和确定性响应。
- `movement_files.py`：从受信任目录动态加载动作 Python 模块，并按修改时间缓存。
- `config.py`：环境变量、`.env` 和默认值的 Pydantic Settings。

项目没有单独的基础设施目录；具体实现与抽象端口都放在 `service/`，由 `container.py` 注入。

### 交付层与组装

- `api/rest.py`：FastAPI 路由。长动作默认返回 HTTP 202 和 `job_id`；摄像头提供 JPEG 与 MJPEG。
- `api/mcp.py`：与 REST 共用应用服务的 MCP 工具；长动作默认异步提交，通过 `get_job` 查询。
- `api/schemas.py`：REST 请求校验；`api/errors.py` 统一错误响应结构。
- `container.py`：根据 `dry_run` 选择 `FakeRobot` 或 `QiRobot`，并构造所有服务。
- `app.py`：创建 FastAPI，挂载 MCP，组合两者 lifespan。启动时连接失败不会阻止服务启动，后续端点会返回相应机器人错误。
- `cli.py`：`serve` 延迟导入应用并用 Uvicorn 启动；`discover` 不创建 `Container`，可在配置机器人 IP 前运行。

`app.py` 模块底部还有 `app = create_app()`，因此导入该模块就会读取配置并创建应用。

## 安装、运行与构建

```sh
uv sync
uv build                         # 生成 dist/ 下的 sdist 和 wheel
uv run embodied --help
uv run embodied discover 192.168.2.0/24
uv run embodied discover --json
uv run embodied serve --robot-ip 192.168.2.34
uv run embodied serve --dry-run
uv run python scripts/nao_vision.py 192.168.2.34
```

`serve` 支持 `--robot-ip`、`--robot-port`、`--host`、`--port`、`--dry-run`、`--log-level`。CLI 参数优先于环境变量和 `.env`。默认机器人地址为 `127.0.0.1:9559`，服务监听 `0.0.0.0:19559`。

启动后：

- OpenAPI 文档：`http://localhost:19559/docs`
- MCP：`http://localhost:19559/mcp/`
- 健康检查：`/health`

`discover` 省略目标时扫描本机 IPv4 的 `/24` 网段和已配置 NAO IP 所在 `/24`。它返回的是候选地址，不是严格的 NAO 身份认证。退出码为 0（发现候选）、1（未发现）、2（参数或网络错误）。扫描最多 4096 个地址，仅支持 IPv4。

动作数据来自独立的外部目录，不会由 `uv build` 自动打包进 wheel。

## 配置要点

完整模板见 `.env.example`，主要变量如下：

- `NAO_IP` / `NAO_PORT`：NAOqi 地址。
- `EMBODIED_HOST` / `EMBODIED_PORT` / `EMBODIED_MCP_PATH`：HTTP 服务与 MCP 挂载路径；挂载路径必须以 `/` 开头。
- `EMBODIED_API_TOKEN`：设置后启用 Bearer 认证。
- `EMBODIED_DRY_RUN`：使用 `FakeRobot`，不连接硬件。
- `EMBODIED_MOVEMENTS_DIR`：动作模块目录。
- `EMBODIED_ACTION_TIMEOUT_S`：获取机器人调用锁的默认超时。
- `EMBODIED_ASR_LANGUAGE`：默认说话/识别语言。
- `EMBODIED_CAMERA_FPS` / `EMBODIED_CAMERA_RESOLUTION`：摄像头参数。
- `EMBODIED_ENABLE_ESCAPE_HATCH` / `EMBODIED_ALLOWED_SERVICES`：低层调用开关与服务白名单。
- `EMBODIED_LOG_LEVEL`：日志级别。

默认动作目录由 `default_movement_dir()` 计算为 `Path(config.py).resolve().parents[5] / "movements"`。该路径对应项目原始布局中的 `Robots/NAO/movements`，不是当前仓库内的目录。移动或单独检出本项目时，应显式配置 `EMBODIED_MOVEMENTS_DIR`，或恢复兼容的目录布局。

## 开发约定

- 文档、模块 docstring 和面向用户的错误消息主要使用中文；新增内容保持这一语言习惯。
- 普遍使用 `from __future__ import annotations`、类型标注、dataclass 和清晰的依赖注入；不可变领域数据使用 `frozen=True`。
- 没有配置强制格式化器或 linter。修改应匹配现有命名、导入顺序和格式，不要假设存在 Ruff、Black、mypy 等检查命令。
- 新增能力时，领域模型放入 `domain/`，抽象放入 `service/ports.py`，应用服务和具体实现放入 `service/`，REST/MCP 入口放入 `api/`，最后在 `container.py` 装配。
- 同时维护 REST 与 MCP 的语义和错误行为；REST 请求字段在 `api/schemas.py` 校验，MCP 通过工具签名暴露参数。
- 硬件/外部调用错误应在适配边界转换为 `RobotError` 子类，让 API 错误码保持稳定。
- 版本号同时存在于 `pyproject.toml` 与 `src/embodied/__init__.py`，发布修改时保持同步。
- `outputs/` 是生成图像目录，已被 Git 忽略，不应作为源码或测试夹具依赖。

### 并发与机器人控制约束

- `QiRobot._motion_lock` 串行化 `ALMotion`、`ALRobotPosture`、`ALBehaviorManager`；其他 I/O 使用独立的 `_call_lock`，因此长动作期间仍可说话或拍照。
- 长动作和行为执行期间会一直持有运动锁。`stop_all` 故意绕过运动锁，并尽力调用 `ALMotion.killAll`、`ALTextToSpeech.stopAll`、`ALBehaviorManager.stopAllBehaviors`；修改时不要用运动锁包住急停。
- 后台队列只有一个 worker。任务和状态仅存在内存中，不要按多进程或持久任务队列设计。
- 动作取消会标记任务并调用急停，但底层线程/硬件调用的停止是尽力而为，不等于物理安全急停。
- 摄像头由后台线程持续订阅并缓存最新 JPEG；MJPEG 路由在线程池中读取缓存。

## 测试策略

```sh
uv run pytest
# 若 Windows/非 ASCII 路径使 uv 无法定位 pytest 启动脚本：
uv run python -m pytest
```

`pyproject.toml` 设置 `testpaths = ["tests"]` 和 `asyncio_mode = "auto"`，异步测试无需逐个添加 asyncio 标记。

现有测试完全不需要真实 NAO：

- REST 测试用 `create_app(Settings(dry_run=True))`、HTTPX `ASGITransport` 和 `FakeRobot`，不启动真实服务器。
- 动作仓储与 REST 测试通过 `tests/conftest.py` 的 `movement_dir` fixture 在 pytest 临时目录生成最小动作模块，不依赖外部动作数据。
- API 测试构造 `Settings` 时禁用 `.env` 并显式固定认证和安全开关；共享测试配置还会在应用导入前固定 MCP 路径，避免本机配置改变测试前提。
- 行为与锁测试通过 `QiRobot`/`FakeRobot` 子类、事件和调用记录验证轮询、取消与急停。
- 发现测试使用 `RobotLocator` 桩和 socket mock，不访问真实网络。
- 摄像头测试直接覆盖后台流对象的锁回归。
- 新测试应沿用这些隔离方式，不要在单元/集成测试中连接真实机器人或扫描真实局域网。

## 安全与部署注意事项

- 默认绑定 `0.0.0.0`，且未设置令牌时没有认证。局域网或不可信网络中应设置强随机 `EMBODIED_API_TOKEN`，并只向受信客户端开放端口。
- 当前认证中间件保护除 `/health` 和 OPTIONS 请求之外的应用路由，包括 REST、MCP 和文档；客户端使用 `Authorization: Bearer <token>`。
- `.env` 可能包含令牌和内部地址，已被忽略；不要提交真实凭据，只更新 `.env.example` 模板。
- 逃生舱默认关闭。开启后仍受服务白名单和方法拒绝规则约束；白名单支持 `"*"`，但应避免在不可信环境使用。
- 动作仓储通过 `importlib` 执行目录中的 Python 文件。动作名正则只防止路径穿越，不能使文件内容变得安全；动作目录必须只包含受信任代码，且不可让未授权用户写入。
- 运动接口使用米、弧度和关节角，会造成真实硬件动作。真实机器人验证前先使用 dry-run，确保周围空间安全，并保留独立的物理急停手段。
- 网络发现会主动连接目标地址的 9559 和 22 端口，只扫描有权测试的网络。
- 生产运行方式是由 `embodied serve` 启动 Uvicorn；仓库没有进程守护、TLS 终止、反向代理或多 worker 部署方案。远程访问还需自行配置防火墙，MCP HTTP 客户端连接 `/mcp/`，stdio 客户端可按 README 使用 `fastmcp-remote` 桥接。

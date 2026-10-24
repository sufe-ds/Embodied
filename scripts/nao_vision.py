"""连接 NAO，显示摄像头画面，并手动控制头部、姿势与仿生动作。

用法：uv run python scripts/nao_vision.py [IP]

未给出 IP 时预填 ``NAO_IP``；画面分辨率与帧率沿用 ``EMBODIED_CAMERA_RESOLUTION`` /
``EMBODIED_CAMERA_FPS`` 的设置。
"""

from __future__ import annotations

import io
import math
import queue
import sys
import threading
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from PIL import Image, ImageTk

from embodied.service.config import Settings
from embodied.service.qi_robot import QiRobot

FRAME_INTERVAL_MS = 60
PUMP_INTERVAL_MS = 30

HEAD_YAW_LIMIT = 2.08
HEAD_PITCH_MIN = -0.67
HEAD_PITCH_MAX = 0.51

POSTURES = {"站": "Stand", "坐": "Sit", "蹲": "Crouch"}

ZOOM_MIN = 1.0
ZOOM_MAX = 4.0


class VisionWindow:
    def __init__(self, root: tk.Tk, initial_ip: str) -> None:
        self.root = root
        self.robot: QiRobot | None = None
        self._connecting = False
        self._closing = False
        self._after_id: str | None = None
        self._pump_id: str | None = None
        self._ui_queue: queue.Queue[tuple[Callable[..., None], tuple]] = queue.Queue()
        self._last_frame: bytes | None = None
        self._photo: ImageTk.PhotoImage | None = None
        self._zoom = ZOOM_MIN

        self._bionic = False
        self._bionic_busy = False
        self._posture_busy = False
        self._tracking = False
        self._tracking_busy = False

        self._epoch = 0
        self._head_target: tuple[float, float] | None = None
        self._head_lock = threading.Lock()
        self._head_wake = threading.Event()
        self._head_stop = threading.Event()
        self._head_yaw = 0.0
        self._head_pitch = 0.0
        self._syncing_head_scales = False

        root.title("NAO 视觉")
        root.geometry("720x620")
        root.minsize(480, 400)

        bar = ttk.Frame(root, padding=(12, 10))
        bar.pack(fill="x")
        ttk.Label(bar, text="机器人 IP").pack(side="left")
        self.ip_var = tk.StringVar(value=initial_ip)
        self.entry = ttk.Entry(bar, textvariable=self.ip_var, width=20)
        self.entry.pack(side="left", padx=8)
        self.entry.bind("<Return>", lambda _event: self.toggle())
        self.button = ttk.Button(bar, text="连接", command=self.toggle)
        self.button.pack(side="left")
        self.status = ttk.Label(bar, text="未连接", foreground="#666666")
        self.status.pack(side="left", padx=(12, 0))

        controls = ttk.Frame(root, padding=(12, 0, 12, 8))
        controls.pack(fill="x")
        self.bionic_button = ttk.Button(
            controls, text="仿生：关", command=self._toggle_bionic
        )
        self.bionic_button.pack(side="left")
        self.tracking_button = ttk.Button(
            controls, text="面部追踪：关", command=self._toggle_tracking
        )
        self.tracking_button.pack(side="left", padx=(6, 0))

        ttk.Separator(controls, orient="vertical").pack(
            side="left", fill="y", padx=10
        )

        ttk.Label(controls, text="姿势").pack(side="left")
        self.posture_var = tk.StringVar()
        self.posture_box = ttk.Combobox(
            controls,
            textvariable=self.posture_var,
            values=list(POSTURES),
            width=4,
            state="readonly",
        )
        self.posture_box.pack(side="left", padx=(6, 0))
        self.posture_box.bind("<<ComboboxSelected>>", self._posture_changed)

        ttk.Separator(controls, orient="vertical").pack(
            side="left", fill="y", padx=10
        )

        ttk.Label(controls, text="放缩").pack(side="left")
        self.zoom_scale = tk.Scale(
            controls,
            from_=ZOOM_MIN,
            to=ZOOM_MAX,
            resolution=0.1,
            orient="horizontal",
            length=110,
            showvalue=False,
            sliderlength=16,
            highlightthickness=0,
            command=self._on_zoom,
        )
        self.zoom_scale.pack(side="left", padx=(6, 4))
        self.zoom_label = ttk.Label(controls, text=f"{self._zoom:.1f}x", width=4)
        self.zoom_label.pack(side="left")

        head_controls = ttk.Frame(root, padding=(12, 0, 12, 8))
        head_controls.pack(fill="x")
        ttk.Label(head_controls, text="头部旋转 (°)").pack(side="left")
        self.head_yaw_scale = tk.Scale(
            head_controls,
            from_=math.degrees(HEAD_YAW_LIMIT),
            to=-math.degrees(HEAD_YAW_LIMIT),
            resolution=0.1,
            orient="horizontal",
            length=220,
            sliderlength=16,
            highlightthickness=0,
            command=self._on_head_yaw,
        )
        self.head_yaw_scale.pack(side="left", padx=(6, 16))
        ttk.Label(head_controls, text="头部俯仰 (°)").pack(side="left")
        self.head_pitch_scale = tk.Scale(
            head_controls,
            from_=math.degrees(HEAD_PITCH_MAX),
            to=math.degrees(HEAD_PITCH_MIN),
            resolution=0.1,
            orient="horizontal",
            length=180,
            sliderlength=16,
            highlightthickness=0,
            command=self._on_head_pitch,
        )
        self.head_pitch_scale.pack(side="left", padx=(6, 0))

        self.view = tk.Label(
            root, bg="#111111", fg="#aaaaaa", text="连接后显示摄像头画面"
        )
        self.view.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self._set_controls_enabled(False)
        self._pump_id = root.after(PUMP_INTERVAL_MS, self._pump)

    # -- 连接 / 断开 --------------------------------------------------------
    def toggle(self) -> None:
        if self._connecting:
            return
        if self.robot is not None:
            self._disconnect()
            return
        ip = self.ip_var.get().strip()
        if not ip:
            self._set_status("请输入机器人 IP", error=True)
            return
        self._connecting = True
        self.button.state(["disabled"])
        self.entry.state(["disabled"])
        self._set_status(f"正在连接 {ip} ...")
        robot = QiRobot(Settings(nao_ip=ip))
        threading.Thread(
            target=self._connect_worker, args=(robot, ip), daemon=True
        ).start()

    def _connect_worker(self, robot: QiRobot, ip: str) -> None:
        try:
            robot.connect()
        except Exception as exc:  # noqa: BLE001 - 失败原因直接展示给用户
            self._ui(self._connect_failed, robot, exc)
            return
        try:
            bionic = bool(
                robot.call_service("ALMotion", "getBreathEnabled", ["Body"])
            )
        except Exception:  # 旧版本 NAOqi 没有呼吸接口，按关闭显示
            bionic = False
        try:
            angles = robot.call_service(
                "ALMotion", "getAngles", [["HeadYaw", "HeadPitch"], True]
            )
            head = (float(angles[0]), float(angles[1]))
        except Exception:  # noqa: BLE001 - 读不到就以中位起步
            head = (0.0, 0.0)
        try:
            robot.start_camera()
        except Exception as exc:  # noqa: BLE001 - 失败原因直接展示给用户
            self._ui(self._connect_failed, robot, exc)
            return
        self._ui(self._connect_done, robot, ip, bionic, head)

    def _connect_done(
        self,
        robot: QiRobot,
        ip: str,
        bionic: bool,
        head: tuple[float, float],
    ) -> None:
        self._connecting = False
        self.robot = robot
        self._bionic = bionic
        self._bionic_busy = False
        self._posture_busy = False
        self._tracking = False
        self._tracking_busy = False
        self.bionic_button.configure(text=f"仿生：{'开' if bionic else '关'}")
        self.tracking_button.configure(text="面部追踪：关")
        self._head_yaw, self._head_pitch = head
        self._sync_head_scales()
        self._head_stop = threading.Event()
        self._head_target = None
        self._epoch += 1
        threading.Thread(
            target=self._head_worker, args=(self._epoch,), daemon=True
        ).start()
        self.button.configure(text="断开")
        self._enable_inputs()
        self._set_controls_enabled(True)
        self._set_status(f"已连接 {ip}")
        self.view.configure(text="")
        self._render_next()

    def _connect_failed(self, robot: QiRobot, exc: Exception) -> None:
        self._connecting = False
        self._enable_inputs()
        self._set_status(f"连接失败：{exc}", error=True)
        threading.Thread(target=robot.disconnect, daemon=True).start()

    def _disconnect(self) -> None:
        robot, self.robot = self.robot, None
        self._epoch += 1
        self._head_stop.set()
        self._head_wake.set()
        with self._head_lock:
            self._head_target = None
        if self._after_id is not None:
            self.root.after_cancel(self._after_id)
            self._after_id = None
        self._last_frame = None
        self._photo = None
        self._bionic = False
        self._bionic_busy = False
        self._posture_busy = False
        self._tracking = False
        self._tracking_busy = False
        self.bionic_button.configure(text="仿生：关")
        self.tracking_button.configure(text="面部追踪：关")
        self.posture_var.set("")
        self.view.configure(image="", text="已断开")
        self.button.state(["disabled"])
        self._set_controls_enabled(False)
        self._set_status("正在断开 ...")
        threading.Thread(
            target=self._disconnect_worker, args=(robot,), daemon=True
        ).start()

    def _disconnect_worker(self, robot: QiRobot | None) -> None:
        try:
            if robot is not None:
                self._teardown(robot)
        finally:
            self._ui(self._disconnect_done)

    def _teardown(self, robot: QiRobot) -> None:
        # 断开前总要停追踪：追踪可能刚被打开，本地状态还没跟上
        self._stop_tracking(robot)
        robot.disconnect()

    def _disconnect_done(self) -> None:
        self.button.configure(text="连接")
        self._enable_inputs()
        self._set_status("未连接")

    # -- 仿生 / 面部追踪 / 姿势 / 头部 / 放缩 --------------------------------
    def _toggle_bionic(self) -> None:
        robot = self.robot
        if robot is None or self._bionic_busy:
            return
        enabled = not self._bionic
        self._bionic_busy = True
        self.bionic_button.state(["disabled"])
        self._set_status("正在开启仿生 ..." if enabled else "正在关闭仿生 ...")
        threading.Thread(
            target=self._bionic_worker, args=(robot, enabled), daemon=True
        ).start()

    def _bionic_worker(self, robot: QiRobot, enabled: bool) -> None:
        try:
            robot.call_service("ALMotion", "setBreathEnabled", ["Body", enabled])
            robot.call_service(
                "ALMotion", "setIdlePostureEnabled", ["Body", enabled]
            )
        except Exception as exc:  # noqa: BLE001 - 失败原因直接展示给用户
            self._ui(self._bionic_failed, robot, exc)
            return
        self._ui(self._bionic_done, robot, enabled)

    def _bionic_done(self, robot: QiRobot, enabled: bool) -> None:
        if self.robot is not robot:
            return
        self._bionic = enabled
        self._bionic_busy = False
        self.bionic_button.configure(text=f"仿生：{'开' if enabled else '关'}")
        self.bionic_button.state(["!disabled"])
        self._set_status("仿生已开启" if enabled else "仿生已关闭")

    def _bionic_failed(self, robot: QiRobot, exc: Exception) -> None:
        if self.robot is not robot:
            return
        self._bionic_busy = False
        self.bionic_button.state(["!disabled"])
        self._set_status(f"切换仿生失败：{exc}", error=True)

    def _toggle_tracking(self) -> None:
        robot = self.robot
        if robot is None or self._tracking_busy:
            return
        enabled = not self._tracking
        self._tracking_busy = True
        self.tracking_button.state(["disabled"])
        self._set_status("正在开启面部追踪 ..." if enabled else "正在关闭面部追踪 ...")
        threading.Thread(
            target=self._tracking_worker, args=(robot, enabled), daemon=True
        ).start()

    def _tracking_worker(self, robot: QiRobot, enabled: bool) -> None:
        if enabled:
            try:
                # 与机器人内置面部追踪程序相同的配置：仅头部跟随，目标尺寸 15 cm
                robot.call_service("ALTracker", "registerTarget", ["Face", 0.15])
                robot.call_service("ALTracker", "setMode", ["Head"])
                robot.call_service("ALTracker", "setExtractorPeriod", ["Face", 5])
                robot.call_service("ALTracker", "track", ["Face"])
            except Exception as exc:  # noqa: BLE001 - 失败原因直接展示给用户
                self._stop_tracking(robot)
                self._ui(self._tracking_failed, robot, exc)
                return
            self._ui(self._tracking_done, robot, True, None)
            return
        self._stop_tracking(robot)
        self._ui(self._tracking_done, robot, False, self._read_head(robot))

    def _tracking_done(
        self,
        robot: QiRobot,
        enabled: bool,
        head: tuple[float, float] | None,
    ) -> None:
        if self.robot is not robot:
            return
        self._tracking = enabled
        self._tracking_busy = False
        self.tracking_button.configure(
            text=f"面部追踪：{'开' if enabled else '关'}"
        )
        self.tracking_button.state(["!disabled"])
        if head is not None:
            self._head_yaw, self._head_pitch = head
        self._sync_head_scales()
        if enabled:
            with self._head_lock:
                self._head_target = None
        self._set_head_enabled(not enabled)
        self._set_status("面部追踪已开启" if enabled else "面部追踪已关闭")

    def _tracking_failed(self, robot: QiRobot, exc: Exception) -> None:
        if self.robot is not robot:
            return
        self._tracking = False
        self._tracking_busy = False
        self.tracking_button.configure(text="面部追踪：关")
        self.tracking_button.state(["!disabled"])
        self._set_head_enabled(True)
        self._set_status(f"切换面部追踪失败：{exc}", error=True)

    @staticmethod
    def _stop_tracking(robot: QiRobot) -> None:
        for method in ("stopTracker", "unregisterAllTargets"):
            try:
                robot.call_service("ALTracker", method, [])
            except Exception:  # noqa: BLE001 - 关闭追踪尽力而为
                pass

    @staticmethod
    def _read_head(robot: QiRobot) -> tuple[float, float] | None:
        try:
            angles = robot.call_service(
                "ALMotion", "getAngles", [["HeadYaw", "HeadPitch"], True]
            )
            return (float(angles[0]), float(angles[1]))
        except Exception:  # noqa: BLE001 - 读不到就沿用旧值
            return None

    def _posture_changed(self, _event: object = None) -> None:
        robot = self.robot
        label = self.posture_var.get()
        if robot is None or self._posture_busy or label not in POSTURES:
            return
        self._posture_busy = True
        self.posture_box.state(["disabled"])
        self._set_status(f"正在切换到{label} ...")
        threading.Thread(
            target=self._posture_worker,
            args=(robot, label, POSTURES[label]),
            daemon=True,
        ).start()

    def _posture_worker(self, robot: QiRobot, label: str, name: str) -> None:
        try:
            robot.go_to_posture(name, 0.5)
        except Exception as exc:  # noqa: BLE001 - 失败原因直接展示给用户
            self._ui(self._posture_failed, robot, exc)
            return
        self._ui(self._posture_done, robot, label)

    def _posture_done(self, robot: QiRobot, label: str) -> None:
        if self.robot is not robot:
            return
        self._posture_busy = False
        self.posture_box.state(["!disabled"])
        self._set_status(f"姿势：{label}")

    def _posture_failed(self, robot: QiRobot, exc: Exception) -> None:
        if self.robot is not robot:
            return
        self._posture_busy = False
        self.posture_box.state(["!disabled"])
        self._set_status(f"切换姿势失败：{exc}", error=True)

    def _on_head_yaw(self, value: str) -> None:
        self._head_yaw = math.radians(float(value))
        self._move_head()

    def _on_head_pitch(self, value: str) -> None:
        self._head_pitch = math.radians(float(value))
        self._move_head()

    def _move_head(self) -> None:
        if self.robot is None or self._tracking or self._syncing_head_scales:
            return
        with self._head_lock:
            self._head_target = (self._head_yaw, self._head_pitch)
        self._head_wake.set()

    def _sync_head_scales(self) -> None:
        scales = (self.head_yaw_scale, self.head_pitch_scale)
        states = [scale.cget("state") for scale in scales]
        self._syncing_head_scales = True
        try:
            for scale in scales:
                scale.configure(state="normal")
            self.head_yaw_scale.set(math.degrees(self._head_yaw))
            self.head_pitch_scale.set(math.degrees(self._head_pitch))
        finally:
            for scale, state in zip(scales, states, strict=True):
                scale.configure(state=state)
            self._syncing_head_scales = False

    def _head_worker(self, epoch: int) -> None:
        while epoch == self._epoch and not self._head_stop.is_set():
            with self._head_lock:
                target, self._head_target = self._head_target, None
            if target is None:
                self._head_wake.wait(0.2)
                self._head_wake.clear()
                continue
            robot = self.robot
            if robot is None:
                return
            try:
                # 不用 QiRobot.set_angles：那会先 wakeUp，坐着时点击视角会把机器人拉起来
                robot.call_service(
                    "ALMotion",
                    "setAngles",
                    [["HeadYaw", "HeadPitch"], [target[0], target[1]], 0.25],
                )
            except Exception as exc:  # noqa: BLE001 - 失败原因直接展示给用户
                self._ui(self._show_error, f"转动头部失败：{exc}")

    def _on_zoom(self, value: str) -> None:
        self._zoom = max(ZOOM_MIN, min(ZOOM_MAX, float(value)))
        self.zoom_label.configure(text=f"{self._zoom:.1f}x")
        if self._last_frame is not None:
            self._show(self._last_frame)

    # -- 画面 ---------------------------------------------------------------
    def _render_next(self) -> None:
        self._after_id = self.root.after(FRAME_INTERVAL_MS, self._render_frame)

    def _render_frame(self) -> None:
        self._after_id = None
        robot = self.robot
        if robot is None:
            return
        frame = robot.camera_frame()
        if frame is not None and frame is not self._last_frame:
            self._last_frame = frame
            self._show(frame)
        self._render_next()

    def _show(self, frame: bytes) -> None:
        image = Image.open(io.BytesIO(frame)).convert("RGB")
        width, height = image.size
        if self._zoom > ZOOM_MIN:
            crop_w, crop_h = int(width / self._zoom), int(height / self._zoom)
            left, top = (width - crop_w) // 2, (height - crop_h) // 2
            image = image.crop((left, top, left + crop_w, top + crop_h))
        view_w, view_h = self.view.winfo_width(), self.view.winfo_height()
        if view_w > 1 and view_h > 1:
            scale = min(view_w / image.width, view_h / image.height)
            size = (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
            image = image.resize(size, Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(image)
        self.view.configure(image=self._photo, text="")

    # -- 辅助 ---------------------------------------------------------------
    def _ui(self, func: Callable[..., None], *args: object) -> None:
        self._ui_queue.put((func, args))

    def _pump(self) -> None:
        # Tk 只能在主线程调用，工作线程的结果经队列交回
        while True:
            try:
                func, args = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            func(*args)
        if not self._closing:
            self._pump_id = self.root.after(PUMP_INTERVAL_MS, self._pump)

    def _enable_inputs(self) -> None:
        self.button.state(["!disabled"])
        self.entry.state(["!disabled"])

    def _set_controls_enabled(self, enabled: bool) -> None:
        state = ["!disabled"] if enabled else ["disabled"]
        for widget in (
            self.bionic_button,
            self.tracking_button,
            self.posture_box,
        ):
            widget.state(state)
        self._set_head_enabled(enabled)

    def _set_head_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.head_yaw_scale.configure(state=state)
        self.head_pitch_scale.configure(state=state)

    def _set_status(self, text: str, error: bool = False) -> None:
        self.status.configure(text=text, foreground="#c0392b" if error else "#666666")

    def _show_error(self, text: str) -> None:
        self._set_status(text, error=True)

    def close(self) -> None:
        self._closing = True
        for after_id in (self._after_id, self._pump_id):
            if after_id is not None:
                self.root.after_cancel(after_id)
        self._after_id = None
        self._pump_id = None
        self._epoch += 1
        self._head_stop.set()
        self._head_wake.set()
        self._tracking = False
        robot, self.robot = self.robot, None
        if robot is not None:
            # 摄像头退订可能阻塞两秒，不能挡在窗口关闭路径上
            threading.Thread(target=self._teardown, args=(robot,), daemon=True).start()
        self.root.destroy()


def main() -> int:
    initial_ip = sys.argv[1] if len(sys.argv) > 1 else Settings().nao_ip
    root = tk.Tk()
    window = VisionWindow(root, initial_ip)
    root.protocol("WM_DELETE_WINDOW", window.close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Tkinter standalone UI for AutomaticReversing."""

from __future__ import annotations

import hashlib
import math
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from automatic_reversing.controller import ReversingController
from automatic_reversing.full_telemetry import FullSCSTelemetryReader
from automatic_reversing.geometry import normalize_angle, offset_in_vehicle_frame
from automatic_reversing.models import ControllerConfig, ControlCommand, Pose2D, TargetPose
from automatic_reversing.scs_sdk import (
    SCSControlsWriter,
    SharedMemoryUnavailable,
)
from automatic_reversing.surroundings import (
    SurroundingsFileReader,
    SurroundingsSnapshot,
    demo_snapshot,
    directional_clearances,
    movement_clearance_profile,
    polygon_distance,
)
from automatic_reversing.telemetry import TelemetryError, vehicle_state_from_api
from automatic_reversing_visualizer.app import Pose

from .settings import SettingsStore
from .vehicle_geometry import ReversingGeometryModel

try:
    import keyboard as global_keyboard
except ImportError:
    global_keyboard = None


APP_DIR = Path(__file__).resolve().parents[1]
SETTINGS_PATH = APP_DIR / "settings.json"
SURROUNDINGS_PATH = APP_DIR / "surroundings.json"


class AutomaticReversingApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("自动倒车辅助")
        self.root.geometry("1080x720")
        self.root.minsize(960, 640)

        self.settings = SettingsStore(SETTINGS_PATH)
        self.telemetry = FullSCSTelemetryReader()
        self.controls = SCSControlsWriter()
        self.controller = ReversingController(self._config_from_settings())
        self.vehicle_geometry = ReversingGeometryModel()
        self.surroundings_reader = SurroundingsFileReader(SURROUNDINGS_PATH)
        self.surroundings = SurroundingsSnapshot()
        self.last_api = None
        self.session_target: TargetPose | None = None
        self.last_state = None
        self.last_command = ControlCommand()
        self.last_control_write = 0.0
        self.last_vehicle_signature = None
        self.global_hotkeys = []
        self.global_hotkey_status = "全局热键：不可用"
        self.status = "空闲"
        self._movement_cache_key = None
        self._movement_profile: tuple[tuple[float, float], ...] = ()

        self._build_ui()
        self.vehicle_geometry.attach_ui(
            self.root,
            self.visual_canvas,
            self.geometry_var,
            lambda: self._read_telemetry_once(show_errors=False),
        )
        self._bind_shortcuts()
        self._bind_global_hotkeys()
        self._tick()

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(self.root, padding=(10, 10, 10, 4))
        toolbar.grid(row=0, column=0, sticky="ew")
        for index in range(3):
            toolbar.columnconfigure(index, weight=1)

        ttk.Button(toolbar, text="设置目标 (F9)", command=self.set_target).grid(row=0, column=0, padx=4, sticky="ew")
        ttk.Button(toolbar, text="开始 / 停止 (F8)", command=self.toggle).grid(row=0, column=1, padx=4, sticky="ew")
        ttk.Button(toolbar, text="取消 (F10)", command=self.cancel).grid(row=0, column=2, padx=4, sticky="ew")
        ttk.Button(toolbar, text="保存控制配置", command=self.save_vehicle_profile).grid(row=1, column=0, padx=4, pady=(5, 0), sticky="ew")
        ttk.Button(toolbar, text="控制设置", command=self.open_control_settings).grid(row=1, column=1, padx=4, pady=(5, 0), sticky="ew")
        ttk.Button(toolbar, text="车型轮廓", command=self.vehicle_geometry.open_geometry_config).grid(
            row=1, column=2, padx=4, pady=(5, 0), sticky="ew"
        )

        content = ttk.Frame(self.root, padding=10)
        content.grid(row=1, column=0, sticky="nsew")
        content.columnconfigure(0, weight=3, minsize=560)
        content.columnconfigure(1, weight=2, minsize=340)
        content.rowconfigure(0, weight=0)
        content.rowconfigure(1, weight=1)
        content.rowconfigure(2, weight=1)

        visual_frame = ttk.LabelFrame(content, text="周边环境与可行动空间", padding=8)
        visual_frame.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 6), pady=(0, 8))
        visual_frame.columnconfigure(0, weight=1)
        visual_frame.rowconfigure(0, weight=1)
        self.visual_canvas = tk.Canvas(visual_frame, width=480, height=420, bg="#181b20", highlightthickness=0)
        self.visual_canvas.grid(row=0, column=0, sticky="nsew")
        self.visual_canvas.bind("<Configure>", lambda _event: self._draw_vehicle_view())

        status_frame = ttk.LabelFrame(content, text="状态", padding=10)
        status_frame.grid(row=0, column=1, sticky="ew", padx=(6, 0), pady=(0, 8))
        status_frame.columnconfigure(1, weight=1)

        self.status_var = tk.StringVar(value="状态：空闲")
        self.telemetry_var = tk.StringVar(value="遥测：未连接")
        self.vehicle_var = tk.StringVar(value="车辆：未知")
        self.command_var = tk.StringVar(value="控制输出：空闲")
        self.pose_var = tk.StringVar(value="目标：未设置")
        self.hotkey_var = tk.StringVar(value="热键：F8 开始/停止，F9 设置目标，F10 取消")
        self.geometry_var = tk.StringVar(value="轮廓：等待车辆数据")
        self.surroundings_var = tk.StringVar(value="环境：等待障碍物数据")
        self.demo_surroundings_var = tk.BooleanVar(value=False)

        for row, variable in enumerate(
            (
                self.status_var,
                self.telemetry_var,
                self.vehicle_var,
                self.command_var,
                self.pose_var,
                self.hotkey_var,
                self.geometry_var,
                self.surroundings_var,
            )
        ):
            columnspan = 1 if variable is self.surroundings_var else 2
            ttk.Label(status_frame, textvariable=variable, wraplength=280, justify="left").grid(
                row=row, column=0, columnspan=columnspan, sticky="w"
            )
        ttk.Checkbutton(
            status_frame,
            text="演示",
            variable=self.demo_surroundings_var,
            command=self._toggle_demo_surroundings,
        ).grid(row=7, column=1, sticky="e")

        target_frame = ttk.LabelFrame(content, text="目标微调（以车头前后左右为准）", padding=10)
        target_frame.grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=(0, 8))
        for index in range(3):
            target_frame.columnconfigure(index, weight=1)

        ttk.Button(target_frame, text="左移", command=lambda: self.nudge_target(lateral=-self._float_setting("target_step_m"))).grid(row=0, column=0, padx=3, pady=1, sticky="ew")
        ttk.Button(target_frame, text="前移", command=lambda: self.nudge_target(forward=self._float_setting("target_step_m"))).grid(row=0, column=1, padx=3, pady=1, sticky="ew")
        ttk.Button(target_frame, text="右移", command=lambda: self.nudge_target(lateral=self._float_setting("target_step_m"))).grid(row=0, column=2, padx=3, pady=1, sticky="ew")
        ttk.Button(target_frame, text="逆时针", command=lambda: self.nudge_target(yaw_delta=-math.radians(self._float_setting("target_step_deg")))).grid(row=1, column=0, padx=3, pady=1, sticky="ew")
        ttk.Button(target_frame, text="后移", command=lambda: self.nudge_target(forward=-self._float_setting("target_step_m"))).grid(row=1, column=1, padx=3, pady=1, sticky="ew")
        ttk.Button(target_frame, text="顺时针", command=lambda: self.nudge_target(yaw_delta=math.radians(self._float_setting("target_step_deg")))).grid(row=1, column=2, padx=3, pady=1, sticky="ew")

        params_frame = ttk.LabelFrame(content, text="实时车辆参数", padding=8)
        params_frame.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(0, 8))
        params_frame.columnconfigure(0, weight=1)
        params_frame.rowconfigure(0, weight=1)
        self.params = ttk.Treeview(params_frame, columns=("value",), show="tree headings", height=12)
        self.params.heading("#0", text="参数")
        self.params.heading("value", text="数值")
        self.params.column("#0", width=220, stretch=True)
        self.params.column("value", width=300, stretch=True)
        self.params.grid(row=0, column=0, sticky="nsew")
        params_scroll = ttk.Scrollbar(params_frame, orient="vertical", command=self.params.yview)
        params_scroll.grid(row=0, column=1, sticky="ns")
        self.params.configure(yscrollcommand=params_scroll.set)

        self.entries: dict[str, tk.StringVar] = {}
        self.settings_window: tk.Toplevel | None = None

        help_text = (
            "请先启动 ETS2/ATS，并确保已安装 SCS 遥测/控制 DLL。"
            "F9 会按车头朝向建立二维直库目标，再以车头前后左右微调位置。"
            "踩刹车或按 F10 可随时取消。"
        )
        ttk.Label(self.root, text=help_text, padding=(10, 4, 10, 10), wraplength=720).grid(row=2, column=0, sticky="ew")

    def _bind_shortcuts(self) -> None:
        self.root.bind("<F8>", lambda _event: self.toggle())
        self.root.bind("<F9>", lambda _event: self.set_target())
        self.root.bind("<F10>", lambda _event: self.cancel())
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _bind_global_hotkeys(self) -> None:
        if global_keyboard is None:
            return
        try:
            self.global_hotkeys = [
                global_keyboard.add_hotkey("F8", lambda: self.root.after(0, self.toggle)),
                global_keyboard.add_hotkey("F9", lambda: self.root.after(0, self.set_target)),
                global_keyboard.add_hotkey("F10", lambda: self.root.after(0, self.cancel)),
            ]
            self.global_hotkey_status = "全局热键：F8/F9/F10 已启用"
        except Exception:
            self.global_hotkey_status = "全局热键：不可用，请聚焦窗口后使用 F8/F9/F10"

    def _toggle_demo_surroundings(self) -> None:
        self._movement_cache_key = None
        self._refresh_surroundings()
        self._refresh_surroundings_status()
        self._draw_vehicle_view()

    def _refresh_surroundings(self) -> None:
        if self.last_state is None:
            self.surroundings = SurroundingsSnapshot(error="等待车辆姿态")
        elif self.demo_surroundings_var.get():
            self.surroundings = demo_snapshot(self.last_state.truck_pose)
        else:
            self.surroundings = self.surroundings_reader.read(self.last_state.truck_pose)

    def _refresh_surroundings_status(self) -> None:
        snapshot = self.surroundings
        if snapshot.error:
            self.surroundings_var.set(f"环境：{snapshot.error}")
            return
        if snapshot.stale:
            self.surroundings_var.set("环境：数据已过期，可行动空间已停用")
            return
        clearance = self._nearest_obstacle_clearance()
        clearance_text = "--" if not math.isfinite(clearance) else f"{clearance:.1f} m"
        source = "演示" if snapshot.source.startswith("演示环境") else snapshot.source
        self.surroundings_var.set(f"环境：{source} | 障碍物 {len(snapshot.obstacles)} | 最近 {clearance_text}")

    def _float_setting(self, key: str) -> float:
        return float(self.settings.get(key))

    def _config_from_settings(self) -> ControllerConfig:
        return ControllerConfig(
            target_speed_kmh=self._float_setting("target_speed_kmh"),
            max_speed_kmh=self._float_setting("max_speed_kmh"),
            max_articulation_deg=self._float_setting("max_articulation_deg"),
            steering_gain_lateral=self._float_setting("steering_gain_lateral"),
            steering_gain_heading=self._float_setting("steering_gain_heading"),
            steering_gain_articulation=self._float_setting("steering_gain_articulation"),
            max_steering=self._float_setting("max_steering"),
            steering_sign=self._float_setting("steering_sign"),
        )

    def _target_from_settings(self) -> TargetPose | None:
        if self.session_target is None:
            return None
        return TargetPose(
            self.session_target.pose,
            tolerance_m=self._float_setting("tolerance_m"),
            tolerance_deg=self._float_setting("tolerance_deg"),
        )

    def _store_target(self, target: TargetPose) -> None:
        self.session_target = target

    def set_target(self) -> None:
        if self.last_state is None:
            self.status = "无法设置目标：遥测不可用"
            return
        target = TargetPose(
            Pose2D(
                self.last_state.trailer_pose.x,
                self.last_state.trailer_pose.z,
                self.last_state.truck_pose.yaw,
            ),
            tolerance_m=self._float_setting("tolerance_m"),
            tolerance_deg=self._float_setting("tolerance_deg"),
        )
        self.controller.set_target(target)
        self._store_target(target)
        self.save_vehicle_profile(show_message=False)
        self.status = "已按车头方向建立二维直库目标"

    def nudge_target(self, forward: float = 0.0, lateral: float = 0.0, yaw_delta: float = 0.0) -> None:
        target = self._target_from_settings()
        if target is None:
            self.status = "请先设置目标，再进行微调"
            return
        reference_yaw = self.last_state.truck_pose.yaw if self.last_state is not None else target.pose.yaw
        x, z = offset_in_vehicle_frame(
            target.pose.x,
            target.pose.z,
            reference_yaw,
            forward=forward,
            right=lateral,
        )
        new_target = TargetPose(
            Pose2D(x, z, normalize_angle(target.pose.yaw + yaw_delta)),
            tolerance_m=target.tolerance_m,
            tolerance_deg=target.tolerance_deg,
        )
        self.controller.set_target(new_target)
        self._store_target(new_target)
        self.status = "目标已微调"

    def open_control_settings(self) -> None:
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.lift()
            self.settings_window.focus_force()
            return
        window = tk.Toplevel(self.root)
        self.settings_window = window
        window.title("自动倒车控制设置")
        window.geometry("720x410")
        window.minsize(620, 360)
        window.transient(self.root)

        frame = ttk.Frame(window, padding=14)
        frame.pack(fill="both", expand=True)
        for index in range(4):
            frame.columnconfigure(index, weight=1)
        fields = [
            ("目标速度 km/h", "target_speed_kmh"),
            ("最高速度 km/h", "max_speed_kmh"),
            ("最大折角 deg", "max_articulation_deg"),
            ("横向增益", "steering_gain_lateral"),
            ("朝向增益", "steering_gain_heading"),
            ("折角增益", "steering_gain_articulation"),
            ("最大转向", "max_steering"),
            ("转向方向", "steering_sign"),
            ("位移步长 m", "target_step_m"),
            ("旋转步长 deg", "target_step_deg"),
            ("距离容差 m", "tolerance_m"),
            ("角度容差 deg", "tolerance_deg"),
        ]
        self.entries = {}
        for index, (label, key) in enumerate(fields):
            row = index // 2
            col = (index % 2) * 2
            ttk.Label(frame, text=label).grid(row=row, column=col, padx=5, pady=7, sticky="w")
            variable = tk.StringVar(value=str(self.settings.get(key)))
            ttk.Entry(frame, width=12, textvariable=variable).grid(
                row=row, column=col + 1, padx=5, pady=7, sticky="ew"
            )
            self.entries[key] = variable

        buttons = ttk.Frame(frame)
        buttons.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(14, 0))
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        ttk.Button(buttons, text="应用设置", command=self.apply_settings).grid(
            row=0, column=0, padx=(0, 5), sticky="ew"
        )
        ttk.Button(buttons, text="关闭", command=self._close_control_settings).grid(
            row=0, column=1, padx=(5, 0), sticky="ew"
        )
        window.protocol("WM_DELETE_WINDOW", self._close_control_settings)

    def _close_control_settings(self) -> None:
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.destroy()
        self.settings_window = None
        self.entries = {}

    def apply_settings(self) -> bool:
        for key, var in self.entries.items():
            try:
                self.settings.set(key, float(var.get()))
            except ValueError:
                messagebox.showerror("自动倒车辅助", f"{key} 的数值无效")
                return False
        target = self._target_from_settings()
        if target is not None:
            self.controller.set_target(target)
        self.controller.set_config(self._config_from_settings())
        self.save_vehicle_profile(show_message=False)
        self.status = "设置已应用"
        return True

    def save_vehicle_profile(self, show_message: bool = True) -> None:
        if self.last_state is None:
            if show_message:
                self.status = "无法保存车型配置：遥测不可用"
            return
        profiles = self.settings.get("vehicle_profiles", {})
        if not isinstance(profiles, dict):
            profiles = {}
        profiles[self._vehicle_profile_signature()] = {
            key: self.settings.get(key)
            for key in [
                "target_speed_kmh",
                "max_speed_kmh",
                "max_articulation_deg",
                "steering_gain_lateral",
                "steering_gain_heading",
                "steering_gain_articulation",
                "max_steering",
                "steering_sign",
            ]
        }
        self.settings.set("vehicle_profiles", profiles)
        if show_message:
            self.status = "车型配置已保存"

    def _load_vehicle_profile(self) -> None:
        if self.last_state is None:
            return
        signature = self._vehicle_profile_signature()
        if signature == self.last_vehicle_signature:
            return
        vehicle_changed = self.last_vehicle_signature is not None
        self.last_vehicle_signature = signature
        if vehicle_changed:
            was_active = self.controller.active
            self.controller.clear_target("vehicle_changed")
            self.session_target = None
            self.last_command = ControlCommand(reason="vehicle_changed")
            if was_active:
                self._write_command(self.last_command)
        profiles = self.settings.get("vehicle_profiles", {})
        profile = profiles.get(signature) if isinstance(profiles, dict) else None
        if not isinstance(profile, dict) and isinstance(profiles, dict):
            profile = profiles.get(self.last_state.vehicle_signature)
        if not isinstance(profile, dict):
            if vehicle_changed:
                self.status = "车型已切换，目标已清除"
            return
        for key, value in profile.items():
            self.settings.set(key, value)
            if key in self.entries:
                self.entries[key].set(str(value))
        self.controller.set_config(self._config_from_settings())
        self.status = "车型已切换，目标已清除" if vehicle_changed else "已加载当前车型配置"

    def _vehicle_profile_signature(self) -> str:
        if self.last_api is None:
            return self.last_state.vehicle_signature if self.last_state is not None else "unknown"
        signature = self.vehicle_geometry._configuration_signature(self.last_api)
        digest = hashlib.sha256(repr(signature).encode("utf-8")).hexdigest()[:20]
        return f"geometry:{digest}"

    def toggle(self) -> None:
        if not self.apply_settings():
            return
        if self.controller.active:
            self.cancel("manual_cancel")
            return
        target = self._target_from_settings()
        if target is not None:
            self.controller.set_target(target)
        if self.controller.start():
            self.status = "自动泊车运行中，正在判断前进或倒车"
        else:
            self.status = "请先设置目标，再开始倒车"

    def cancel(self, reason: str = "manual_cancel") -> None:
        self.controller.cancel(reason)
        command = ControlCommand(reason=reason)
        self.last_command = command
        self._write_command(command)
        self.status = "已取消"

    def _write_command(self, command: ControlCommand) -> None:
        try:
            self.controls.write_command(command)
            self.last_control_write = time.time()
        except SharedMemoryUnavailable as exc:
            self.status = self._localize_message(str(exc))

    def _read_telemetry_once(self, show_errors: bool = False) -> bool:
        try:
            api = self.telemetry.read()
            if api.get("scsValues", {}).get("telemetryPluginRevision", 0) < 2:
                raise SharedMemoryUnavailable("Waiting for ETS2/ATS telemetry")
            self.last_api = api
            self.last_state = vehicle_state_from_api(api)
            geometry_ready = self.vehicle_geometry.update_snapshot(api)
            self._refresh_surroundings()
            self._load_vehicle_profile()
            self.telemetry_var.set("遥测：已连接")
            self._refresh_geometry_status(geometry_ready)
        except (SharedMemoryUnavailable, TelemetryError, OSError) as exc:
            self.last_state = None
            self.last_api = None
            self._refresh_surroundings()
            self.telemetry_var.set("遥测：未连接")
            if self.controller.active:
                self.cancel(str(exc))
            else:
                self.status = self._localize_message(str(exc))
            self.geometry_var.set("轮廓：等待完整 telemetry 数据")
            if show_errors:
                messagebox.showwarning("自动倒车辅助", self.status)
            return False
        return True

    def _refresh_geometry_status(self, geometry_ready: bool) -> None:
        if not geometry_ready:
            self.geometry_var.set("轮廓：检测到车型变化，正在同步游戏模型")
            return
        missing = self.vehicle_geometry._missing_resource_ids()
        if missing:
            self.geometry_var.set(f"轮廓：缺少 {', '.join(missing)}，请在“车型轮廓”中导入资源")
            return
        units = self.vehicle_geometry.last_units
        if not units:
            self.geometry_var.set("轮廓：当前没有可绘制的车辆")
            return
        summary = " | ".join(
            f"{unit.name} {unit.game_axle_count or len(self.vehicle_geometry._axle_groups(unit.wheels))}轴"
            for unit in units
        )
        self.geometry_var.set(f"轮廓：游戏碰撞模型 | {summary}")

    def _tick(self) -> None:
        if not self._read_telemetry_once():
            self._refresh_labels()
            self.root.after(50, self._tick)
            return

        self.controller.set_config(self._config_from_settings())
        command = self.controller.update(self.last_state)
        self.status = self._localize_message(command.reason)
        if not command.active:
            command = ControlCommand(reason=command.reason)
        self.last_command = command
        self._write_command(command)

        self._refresh_labels()
        self.root.after(50, self._tick)

    def _refresh_labels(self) -> None:
        self.status_var.set(f"状态：{self.status}")
        if self.last_state is None:
            self.vehicle_var.set("车辆：未知")
        else:
            speed = abs(self.last_state.speed) * 3.6
            articulation = math.degrees(self.last_state.articulation_angle)
            config = self.last_api.get("configString", {}) if self.last_api else {}
            truck_name = config.get("truckName") or config.get("truckId") or "未知车头"
            trailer_units = [unit.name for unit in self.vehicle_geometry.last_units if unit.kind == "trailer"]
            trailer_name = ", ".join(trailer_units) or "无挂车"
            self.vehicle_var.set(
                f"车辆：{truck_name} | 挂车：{trailer_name} | "
                f"速度 {speed:.1f} km/h | 折角 {articulation:.1f} deg"
            )

        target = self._target_from_settings()
        if target is None:
            self.pose_var.set("目标：未设置")
        else:
            self.pose_var.set(
                f"目标：X {target.pose.x:.2f}, Z {target.pose.z:.2f}, 朝向 {math.degrees(target.pose.yaw):.1f} deg"
            )

        self.command_var.set(
            "控制输出："
            f"转向 {self.last_command.steering:.2f}, 油门 {self.last_command.throttle:.2f}, "
            f"刹车 {self.last_command.brake:.2f}, "
            f"方向 {'倒车' if self.last_command.reverse else ('前进' if self.last_command.drive else '释放')}"
        )
        self.hotkey_var.set("热键：F8 开始/停止，F9 设置目标，F10 取消 | " + self.global_hotkey_status)
        self._refresh_surroundings_status()
        self._refresh_params()
        self._draw_vehicle_view()

    def _refresh_params(self) -> None:
        values = []
        if self.last_state is None:
            values.extend(
                [
                    ("遥测连接", "否"),
                    ("控制器运行", self._bool_text(self.controller.active)),
                    ("状态", self.status),
                ]
            )
        else:
            target = self._target_from_settings()
            values.extend(
                [
                    ("遥测连接", "是"),
                    ("控制器运行", self._bool_text(self.controller.active)),
                    ("状态", self.status),
                    ("车型签名", self.last_state.vehicle_signature),
                    ("挂车数量", str(self.last_state.trailer_count)),
                    ("车头 X", f"{self.last_state.truck_pose.x:.3f}"),
                    ("车头 Z", f"{self.last_state.truck_pose.z:.3f}"),
                    ("车头朝向", f"{math.degrees(self.last_state.truck_pose.yaw):.2f} deg"),
                    ("挂车 X", f"{self.last_state.trailer_pose.x:.3f}"),
                    ("挂车 Z", f"{self.last_state.trailer_pose.z:.3f}"),
                    ("挂车朝向", f"{math.degrees(self.last_state.trailer_pose.yaw):.2f} deg"),
                    ("车头-挂车折角", f"{math.degrees(self.last_state.articulation_angle):.2f} deg"),
                    ("速度", f"{abs(self.last_state.speed) * 3.6:.2f} km/h"),
                    ("挡位", str(self.last_state.gear)),
                    ("游戏暂停", self._bool_text(self.last_state.paused)),
                    ("玩家油门", f"{self.last_state.user_throttle:.2f}"),
                    ("玩家刹车", f"{self.last_state.user_brake:.2f}"),
                    ("输出转向", f"{self.last_command.steering:.3f}"),
                    ("输出油门", f"{self.last_command.throttle:.3f}"),
                    ("输出刹车", f"{self.last_command.brake:.3f}"),
                    ("输出方向", "倒车" if self.last_command.reverse else ("前进" if self.last_command.drive else "释放")),
                ]
            )
            if target is not None:
                values.extend(
                    [
                        ("目标 X", f"{target.pose.x:.3f}"),
                        ("目标 Z", f"{target.pose.z:.3f}"),
                        ("目标朝向", f"{math.degrees(target.pose.yaw):.2f} deg"),
                        ("目标容差", f"{target.tolerance_m:.2f} m / {target.tolerance_deg:.1f} deg"),
                    ]
                )
            for unit in self.vehicle_geometry.last_units:
                axle_count = unit.game_axle_count or len(self.vehicle_geometry._axle_groups(unit.wheels))
                values.extend(
                    [
                        (f"{unit.name}轮廓来源", unit.dimensions_source),
                        (f"{unit.name}尺寸", f"{unit.length:.3f} m x {unit.width:.3f} m"),
                        (f"{unit.name}车轴/轮位", f"{axle_count} 轴 / {unit.reported_wheel_count} 轮位"),
                        (f"{unit.name}游戏定义", " + ".join(unit.definition_paths) or "未找到"),
                    ]
                )
            if self.surroundings.usable:
                clearance = self._nearest_obstacle_clearance()
                values.extend(
                    [
                        ("环境数据源", self.surroundings.source),
                        ("障碍物数量", str(len(self.surroundings.obstacles))),
                        ("最近障碍物", "--" if not math.isfinite(clearance) else f"{clearance:.2f} m"),
                        ("探测范围", f"{self.surroundings.range_m:.1f} m"),
                    ]
                )
                for obstacle in self.surroundings.obstacles:
                    values.append((f"障碍物 {obstacle.label}", f"{obstacle.kind} | 置信度 {obstacle.confidence:.0%}"))

        self.params.delete(*self.params.get_children())
        for key, value in values:
            self.params.insert("", "end", text=key, values=(value,))

    def _draw_vehicle_view(self) -> None:
        canvas = self.visual_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 200)
        height = max(canvas.winfo_height(), 200)
        canvas.create_rectangle(0, 0, width, height, fill="#181b20", outline="")

        target = self._target_from_settings()
        geometry = self.vehicle_geometry
        units = geometry.last_units
        trailer_unit = next((unit for unit in units if unit.kind == "trailer"), None)
        target_pose = self._target_geometry_pose(trailer_unit, target.pose) if target and trailer_unit else None
        vehicle_polygons = tuple(self._unit_world_polygon(unit, unit.pose) for unit in units)
        movement_origin = (
            (self.last_state.truck_pose.x, self.last_state.truck_pose.z)
            if self.last_state is not None
            else None
        )
        movement_profile = self._movement_clearance(vehicle_polygons, movement_origin)

        world_points = []
        for unit in units:
            world_points.extend(self._unit_world_bounds(unit, unit.pose))
        if trailer_unit is not None and target_pose is not None:
            world_points.extend(self._unit_world_bounds(trailer_unit, target_pose))
        elif target is not None:
            world_points.append((target.pose.x, target.pose.z))
        for obstacle in self.surroundings.obstacles:
            world_points.extend(obstacle.polygon)
        if movement_origin is not None:
            world_points.extend(
                (
                    movement_origin[0] + math.cos(angle) * distance,
                    movement_origin[1] + math.sin(angle) * distance,
                )
                for angle, distance in movement_profile
            )

        if not world_points:
            canvas.create_text(width / 2, height / 2, fill="#d9dee7", text="等待遥测连接", font=("Segoe UI", 16, "bold"))
            return

        min_x = min(point[0] for point in world_points)
        max_x = max(point[0] for point in world_points)
        min_z = min(point[1] for point in world_points)
        max_z = max(point[1] for point in world_points)
        span = max(max_x - min_x, max_z - min_z, 12.0)
        scale = min((width - 80) / span, (height - 80) / span)
        center_x = (min_x + max_x) / 2
        center_z = (min_z + max_z) / 2

        def to_canvas(x: float, z: float) -> tuple[float, float]:
            return width / 2 + (x - center_x) * scale, height / 2 - (z - center_z) * scale

        self._draw_grid(canvas, width, height)
        if movement_origin is not None and movement_profile:
            self._draw_free_space(to_canvas, movement_origin, movement_profile, width)
        self._draw_obstacles(to_canvas, vehicle_polygons)
        if trailer_unit is not None and target_pose is not None:
            self._draw_target_geometry(to_canvas, trailer_unit, target_pose, target)
        elif target is not None:
            self._draw_pose(canvas, to_canvas, target.pose, 1.5, 1.5, "#4cc9f0", "目标", outline_only=True)

        for unit in reversed(units):
            geometry._draw_unit(to_canvas, unit)
        if units:
            geometry._draw_coupling_marker(to_canvas)

        self._draw_command_overlay(canvas, width, height)

    def _unit_world_bounds(self, unit, pose: Pose) -> list[tuple[float, float]]:
        min_x, max_x, min_z, max_z = unit.bounds
        return [
            self.vehicle_geometry._local_to_world(pose, x, z)
            for x, z in ((min_x, min_z), (min_x, max_z), (max_x, min_z), (max_x, max_z))
        ]

    def _unit_world_polygon(self, unit, pose: Pose) -> tuple[tuple[float, float], ...]:
        contour = unit.body_contour
        if contour is None:
            min_x, max_x, min_z, max_z = unit.bounds
            contour = ((min_x, min_z), (max_x, min_z), (max_x, max_z), (min_x, max_z))
        return tuple(self.vehicle_geometry._local_to_world(pose, x, z) for x, z in contour)

    def _movement_clearance(
        self,
        vehicle_polygons: tuple[tuple[tuple[float, float], ...], ...],
        origin: tuple[float, float] | None,
    ) -> tuple[tuple[float, float], ...]:
        if origin is None or not vehicle_polygons or not self.surroundings.usable:
            return ()
        geometry_key = tuple(
            tuple((round(x - origin[0], 1), round(z - origin[1], 1)) for x, z in polygon)
            for polygon in vehicle_polygons
        )
        obstacle_key = tuple(
            tuple((round(x - origin[0], 1), round(z - origin[1], 1)) for x, z in obstacle.polygon)
            for obstacle in self.surroundings.obstacles
        )
        cache_key = (geometry_key, obstacle_key, round(self.surroundings.range_m, 1))
        if cache_key != self._movement_cache_key:
            self._movement_cache_key = cache_key
            self._movement_profile = movement_clearance_profile(
                vehicle_polygons,
                self.surroundings.obstacles,
                origin,
                self.surroundings.range_m,
            )
        return self._movement_profile

    def _draw_free_space(self, to_canvas, origin, profile, width: int) -> None:
        points = [
            to_canvas(origin[0] + math.cos(angle) * distance, origin[1] + math.sin(angle) * distance)
            for angle, distance in profile
        ]
        flat = [coordinate for point in points for coordinate in point]
        self.visual_canvas.create_polygon(
            flat,
            fill="#174238",
            outline="#34d399",
            width=2,
            stipple="gray25",
        )
        if self.last_state is not None:
            clearances = directional_clearances(profile, self.last_state.truck_pose.yaw)
            text = "平移余量  " + "  ".join(f"{label} {distance:.1f}m" for label, distance in clearances.items())
            self.visual_canvas.create_text(
                width / 2,
                18,
                text=text,
                fill="#86efac",
                font=("Microsoft YaHei UI", 9, "bold"),
            )

    def _draw_obstacles(self, to_canvas, vehicle_polygons) -> None:
        stale = not self.surroundings.usable
        colors = {
            "vehicle": ("#632f38", "#fb7185"),
            "pedestrian": ("#73313d", "#f43f5e"),
            "wall": ("#56343a", "#e87987"),
            "bollard": ("#664521", "#fbbf24"),
        }
        for obstacle in self.surroundings.obstacles:
            fill, outline = colors.get(obstacle.kind, ("#59343b", "#fb7185"))
            if stale:
                fill, outline = "#343941", "#7d8793"
            canvas_points = [to_canvas(x, z) for x, z in obstacle.polygon]
            flat = [coordinate for point in canvas_points for coordinate in point]
            self.visual_canvas.create_polygon(flat, fill=fill, outline=outline, width=2)
            center_x = sum(point[0] for point in canvas_points) / len(canvas_points)
            label_y = min(point[1] for point in canvas_points) - 4
            distance = min(
                (polygon_distance(vehicle, obstacle.polygon) for vehicle in vehicle_polygons),
                default=math.inf,
            )
            suffix = "" if not math.isfinite(distance) else f"  {distance:.1f}m"
            self.visual_canvas.create_text(
                center_x,
                label_y,
                text=obstacle.label + suffix,
                anchor="s",
                fill="#f8fafc" if not stale else "#aab2bd",
                font=("Microsoft YaHei UI", 8, "bold"),
            )

    def _nearest_obstacle_clearance(self) -> float:
        vehicle_polygons = tuple(
            self._unit_world_polygon(unit, unit.pose) for unit in self.vehicle_geometry.last_units
        )
        return min(
            (
                polygon_distance(vehicle, obstacle.polygon)
                for vehicle in vehicle_polygons
                for obstacle in self.surroundings.obstacles
            ),
            default=math.inf,
        )

    def _target_geometry_pose(self, trailer_unit, target: Pose2D) -> Pose:
        telemetry_pose = trailer_unit.telemetry_pose
        offset_x = trailer_unit.pose.x - telemetry_pose.x
        offset_z = trailer_unit.pose.z - telemetry_pose.z
        cos_yaw = math.cos(telemetry_pose.yaw)
        sin_yaw = math.sin(telemetry_pose.yaw)
        local_x = offset_x * cos_yaw + offset_z * sin_yaw
        local_z = -offset_x * sin_yaw + offset_z * cos_yaw
        target_offset = self.vehicle_geometry._rotate_local(target.yaw, local_x, local_z)
        return Pose(target.x + target_offset[0], target.z + target_offset[1], target.yaw)

    def _draw_target_geometry(self, to_canvas, trailer_unit, pose: Pose, target: TargetPose) -> None:
        contour = trailer_unit.body_contour
        if contour is None:
            min_x, max_x, min_z, max_z = trailer_unit.bounds
            contour = ((min_x, min_z), (max_x, min_z), (max_x, max_z), (min_x, max_z))
        min_x, max_x, min_z, max_z = trailer_unit.body_bounds or trailer_unit.bounds
        bay_margin_x = 0.45
        bay_margin_z = 0.75
        bay_local = (
            (min_x - bay_margin_x, min_z - bay_margin_z),
            (max_x + bay_margin_x, min_z - bay_margin_z),
            (max_x + bay_margin_x, max_z + bay_margin_z),
            (min_x - bay_margin_x, max_z + bay_margin_z),
        )
        bay_points = [to_canvas(*self.vehicle_geometry._local_to_world(pose, x, z)) for x, z in bay_local]
        bay_flat = [coordinate for point in bay_points for coordinate in point]
        self.visual_canvas.create_polygon(
            bay_flat,
            fill="",
            outline="#7dd3fc",
            width=2,
            dash=(10, 5),
        )
        center_start = to_canvas(*self.vehicle_geometry._local_to_world(pose, 0.0, min_z - bay_margin_z))
        center_end = to_canvas(*self.vehicle_geometry._local_to_world(pose, 0.0, max_z + bay_margin_z))
        self.visual_canvas.create_line(*center_start, *center_end, fill="#38bdf8", dash=(4, 5), width=1)

        world_points = [self.vehicle_geometry._local_to_world(pose, x, z) for x, z in contour]
        canvas_points = [to_canvas(*point) for point in world_points]
        flat = [coordinate for point in canvas_points for coordinate in point]
        self.visual_canvas.create_polygon(flat, fill="", outline="#4cc9f0", width=3, dash=(7, 4))

        for wheel in trailer_unit.wheels:
            corners = self.vehicle_geometry._wheel_corners_world(pose, wheel, trailer_unit.front_sign)
            wheel_flat = [coordinate for point in corners for coordinate in to_canvas(*point)]
            self.visual_canvas.create_polygon(wheel_flat, fill="", outline="#4cc9f0", width=1, dash=(3, 2))

        center = to_canvas(target.pose.x, target.pose.z)
        self.visual_canvas.create_line(center[0] - 5, center[1], center[0] + 5, center[1], fill="#4cc9f0")
        self.visual_canvas.create_line(center[0], center[1] - 5, center[0], center[1] + 5, fill="#4cc9f0")
        label_x = sum(point[0] for point in canvas_points) / len(canvas_points)
        label_y = min(point[1] for point in canvas_points) - 10
        self.visual_canvas.create_text(
            label_x,
            label_y,
            anchor="s",
            fill="#bdefff",
            text=f"二维直库目标 ±{target.tolerance_m:.2f}m",
            font=("Microsoft YaHei UI", 9, "bold"),
        )

    def _draw_grid(self, canvas: tk.Canvas, width: int, height: int) -> None:
        step = 40
        for x in range(0, width, step):
            canvas.create_line(x, 0, x, height, fill="#232831")
        for y in range(0, height, step):
            canvas.create_line(0, y, width, y, fill="#232831")

    def _draw_pose(self, canvas: tk.Canvas, to_canvas, pose: Pose2D, length: float, vehicle_width: float, color: str, label: str, outline_only: bool = False) -> None:
        half_l = length / 2
        half_w = vehicle_width / 2
        corners = [
            (half_l, half_w),
            (half_l, -half_w),
            (-half_l, -half_w),
            (-half_l, half_w),
        ]
        points = []
        cos_yaw = math.cos(pose.yaw)
        sin_yaw = math.sin(pose.yaw)
        for local_x, local_z in corners:
            world_x = pose.x + local_x * cos_yaw - local_z * sin_yaw
            world_z = pose.z + local_x * sin_yaw + local_z * cos_yaw
            points.extend(to_canvas(world_x, world_z))

        fill = "" if outline_only else color
        outline = color
        dash = (5, 3) if outline_only else None
        canvas.create_polygon(points, fill=fill, outline=outline, width=2, dash=dash)
        label_x, label_y = to_canvas(pose.x, pose.z)
        canvas.create_text(label_x, label_y, fill="#ffffff", text=label, font=("Segoe UI", 9, "bold"))

    def _draw_heading(self, canvas: tk.Canvas, to_canvas, pose: Pose2D, color: str) -> None:
        start_x, start_y = to_canvas(pose.x, pose.z)
        end_x, end_y = to_canvas(pose.x + math.cos(pose.yaw) * 4.0, pose.z + math.sin(pose.yaw) * 4.0)
        canvas.create_line(start_x, start_y, end_x, end_y, fill=color, width=3, arrow=tk.LAST)

    def _draw_command_overlay(self, canvas: tk.Canvas, width: int, height: int) -> None:
        steering = self.last_command.steering
        bar_x = 20
        bar_y = height - 34
        bar_w = 180
        canvas.create_rectangle(bar_x, bar_y, bar_x + bar_w, bar_y + 8, outline="#596170", fill="#252b35")
        center = bar_x + bar_w / 2
        tip = center + steering * (bar_w / 2)
        canvas.create_line(center, bar_y + 4, tip, bar_y + 4, fill="#5eead4", width=5)
        canvas.create_text(bar_x, bar_y - 10, anchor="w", fill="#d9dee7", text=f"转向 {steering:.2f}")

        throttle = self.last_command.throttle
        brake = self.last_command.brake
        canvas.create_text(width - 20, height - 28, anchor="e", fill="#d9dee7", text=f"油门 {throttle:.2f}  刹车 {brake:.2f}")

    def _bool_text(self, value: bool) -> str:
        return "是" if value else "否"

    def _localize_message(self, message: str) -> str:
        exact = {
            "idle": "空闲",
            "target_set": "目标已设置",
            "active": "自动倒车运行中",
            "coasting": "滑行控速中",
            "slowing": "正在减速",
            "cancelled": "已取消",
            "manual_cancel": "已手动取消",
            "no_target": "未设置目标",
            "missing_telemetry": "遥测数据缺失",
            "game_paused": "游戏已暂停",
            "user_brake": "检测到玩家刹车，控制已释放",
            "user_throttle": "检测到玩家油门，控制已释放",
            "unsupported_trailer_count": "仅支持单挂车，已取消",
            "articulation_limit": "车头-挂车折角过大，已取消",
            "target_reached": "已到达目标，自动刹停",
            "target_stopping": "已进入目标范围，正在刹停",
            "target_cleared": "目标已清除",
            "vehicle_changed": "车型已切换，目标已清除",
            "SCS telemetry shared memory is unavailable": "找不到 SCS 遥测共享内存，请确认游戏和 telemetry DLL 已启动",
            "SCS controls shared memory is unavailable": "找不到 SCS 控制共享内存，请确认 input_semantical.dll 已安装",
            "Waiting for ETS2/ATS telemetry": "等待 ETS2/ATS 遥测连接",
        }
        if message in exact:
            return exact[message]
        prefixes = {
            "active_reverse:": "自动倒车运行中",
            "active_forward:": "自动前进调整中",
            "coasting_reverse:": "倒车滑行控速中",
            "coasting_forward:": "前进滑行控速中",
            "slowing_reverse:": "倒车减速中",
            "slowing_forward:": "前进减速中",
            "missing telemetry field:": "缺少遥测字段：",
            "invalid telemetry field:": "遥测字段无效：",
        }
        for prefix, localized in prefixes.items():
            if message.startswith(prefix):
                if ":" in message[len(prefix) :]:
                    return localized + "（" + message + "）"
                return localized + message[len(prefix) :]
        return message

    def close(self) -> None:
        try:
            self.controls.write_command(ControlCommand())
        except SharedMemoryUnavailable:
            pass
        if global_keyboard is not None:
            for handle in self.global_hotkeys:
                try:
                    global_keyboard.remove_hotkey(handle)
                except Exception:
                    pass
        self.controls.close()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    AutomaticReversingApp(root)
    root.mainloop()

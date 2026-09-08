"""ETS2LA adapter for the AutomaticReversing core."""

from __future__ import annotations

import math
import sys
import time
from pathlib import Path


def _bootstrap_core_path() -> None:
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2],
        here.parents[3] / "AutomaticReversing" if len(here.parents) > 3 else None,
    ]
    for candidate in candidates:
        if candidate and (candidate / "automatic_reversing").is_dir():
            path = str(candidate)
            if path not in sys.path:
                sys.path.insert(0, path)
            return


_bootstrap_core_path()

from plugins.plugin import PluginInformation
from src.logger import print

PluginInfo = PluginInformation(
    name="AutomaticReversing",
    description="Low-speed trailer-first reversing assistant.",
    version="0.1",
    author="AutomaticReversing",
    url="",
    type="dynamic",
    dynamicOrder="controller",
    requires=["TruckSimAPI", "SDKController"],
)

import tkinter as tk
from tkinter import ttk

import src.controls as controls
import src.helpers as helpers
import src.settings as settings
import src.variables as variables
from automatic_reversing.controller import ReversingController
from automatic_reversing.geometry import normalize_angle
from automatic_reversing.models import ControllerConfig, Pose2D, TargetPose
from automatic_reversing.telemetry import TelemetryError, vehicle_state_from_api
from plugins.TruckSimAPI.scsPlugin import scsTelemetry


CATEGORY = "AutomaticReversing"
DEFAULTS = {
    "target_speed_kmh": 1.5,
    "max_speed_kmh": 3.0,
    "max_articulation_deg": 45.0,
    "steering_gain_lateral": 0.18,
    "steering_gain_heading": 0.75,
    "steering_gain_articulation": 0.45,
    "max_steering": 0.85,
    "steering_sign": -1.0,
    "tolerance_m": 0.75,
    "tolerance_deg": 8.0,
    "target_step_m": 0.25,
    "target_step_deg": 2.0,
}


controller = ReversingController()
telemetry_reader = None
last_action = 0.0
last_state = None
last_command = None
status_text = "空闲"
loaded_vehicle_signature = None


def _setting(name, default):
    return settings.GetSettings(CATEGORY, name, default)


def _set_setting(name, value):
    settings.CreateSettings(CATEGORY, name, value)


def _load_config() -> ControllerConfig:
    return ControllerConfig(
        target_speed_kmh=float(_setting("target_speed_kmh", DEFAULTS["target_speed_kmh"])),
        max_speed_kmh=float(_setting("max_speed_kmh", DEFAULTS["max_speed_kmh"])),
        max_articulation_deg=float(_setting("max_articulation_deg", DEFAULTS["max_articulation_deg"])),
        steering_gain_lateral=float(_setting("steering_gain_lateral", DEFAULTS["steering_gain_lateral"])),
        steering_gain_heading=float(_setting("steering_gain_heading", DEFAULTS["steering_gain_heading"])),
        steering_gain_articulation=float(_setting("steering_gain_articulation", DEFAULTS["steering_gain_articulation"])),
        max_steering=float(_setting("max_steering", DEFAULTS["max_steering"])),
        steering_sign=float(_setting("steering_sign", DEFAULTS["steering_sign"])),
    )


def _ensure_defaults():
    for key, value in DEFAULTS.items():
        settings.GetSettings(CATEGORY, key, value)


def _debounced(seconds=0.35) -> bool:
    global last_action
    now = time.time()
    if now - last_action < seconds:
        return False
    last_action = now
    return True


def _api_with_trailers(data):
    global telemetry_reader
    api = data.get("api")
    if isinstance(api, dict) and "trailers" in api:
        return api

    if telemetry_reader is None:
        telemetry_reader = scsTelemetry()

    full_api = telemetry_reader.update(trailerData=True)
    if isinstance(api, dict):
        merged = api.copy()
        merged["trailers"] = full_api.get("trailers", [])
        return merged
    return full_api


def _current_state(data):
    api = _api_with_trailers(data)
    return vehicle_state_from_api(api)


def _target_from_settings():
    x = settings.GetSettings(CATEGORY, "target_x")
    z = settings.GetSettings(CATEGORY, "target_z")
    yaw = settings.GetSettings(CATEGORY, "target_yaw")
    if x is None or z is None or yaw is None:
        return None
    return TargetPose(
        Pose2D(float(x), float(z), float(yaw)),
        tolerance_m=float(_setting("tolerance_m", DEFAULTS["tolerance_m"])),
        tolerance_deg=float(_setting("tolerance_deg", DEFAULTS["tolerance_deg"])),
    )


def _store_target(target: TargetPose):
    _set_setting("target_x", target.pose.x)
    _set_setting("target_z", target.pose.z)
    _set_setting("target_yaw", target.pose.yaw)
    _set_setting("tolerance_m", target.tolerance_m)
    _set_setting("tolerance_deg", target.tolerance_deg)


def _set_target_from_current_state():
    global status_text
    if not _debounced():
        return
    try:
        state = last_state
        if state is None:
            status_text = "无法设置目标：遥测不可用"
            return
        target = TargetPose(
            state.trailer_pose,
            tolerance_m=float(_setting("tolerance_m", DEFAULTS["tolerance_m"])),
            tolerance_deg=float(_setting("tolerance_deg", DEFAULTS["tolerance_deg"])),
        )
        controller.set_target(target)
        _store_target(target)
        _save_vehicle_profile(state.vehicle_signature)
        status_text = "已用当前挂车姿态设置目标"
    except Exception as ex:
        status_text = "无法设置目标"
        print(ex)


def _toggle_active():
    global status_text
    if not _debounced():
        return
    controller.set_config(_load_config())
    target = _target_from_settings()
    if target is not None:
        controller.set_target(target)
    if controller.active:
        controller.cancel("manual_cancel")
        status_text = "已取消"
    elif controller.start():
        status_text = "自动倒车运行中"
    else:
        status_text = "请先设置目标，再开始倒车"


def _cancel():
    global status_text
    if not _debounced(0.15):
        return
    controller.cancel("manual_cancel")
    status_text = "已取消"


def _vehicle_profiles():
    value = settings.GetSettings(CATEGORY, "vehicle_profiles", {})
    return value if isinstance(value, dict) else {}


def _save_vehicle_profile(signature):
    if not signature:
        return
    profiles = _vehicle_profiles()
    profiles[signature] = {
        "target_speed_kmh": float(_setting("target_speed_kmh", DEFAULTS["target_speed_kmh"])),
        "max_speed_kmh": float(_setting("max_speed_kmh", DEFAULTS["max_speed_kmh"])),
        "max_articulation_deg": float(_setting("max_articulation_deg", DEFAULTS["max_articulation_deg"])),
        "steering_gain_lateral": float(_setting("steering_gain_lateral", DEFAULTS["steering_gain_lateral"])),
        "steering_gain_heading": float(_setting("steering_gain_heading", DEFAULTS["steering_gain_heading"])),
        "steering_gain_articulation": float(_setting("steering_gain_articulation", DEFAULTS["steering_gain_articulation"])),
        "max_steering": float(_setting("max_steering", DEFAULTS["max_steering"])),
        "steering_sign": float(_setting("steering_sign", DEFAULTS["steering_sign"])),
    }
    _set_setting("vehicle_profiles", profiles)


def _load_vehicle_profile(signature):
    global loaded_vehicle_signature
    if not signature:
        return
    if loaded_vehicle_signature == signature:
        return
    profile = _vehicle_profiles().get(signature)
    loaded_vehicle_signature = signature
    if not isinstance(profile, dict):
        return
    for key in DEFAULTS:
        if key in profile:
            _set_setting(key, profile[key])


def _nudge_target(forward=0.0, lateral=0.0, yaw_delta=0.0):
    target = _target_from_settings()
    if target is None:
        return
    yaw = target.pose.yaw
    x = target.pose.x + math.cos(yaw) * forward - math.sin(yaw) * lateral
    z = target.pose.z + math.sin(yaw) * forward + math.cos(yaw) * lateral
    new_target = TargetPose(
        Pose2D(x, z, normalize_angle(yaw + yaw_delta)),
        tolerance_m=target.tolerance_m,
        tolerance_deg=target.tolerance_deg,
    )
    controller.set_target(new_target)
    _store_target(new_target)


controls.RegisterKeybind(
    "Toggle Automatic Reversing",
    callback=_toggle_active,
    defaultButtonIndex="r",
    notBoundInfo="绑定此按键以开始或停止自动倒车。",
    description="开始或停止自动倒车插件。",
)
controls.RegisterKeybind(
    "Set Target Pose",
    callback=_set_target_from_current_state,
    defaultButtonIndex="t",
    notBoundInfo="绑定此按键以记录当前挂车姿态作为倒车目标。",
    description="记录当前挂车姿态作为倒车目标。",
)
controls.RegisterKeybind(
    "Cancel Automatic Reversing",
    callback=_cancel,
    defaultButtonIndex="c",
    notBoundInfo="绑定此按键作为紧急取消快捷键。",
    description="立即取消自动倒车。",
)


def onEnable():
    _ensure_defaults()
    target = _target_from_settings()
    if target is not None:
        controller.set_target(target)
    controller.set_config(_load_config())


def onDisable():
    controller.cancel("plugin_disabled")


def _apply_command(data, command):
    if "sdk" not in data or not isinstance(data["sdk"], dict):
        data["sdk"] = {}
    if not command.active and command.brake <= 0:
        return data
    data["sdk"]["steering"] = command.steering
    data["sdk"]["acceleration"] = command.throttle
    data["sdk"]["brake"] = command.brake
    data["sdk"]["Reverse"] = command.reverse
    data["sdk"]["Drive"] = command.drive
    return data


def plugin(data):
    global last_state, last_command, status_text
    try:
        last_state = _current_state(data)
        _load_vehicle_profile(last_state.vehicle_signature)
        controller.set_config(_load_config())
    except TelemetryError as ex:
        last_state = None
        last_command = controller.cancel(str(ex)) if controller.active else None
        status_text = str(ex)
        if last_command is not None:
            return _apply_command(data, last_command)
        return data
    except Exception as ex:
        last_state = None
        last_command = controller.cancel("telemetry_error") if controller.active else None
        status_text = "遥测错误"
        print(ex)
        if last_command is not None:
            return _apply_command(data, last_command)
        return data

    last_command = controller.update(last_state)
    status_text = last_command.reason
    data["AutomaticReversing"] = {
        "active": controller.active,
        "status": status_text,
        "vehicleSignature": last_state.vehicle_signature,
        "command": {
            "steering": last_command.steering,
            "throttle": last_command.throttle,
            "brake": last_command.brake,
            "reverse": last_command.reverse,
            "active": last_command.active,
        },
    }
    return _apply_command(data, last_command)


class UI:
    def __init__(self, master) -> None:
        self.master = master
        self._build()

    def destroy(self):
        self.done = True
        self.root.destroy()
        del self

    def _build(self):
        try:
            self.root.destroy()
        except Exception:
            pass

        self.root = tk.Canvas(self.master, width=600, height=520, border=0, highlightthickness=0)
        self.root.grid_propagate(0)
        self.root.pack_propagate(0)

        self.status = helpers.MakeLabel(self.root, "状态：空闲", 0, 0, font=("Roboto", 14, "bold"), padx=15, pady=8, columnspan=3)
        self.pose = helpers.MakeLabel(self.root, "目标：未设置", 1, 0, padx=15, pady=4, columnspan=3)
        self.vehicle = helpers.MakeLabel(self.root, "车辆：未知", 2, 0, padx=15, pady=4, columnspan=3)

        helpers.MakeButton(self.root, "设置目标", lambda: _set_target_from_current_state(), 3, 0, width=16)
        helpers.MakeButton(self.root, "开始 / 停止", lambda: _toggle_active(), 3, 1, width=16)
        helpers.MakeButton(self.root, "取消", lambda: _cancel(), 3, 2, width=16)

        controls_frame = ttk.LabelFrame(self.root, text="目标微调")
        controls_frame.grid(row=4, column=0, padx=15, pady=8, columnspan=3, sticky="n")
        helpers.MakeButton(controls_frame, "向前", lambda: _nudge_target(forward=float(_setting("target_step_m", DEFAULTS["target_step_m"]))), 0, 1, width=12)
        helpers.MakeButton(controls_frame, "向左", lambda: _nudge_target(lateral=-float(_setting("target_step_m", DEFAULTS["target_step_m"]))), 1, 0, width=12)
        helpers.MakeButton(controls_frame, "向右", lambda: _nudge_target(lateral=float(_setting("target_step_m", DEFAULTS["target_step_m"]))), 1, 2, width=12)
        helpers.MakeButton(controls_frame, "向后", lambda: _nudge_target(forward=-float(_setting("target_step_m", DEFAULTS["target_step_m"]))), 2, 1, width=12)
        helpers.MakeButton(controls_frame, "逆时针", lambda: _nudge_target(yaw_delta=-math.radians(float(_setting("target_step_deg", DEFAULTS["target_step_deg"])))), 3, 0, width=12)
        helpers.MakeButton(controls_frame, "顺时针", lambda: _nudge_target(yaw_delta=math.radians(float(_setting("target_step_deg", DEFAULTS["target_step_deg"])))), 3, 2, width=12)

        settings_frame = ttk.LabelFrame(self.root, text="控制设置")
        settings_frame.grid(row=5, column=0, padx=15, pady=8, columnspan=3, sticky="n")
        self.entries = {}
        keys = [
            ("目标速度 km/h", "target_speed_kmh"),
            ("最高速度 km/h", "max_speed_kmh"),
            ("最大折角 deg", "max_articulation_deg"),
            ("横向增益", "steering_gain_lateral"),
            ("朝向增益", "steering_gain_heading"),
            ("折角增益", "steering_gain_articulation"),
            ("最大转向", "max_steering"),
            ("转向方向", "steering_sign"),
        ]
        for index, (label, key) in enumerate(keys):
            helpers.MakeLabel(settings_frame, label, index, 0, padx=6, pady=2, sticky="w")
            var = tk.StringVar(value=str(_setting(key, DEFAULTS[key])))
            entry = ttk.Entry(settings_frame, width=12, textvariable=var)
            entry.grid(row=index, column=1, padx=6, pady=2)
            self.entries[key] = var
        helpers.MakeButton(settings_frame, "应用", lambda: self._apply_settings(), len(keys), 0, width=16, columnspan=2)

        self.root.pack(anchor="center", expand=False)
        self.root.update()

    def _apply_settings(self):
        for key, var in self.entries.items():
            try:
                _set_setting(key, float(var.get()))
            except ValueError:
                pass
        if last_state is not None:
            _save_vehicle_profile(last_state.vehicle_signature)
        controller.set_config(_load_config())

    def update(self, data):
        target = _target_from_settings()
        self.status.set("状态：" + status_text)
        if target is None:
            self.pose.set("目标：未设置")
        else:
            self.pose.set(f"目标：X {target.pose.x:.2f}, Z {target.pose.z:.2f}, 朝向 {math.degrees(target.pose.yaw):.1f} deg")
        if last_state is None:
            self.vehicle.set("车辆：遥测不可用")
        else:
            self.vehicle.set(f"车辆：{last_state.vehicle_signature}")
        self.root.update()

"""Top-down vehicle geometry visualizer for SCS telemetry."""

from __future__ import annotations

import json
import hashlib
import math
import subprocess
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from automatic_reversing.full_telemetry import FullSCSTelemetryReader
from automatic_reversing.game_geometry import GameContour, GameGeometryResolver
from automatic_reversing.game_resources import (
    DEFAULT_EXTRACTOR,
    archive_candidates,
    best_archive,
    find_game,
    import_vehicle_archives,
)
from automatic_reversing.geometry import normalize_angle, scs_rotation_to_radians
from automatic_reversing.scs_sdk import SharedMemoryUnavailable
from automatic_reversing.telemetry import attached_trailers

DIMENSIONS_FILE = Path(__file__).resolve().parents[1] / "vehicle_dimensions.json"
GEOMETRY_OVERRIDES_FILE = Path(__file__).resolve().parents[1] / "geometry_overrides.json"
AUTO_SELECTION = "（自动匹配）"


@dataclass
class Pose:
    x: float
    z: float
    yaw: float


@dataclass
class WheelLayout:
    local_x: float
    local_z: float
    steering: float
    velocity: float
    steerable: bool
    radius: float = 0.5
    powered: bool = False
    liftable: bool = False
    source: str = "telemetry"


@dataclass
class DimensionSpec:
    key: str
    width: float
    front_z: float
    rear_z: float
    source: str

    @property
    def length(self) -> float:
        return self.front_z - self.rear_z

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        half_width = self.width / 2.0
        return -half_width, half_width, self.rear_z, self.front_z


@dataclass
class UnitLayout:
    name: str
    kind: str
    dimension_key: str
    pose: Pose
    telemetry_pose: Pose
    bounds: tuple[float, float, float, float]
    width: float
    length: float
    dimensions_source: str
    wheels: list[WheelLayout]
    hook: tuple[float, float] | None
    color: str
    outline: str
    body_bounds: tuple[float, float, float, float] | None = None
    body_contour: tuple[tuple[float, float], ...] | None = None
    model_paths: tuple[str, ...] = ()
    definition_paths: tuple[str, ...] = ()
    front_sign: float = -1.0
    aligned_to_coupling: bool = False
    reported_wheel_count: int = 0
    telemetry_wheel_count: int = 0
    game_axle_count: int = 0

    @property
    def has_verified_body(self) -> bool:
        return self.body_contour is not None


class VehicleGeometryVisualizer:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("车辆组合几何可视化")
        self.root.geometry("1280x820")
        self.root.minsize(980, 640)

        self.reader = FullSCSTelemetryReader()
        self.game_geometry = GameGeometryResolver()
        self.dimensions = self._load_dimensions()
        self.geometry_overrides = self._load_geometry_overrides()
        self.data: dict[str, Any] = {}
        self.paused = False
        self.refresh_ms = 120
        self.last_units: list[UnitLayout] = []
        self.configuration_signature: tuple[Any, ...] | None = None
        self.pending_configuration_signature: tuple[Any, ...] | None = None
        self.pending_configuration_frames = 0
        self.configuration_generation = 0
        self.resource_sync_attempts: set[tuple[str, ...]] = set()

        self._build_ui()
        self._bind_shortcuts()
        self._tick()

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(self.root, padding=(10, 10, 10, 6))
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar(value="状态：等待遥测")
        ttk.Label(toolbar, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        ttk.Button(toolbar, text="暂停刷新", command=self.toggle_pause).grid(row=0, column=1, padx=4)
        ttk.Button(toolbar, text="立即刷新", command=self.force_refresh).grid(row=0, column=2, padx=4)
        ttk.Button(toolbar, text="车型配置", command=self.open_geometry_config).grid(row=0, column=3, padx=4)
        ttk.Button(toolbar, text="导入当前车型资源", command=self.import_current_vehicle_resources).grid(
            row=0, column=4, padx=4
        )
        ttk.Button(toolbar, text="重新加载游戏轮廓", command=self.reload_dimensions).grid(row=0, column=5, padx=4)

        main = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        main.grid(row=1, column=0, sticky="nsew")
        main.columnconfigure(0, weight=3)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)

        canvas_frame = ttk.Frame(main)
        canvas_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        canvas_frame.columnconfigure(0, weight=1)
        canvas_frame.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(canvas_frame, background="#101418", highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.canvas.bind("<Configure>", lambda _event: self.redraw())

        side = ttk.Frame(main)
        side.grid(row=0, column=1, sticky="nsew")
        side.columnconfigure(0, weight=1)
        side.rowconfigure(1, weight=1)
        ttk.Label(side, text="几何参数", font=("Microsoft YaHei UI", 11, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.info = ttk.Treeview(side, columns=("value",), show="tree headings")
        self.info.heading("#0", text="项目")
        self.info.heading("value", text="数值")
        self.info.column("#0", width=180, stretch=True)
        self.info.column("value", width=250, stretch=True)
        self.info.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(side, orient="vertical", command=self.info.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.info.configure(yscrollcommand=scroll.set)

        note = (
            "车轮优先使用 telemetry；超出共享内存上限的轮位从游戏 PMG locator 补齐。"
            "轴数与车身轮廓来自游戏定义和碰撞模型，黄色轮胎按实时转角旋转。"
        )
        ttk.Label(self.root, text=note, padding=(10, 0, 10, 10), wraplength=1220).grid(row=2, column=0, sticky="ew")

    def _bind_shortcuts(self) -> None:
        self.root.bind("<space>", lambda _event: self.toggle_pause())
        self.root.bind("<F5>", lambda _event: self.force_refresh())

    def _tick(self) -> None:
        if not self.paused:
            self.force_refresh(show_errors=False)
        self.root.after(self.refresh_ms, self._tick)

    def force_refresh(self, show_errors: bool = True) -> None:
        try:
            snapshot = self.reader.read(include_trailers=True)
            if not self._accept_configuration_snapshot(snapshot):
                self.status_var.set(
                    f"状态：检测到车辆配置变化，正在同步 | {self._configuration_label(snapshot)}"
                )
                return
            self._auto_import_current_resources()
            self.last_units = self._build_layouts()
            game = self.data.get("scsValues", {}).get("game", "unknown")
            truck = self.data.get("configString", {}).get("truckName") or "未知车辆"
            trailers = len(attached_trailers(self.data))
            angle = self._articulation_text()
            self.status_var.set(
                f"状态：实时同步 #{self.configuration_generation} | 游戏 {game} | {truck} | "
                f"已连接挂车 {trailers} | 车头-挂车水平夹角 {angle}"
            )
            missing_resources = self._missing_resource_ids()
            if missing_resources:
                self.status_var.set(
                    f"状态：缺少游戏资源 {', '.join(missing_resources)} | 点击“导入当前车型资源”"
                )
            self.redraw()
            self._refresh_info()
        except SharedMemoryUnavailable as exc:
            self.status_var.set("状态：未连接遥测共享内存，请启动 ETS2/ATS 并确认 telemetry DLL 已安装")
            self.last_units = []
            self.redraw()
            if show_errors:
                messagebox.showwarning("车辆组合几何可视化", str(exc))
        except Exception as exc:
            self.status_var.set(f"状态：读取失败 - {exc}")
            if show_errors:
                messagebox.showerror("车辆组合几何可视化", str(exc))

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        self.status_var.set("状态：已暂停刷新" if self.paused else "状态：继续实时刷新")
        if not self.paused:
            self.force_refresh(show_errors=False)

    def reload_dimensions(self) -> None:
        self.dimensions = self._load_dimensions()
        self.geometry_overrides = self._load_geometry_overrides()
        self.game_geometry.clear_cache()
        self.force_refresh(show_errors=False)
        self.status_var.set("状态：已重新读取游戏碰撞轮廓")

    def _missing_resource_ids(self) -> list[str]:
        if not self.data:
            return []
        missing: list[str] = []
        truck_id = str(self.data.get("configString", {}).get("truckId") or "")
        if truck_id and not self.game_geometry.truck_definition_candidates(truck_id).get("chassis"):
            missing.append(truck_id)
        for trailer in attached_trailers(self.data):
            trailer_id = str(trailer.get("conString", {}).get("id") or "")
            candidates = self.game_geometry.trailer_definition_candidates(trailer_id)
            if trailer_id and not candidates.get("chassis"):
                missing.append(trailer_id)
        return list(dict.fromkeys(missing))

    def _auto_import_current_resources(self) -> None:
        missing = self._missing_resource_ids()
        signature = tuple(missing)
        attempts = getattr(self, "resource_sync_attempts", set())
        if not missing or signature in attempts:
            return
        attempts.add(signature)
        self.resource_sync_attempts = attempts
        game_dir = find_game()
        if game_dir is None or not DEFAULT_EXTRACTOR.is_file():
            return
        archives = [best_archive(game_dir, vehicle_id) for vehicle_id in missing]
        if any(archive is None for archive in archives):
            return
        self.status_var.set(f"状态：正在自动导入车型资源 | {', '.join(missing)}")
        self.root.update_idletasks()
        try:
            import_vehicle_archives(archive for archive in archives if archive is not None)
        except (OSError, ValueError, subprocess.SubprocessError):
            return
        self.game_geometry.clear_cache()

    def import_current_vehicle_resources(self) -> None:
        missing = self._missing_resource_ids()
        if not missing:
            messagebox.showinfo("导入当前车型资源", "当前车头和挂车的游戏定义已经存在。")
            return
        game_dir = find_game()
        suggested: list[Path] = []
        if game_dir is not None:
            suggested = [best_archive(game_dir, vehicle_id) for vehicle_id in missing]
            suggested = [archive for archive in suggested if archive is not None]
        if len(suggested) < len(missing):
            candidate_text = []
            if game_dir is not None:
                for vehicle_id in missing:
                    names = [path.name for _, path in archive_candidates(game_dir, vehicle_id)[:3]]
                    candidate_text.append(f"{vehicle_id}: {', '.join(names) or '无自动候选'}")
            selected = filedialog.askopenfilenames(
                title="选择包含当前车型的 DLC 或模组 .scs 文件",
                initialdir=str(game_dir or Path.home()),
                filetypes=(("SCS 资源包", "*.scs"), ("所有文件", "*.*")),
            )
            if not selected:
                if candidate_text:
                    messagebox.showinfo("未导入资源", "\n".join(candidate_text))
                return
            suggested.extend(Path(path) for path in selected)
        archives = list(dict.fromkeys(suggested))
        details = "\n".join(path.name for path in archives)
        if not messagebox.askyesno(
            "导入当前车型资源",
            f"缺少车型：\n{chr(10).join(missing)}\n\n将导入：\n{details}\n\n是否继续？",
        ):
            return
        self.status_var.set("状态：正在导入当前车型资源，请稍候")
        self.root.update_idletasks()
        try:
            imported = import_vehicle_archives(archives)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            messagebox.showerror("导入当前车型资源", str(exc))
            return
        self.resource_sync_attempts.clear()
        self.reload_dimensions()
        messagebox.showinfo("导入完成", f"已导入：{', '.join(imported)}")

    def open_geometry_config(self) -> None:
        if not self.data:
            messagebox.showwarning("车型配置", "当前没有可用的车辆数据")
            return
        window = tk.Toplevel(self.root)
        window.title("车型配置管理")
        window.geometry("920x560")
        window.minsize(760, 460)
        window.transient(self.root)

        notebook = ttk.Notebook(window)
        notebook.pack(fill="both", expand=True, padx=10, pady=(10, 6))
        controls: list[dict[str, Any]] = []

        truck_id = str(self.data.get("configString", {}).get("truckId") or "")
        truck_candidates = self.game_geometry.truck_definition_candidates(truck_id)
        self._add_geometry_config_tab(
            notebook,
            controls,
            "车头",
            "truck",
            0,
            truck_id or "未知车头",
            truck_candidates,
            (("chassis", "底盘定义"), ("cabin", "驾驶室定义")),
            self.last_units[0] if self.last_units else None,
        )

        trailers = attached_trailers(self.data)
        for index, trailer in enumerate(trailers):
            strings = trailer.get("conString", {})
            trailer_id = str(strings.get("id") or "")
            identity = " | ".join(filter(None, (
                trailer_id,
                str(strings.get("bodyType") or ""),
                str(strings.get("chainType") or ""),
            ))) or f"挂车 {index + 1}"
            unit = self.last_units[index + 1] if index + 1 < len(self.last_units) else None
            self._add_geometry_config_tab(
                notebook,
                controls,
                f"挂车 {index + 1}",
                "trailer",
                index,
                identity,
                self.game_geometry.trailer_definition_candidates(trailer_id),
                (("chassis", "挂车底盘定义"), ("body", "挂车车身定义")),
                unit,
            )

        buttons = ttk.Frame(window, padding=(10, 4, 10, 10))
        buttons.pack(fill="x")
        ttk.Button(
            buttons,
            text="恢复自动并保存",
            command=lambda: (
                self._reset_geometry_config_controls(controls),
                self._apply_geometry_config_controls(controls, window, True),
            ),
        ).pack(side="left")
        ttk.Button(buttons, text="导入当前车型资源", command=self.import_current_vehicle_resources).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(buttons, text="关闭", command=window.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(
            buttons,
            text="保存并应用",
            command=lambda: self._apply_geometry_config_controls(controls, window, True),
        ).pack(side="right", padx=(6, 0))
        ttk.Button(
            buttons,
            text="应用预览",
            command=lambda: self._apply_geometry_config_controls(controls, window, False),
        ).pack(side="right")

    def _add_geometry_config_tab(
        self,
        notebook: ttk.Notebook,
        controls: list[dict[str, Any]],
        title: str,
        kind: str,
        index: int,
        identity: str,
        candidates: dict[str, list[str]],
        fields: tuple[tuple[str, str], ...],
        unit: UnitLayout | None,
    ) -> None:
        frame = ttk.Frame(notebook, padding=14)
        notebook.add(frame, text=title)
        frame.columnconfigure(1, weight=1)
        key = self._geometry_override_key(kind, index)
        entry = self.geometry_overrides.get(kind, {}).get(key, {})
        selection = entry.get("selection", {}) if isinstance(entry, dict) else {}

        ttk.Label(frame, text="当前类型", font=("Microsoft YaHei UI", 10, "bold")).grid(
            row=0, column=0, sticky="nw", padx=(0, 10), pady=(0, 10)
        )
        ttk.Label(frame, text=identity, wraplength=680).grid(row=0, column=1, sticky="nw", pady=(0, 10))
        current = "\n".join(unit.definition_paths) if unit and unit.definition_paths else "未找到游戏定义"
        ttk.Label(frame, text="当前解析结果").grid(row=1, column=0, sticky="nw", padx=(0, 10), pady=(0, 12))
        ttk.Label(frame, text=current, wraplength=680).grid(row=1, column=1, sticky="nw", pady=(0, 12))

        variables: dict[str, tk.StringVar] = {}
        row = 2
        for field, label in fields:
            values = [AUTO_SELECTION, *candidates.get(field, [])]
            selected = str(selection.get(field) or AUTO_SELECTION)
            if selected not in values:
                selected = AUTO_SELECTION
            variable = tk.StringVar(value=selected)
            variables[field] = variable
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=5)
            combo = ttk.Combobox(frame, textvariable=variable, values=values, state="readonly")
            combo.grid(row=row, column=1, sticky="ew", pady=5)
            row += 1

        ttk.Label(frame, text="配置签名").grid(row=row, column=0, sticky="nw", padx=(0, 10), pady=(14, 0))
        ttk.Label(frame, text=key).grid(row=row, column=1, sticky="nw", pady=(14, 0))
        controls.append({
            "kind": kind,
            "index": index,
            "key": key,
            "label": identity,
            "variables": variables,
        })

    def _reset_geometry_config_controls(self, controls: list[dict[str, Any]]) -> None:
        for control in controls:
            for variable in control["variables"].values():
                variable.set(AUTO_SELECTION)

    def _apply_geometry_config_controls(
        self,
        controls: list[dict[str, Any]],
        window: tk.Toplevel,
        save: bool,
    ) -> None:
        for control in controls:
            kind = control["kind"]
            key = control["key"]
            bucket = self.geometry_overrides.setdefault(kind, {})
            selection = {
                field: variable.get()
                for field, variable in control["variables"].items()
                if variable.get() != AUTO_SELECTION
            }
            if selection:
                bucket[key] = {"label": control["label"], "selection": selection}
            else:
                bucket.pop(key, None)
        if save:
            try:
                GEOMETRY_OVERRIDES_FILE.write_text(
                    json.dumps(self.geometry_overrides, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            except OSError as exc:
                messagebox.showerror("车型配置", f"无法保存车型配置：{exc}")
                return
        self.game_geometry.clear_cache()
        self.force_refresh(show_errors=False)
        if save:
            window.destroy()
            self.status_var.set(f"状态：车型配置已保存到 {GEOMETRY_OVERRIDES_FILE.name}")
        else:
            self.status_var.set("状态：已应用车型配置预览")

    def _geometry_override_key(self, kind: str, index: int = 0) -> str:
        signature = self._configuration_signature(self.data)
        if kind == "truck":
            value = signature[0]
        else:
            trailers = signature[1]
            value = trailers[index] if index < len(trailers) else ("missing", index)
        digest = hashlib.sha256(repr(value).encode("utf-8")).hexdigest()[:16]
        return f"{kind}-{digest}"

    def _geometry_selection(self, kind: str, index: int = 0) -> dict[str, str] | None:
        overrides = getattr(self, "geometry_overrides", {})
        key = self._geometry_override_key(kind, index)
        entry = overrides.get(kind, {}).get(key, {})
        selection = entry.get("selection") if isinstance(entry, dict) else None
        return selection if isinstance(selection, dict) else None

    def _load_geometry_overrides(self) -> dict[str, Any]:
        default = {"version": 1, "truck": {}, "trailer": {}}
        if not GEOMETRY_OVERRIDES_FILE.is_file():
            return default
        try:
            data = json.loads(GEOMETRY_OVERRIDES_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default
        if not isinstance(data, dict):
            return default
        data.setdefault("version", 1)
        data.setdefault("truck", {})
        data.setdefault("trailer", {})
        return data

    def _accept_configuration_snapshot(self, snapshot: dict[str, Any]) -> bool:
        signature = self._configuration_signature(snapshot)
        if self.configuration_signature is None:
            self.configuration_signature = signature
            self.configuration_generation = 1
            self.data = snapshot
            return True
        if signature == self.configuration_signature:
            self.pending_configuration_signature = None
            self.pending_configuration_frames = 0
            self.data = snapshot
            return True
        if signature != self.pending_configuration_signature:
            self.pending_configuration_signature = signature
            self.pending_configuration_frames = 1
            return False
        self.pending_configuration_frames += 1
        if self.pending_configuration_frames < 2:
            return False

        self.configuration_signature = signature
        self.pending_configuration_signature = None
        self.pending_configuration_frames = 0
        self.configuration_generation += 1
        self.game_geometry.clear_cache()
        self.data = snapshot
        return True

    def _configuration_signature(self, data: dict[str, Any]) -> tuple[Any, ...]:
        config_strings = data.get("configString", {})
        config_ui = data.get("configUI", {})
        config_vectors = data.get("configVector", {})
        config_bools = data.get("configBool", {})
        truck_count = self._safe_int(config_ui.get("truckWheelCount"), 0)
        truck = (
            str(config_strings.get("truckId") or ""),
            str(config_strings.get("truckName") or ""),
            truck_count,
            self._wheel_configuration_signature(
                config_vectors.get("truckWheelPositionX"),
                config_vectors.get("truckWheelPositionZ"),
                config_bools.get("truckWheelSteerable"),
                config_bools.get("truckWheelPowered"),
                config_bools.get("truckWheelLiftable"),
                config_bools.get("truckWheelSimulated"),
                truck_count,
            ),
            self._rounded_point(config_vectors.get("truckHookPositionX"), config_vectors.get("truckHookPositionZ")),
        )
        trailers = []
        for trailer in attached_trailers(data):
            strings = trailer.get("conString", {})
            vectors = trailer.get("conVector", {})
            booleans = trailer.get("conBool", {})
            count = self._safe_int(trailer.get("conUI", {}).get("wheelCount"), 0)
            trailers.append((
                str(strings.get("id") or ""),
                str(strings.get("bodyType") or ""),
                str(strings.get("chainType") or ""),
                str(strings.get("name") or ""),
                count,
                self._wheel_configuration_signature(
                    vectors.get("wheelPositionX"),
                    vectors.get("wheelPositionZ"),
                    booleans.get("wheelSteerable"),
                    booleans.get("wheelPowered"),
                    booleans.get("wheelLiftable"),
                    booleans.get("wheelSimulated"),
                    count,
                ),
                self._rounded_point(vectors.get("hookPositionX"), vectors.get("hookPositionZ")),
            ))
        return truck, tuple(trailers)

    def _wheel_configuration_signature(
        self,
        xs: Any,
        zs: Any,
        steerables: Any,
        powereds: Any,
        liftables: Any,
        simulateds: Any,
        count: int,
    ) -> tuple[tuple[Any, ...], ...]:
        if not isinstance(xs, list) or not isinstance(zs, list):
            return ()
        limit = max(0, min(count, len(xs), len(zs), 16))
        result = []
        for index in range(limit):
            simulated = self._array_bool(simulateds, index, True)
            if not simulated:
                continue
            result.append((
                round(float(xs[index]), 3),
                round(float(zs[index]), 3),
                self._array_bool(steerables, index),
                self._array_bool(powereds, index),
                self._array_bool(liftables, index),
            ))
        return tuple(result)

    def _array_bool(self, values: Any, index: int, default: bool = False) -> bool:
        return bool(values[index]) if isinstance(values, list) and index < len(values) else default

    def _rounded_point(self, x_value: Any, z_value: Any) -> tuple[float, float] | None:
        point = self._optional_point(x_value, z_value)
        return None if point is None else (round(point[0], 3), round(point[1], 3))

    def _configuration_label(self, data: dict[str, Any]) -> str:
        truck = data.get("configString", {}).get("truckId") or "未知车头"
        trailers = attached_trailers(data)
        if not trailers:
            return f"车头 {truck} | 无挂车"
        labels = []
        for trailer in trailers:
            strings = trailer.get("conString", {})
            labels.append(str(strings.get("id") or strings.get("name") or "未知挂车"))
        return f"车头 {truck} | 挂车 {', '.join(labels)}"

    def _build_layouts(self) -> list[UnitLayout]:
        truck = self._truck_layout()
        if truck is None:
            return []

        units = [truck]
        coupling_world = self._unit_hook_world(truck)
        for index, trailer_data in enumerate(attached_trailers(self.data)):
            trailer = self._trailer_layout(index, trailer_data)
            if coupling_world is not None and trailer.hook is not None:
                trailer.pose = self._pose_with_local_point_at_world(trailer.pose, trailer.hook, coupling_world)
                trailer.aligned_to_coupling = True
            units.append(trailer)
        return units

    def _truck_layout(self) -> UnitLayout | None:
        placement = self.data.get("truckPlacement", {})
        if not placement:
            return None
        pose = Pose(
            x=float(placement.get("coordinateX", 0.0)),
            z=float(placement.get("coordinateZ", 0.0)),
            yaw=scs_rotation_to_radians(float(placement.get("rotationX", 0.0))),
        )
        config = self.data.get("configVector", {})
        count = self._safe_int(self.data.get("configUI", {}).get("truckWheelCount"), 0)
        wheels = self._wheel_points(
            config.get("truckWheelPositionX"),
            config.get("truckWheelPositionZ"),
            self.data.get("truckFloat", {}).get("truck_wheelSteering"),
            self.data.get("truckFloat", {}).get("truck_wheelVelocity"),
            self.data.get("configBool", {}).get("truckWheelSteerable"),
            self.data.get("configFloat", {}).get("truckWheelRadius"),
            self.data.get("configBool", {}).get("truckWheelPowered"),
            self.data.get("configBool", {}).get("truckWheelLiftable"),
            self.data.get("configBool", {}).get("truckWheelSimulated"),
            count,
        )
        telemetry_wheel_count = len(wheels)
        hook = self._optional_point(config.get("truckHookPositionX"), config.get("truckHookPositionZ"))
        key = self._truck_dimension_key()
        game_contour = (
            self.game_geometry.truck_contour(
                key,
                wheels,
                len(self._axle_groups(wheels)),
                self._geometry_selection("truck"),
            )
            if self.game_geometry.available else None
        )
        wheels = self._merge_model_wheels(wheels, game_contour, count)
        bounds, width, length, source, body_bounds, contour, model_paths = self._bounds_for("truck", key, wheels, hook, game_contour)
        return UnitLayout(
            "车头", "truck", key, pose, Pose(pose.x, pose.z, pose.yaw),
            bounds, width, length, source, wheels, hook, "#25364a", "#6fa8dc",
            body_bounds, contour, model_paths,
            game_contour.definition_paths if game_contour is not None else (),
            self._front_sign(wheels, hook, "truck"),
            reported_wheel_count=count,
            telemetry_wheel_count=telemetry_wheel_count,
            game_axle_count=game_contour.axle_count if game_contour is not None else 0,
        )

    def _trailer_layout(self, index: int, trailer: dict[str, Any]) -> UnitLayout:
        doubles = trailer.get("comDouble", {})
        pose = Pose(
            x=float(doubles.get("worldX", 0.0)),
            z=float(doubles.get("worldZ", 0.0)),
            yaw=scs_rotation_to_radians(float(doubles.get("rotationX", 0.0))),
        )
        vectors = trailer.get("conVector", {})
        count = self._safe_int(trailer.get("conUI", {}).get("wheelCount"), 0)
        wheels = self._wheel_points(
            vectors.get("wheelPositionX"),
            vectors.get("wheelPositionZ"),
            trailer.get("comFloat", {}).get("wheelSteering"),
            trailer.get("comFloat", {}).get("wheelVelocity"),
            trailer.get("conBool", {}).get("wheelSteerable"),
            trailer.get("conFloat", {}).get("wheelRadius"),
            trailer.get("conBool", {}).get("wheelPowered"),
            trailer.get("conBool", {}).get("wheelLiftable"),
            trailer.get("conBool", {}).get("wheelSimulated"),
            count,
        )
        telemetry_wheel_count = len(wheels)
        hook = self._optional_point(vectors.get("hookPositionX"), vectors.get("hookPositionZ"))
        key = self._trailer_dimension_key(trailer)
        strings = trailer.get("conString", {})
        game_contour = None
        if self.game_geometry.available:
            game_contour = self.game_geometry.trailer_contour(
                str(strings.get("id") or ""),
                str(strings.get("bodyType") or ""),
                str(strings.get("chainType") or ""),
                wheels,
                hook,
                len(self._axle_groups(wheels)),
                self._geometry_selection("trailer", index),
            )
        wheels = self._merge_model_wheels(wheels, game_contour, count)
        bounds, width, length, source, body_bounds, contour, model_paths = self._bounds_for(
            "trailer", key, wheels, hook, game_contour
        )
        name = trailer.get("conString", {}).get("name") or f"挂车 {index + 1}"
        return UnitLayout(
            name, "trailer", key, pose, Pose(pose.x, pose.z, pose.yaw),
            bounds, width, length, source, wheels, hook, "#3b3322", "#d6a84f",
            body_bounds, contour, model_paths,
            game_contour.definition_paths if game_contour is not None else (),
            self._front_sign(wheels, hook, "trailer"),
            reported_wheel_count=count,
            telemetry_wheel_count=telemetry_wheel_count,
            game_axle_count=game_contour.axle_count if game_contour is not None else 0,
        )

    def _wheel_points(
        self,
        xs: Any,
        zs: Any,
        steerings: Any,
        velocities: Any,
        steerables: Any,
        radii: Any,
        powereds: Any,
        liftables: Any,
        simulateds: Any,
        count: int,
    ) -> list[WheelLayout]:
        if not isinstance(xs, list) or not isinstance(zs, list):
            return []
        count = max(0, min(count, len(xs), len(zs), 16))
        wheels = []
        for index in range(count):
            if isinstance(simulateds, list) and index < len(simulateds) and not bool(simulateds[index]):
                continue
            x = float(xs[index])
            z = float(zs[index])
            steering = float(steerings[index]) if isinstance(steerings, list) and index < len(steerings) else 0.0
            velocity = float(velocities[index]) if isinstance(velocities, list) and index < len(velocities) else 0.0
            steerable = bool(steerables[index]) if isinstance(steerables, list) and index < len(steerables) else abs(steering) > math.radians(0.25)
            radius = float(radii[index]) if isinstance(radii, list) and index < len(radii) else 0.5
            if not 0.1 <= radius <= 2.0:
                radius = 0.5
            powered = bool(powereds[index]) if isinstance(powereds, list) and index < len(powereds) else False
            liftable = bool(liftables[index]) if isinstance(liftables, list) and index < len(liftables) else False
            wheels.append(WheelLayout(x, z, steering, velocity, steerable, radius, powered, liftable))
        return wheels

    def _merge_model_wheels(
        self, wheels: list[WheelLayout], game_contour: GameContour | None, reported_wheel_count: int
    ) -> list[WheelLayout]:
        if (
            game_contour is None
            or not game_contour.wheel_points
            or reported_wheel_count <= 0
            or len(wheels) >= reported_wheel_count
            or len(game_contour.wheel_points) != reported_wheel_count
        ):
            return wheels
        merged: list[WheelLayout] = []
        unmatched = set(range(len(wheels)))
        default_radius = sum(wheel.radius for wheel in wheels) / len(wheels) if wheels else 0.5
        for model_wheel in game_contour.wheel_points:
            nearest_index = min(
                unmatched,
                key=lambda index: math.hypot(
                    wheels[index].local_x - model_wheel.x, wheels[index].local_z - model_wheel.z
                ),
                default=None,
            )
            if nearest_index is not None:
                nearest_distance = math.hypot(
                    wheels[nearest_index].local_x - model_wheel.x,
                    wheels[nearest_index].local_z - model_wheel.z,
                )
                if nearest_distance <= 0.2:
                    merged.append(wheels[nearest_index])
                    unmatched.remove(nearest_index)
                    continue

            same_side = [
                wheel for wheel in wheels
                if wheel.local_x == 0.0 or model_wheel.x == 0.0 or wheel.local_x * model_wheel.x > 0.0
            ]
            nearest_live = min(same_side or wheels, key=lambda wheel: abs(wheel.local_z - model_wheel.z), default=None)
            merged.append(WheelLayout(
                model_wheel.x,
                model_wheel.z,
                nearest_live.steering if nearest_live is not None and model_wheel.steerable else 0.0,
                nearest_live.velocity if nearest_live is not None else 0.0,
                model_wheel.steerable,
                nearest_live.radius if nearest_live is not None else default_radius,
                model_wheel.powered,
                model_wheel.liftable,
                "游戏 PMG",
            ))
        merged.extend(wheels[index] for index in sorted(unmatched))
        return merged

    def _bounds_for(
        self,
        kind: str,
        key: str,
        wheels: list[WheelLayout],
        hook: tuple[float, float] | None,
        game_contour: GameContour | None,
    ) -> tuple[
        tuple[float, float, float, float],
        float,
        float,
        str,
        tuple[float, float, float, float] | None,
        tuple[tuple[float, float], ...] | None,
        tuple[str, ...],
    ]:
        if game_contour is not None:
            return (
                game_contour.bounds,
                game_contour.width,
                game_contour.length,
                game_contour.source,
                game_contour.bounds,
                game_contour.points,
                game_contour.model_paths,
            )
        spec = self._dimension_spec(kind, key)
        if spec is not None:
            min_x, max_x, min_z, max_z = spec.bounds
            contour = ((min_x, min_z), (max_x, min_z), (max_x, max_z), (min_x, max_z))
            return spec.bounds, spec.width, spec.length, "已验证外形尺寸表", spec.bounds, contour, ()
        bounds, width, length = self._telemetry_bounds(wheels, hook)
        return bounds, width, length, "遥测点位跨度（非车身尺寸）", None, None, ()

    def _telemetry_bounds(
        self,
        wheels: list[WheelLayout],
        hook: tuple[float, float] | None,
    ) -> tuple[tuple[float, float, float, float], float, float]:
        xs = [wheel.local_x for wheel in wheels]
        zs = [wheel.local_z for wheel in wheels]
        if hook is not None:
            xs.append(hook[0])
            zs.append(hook[1])
        if not xs or not zs:
            return (-0.5, 0.5, -0.5, 0.5), 0.0, 0.0
        min_x, max_x = min(xs), max(xs)
        min_z, max_z = min(zs), max(zs)
        point_width = max_x - min_x
        point_length = max_z - min_z
        padding = 0.65
        return (
            min_x - padding,
            max_x + padding,
            min_z - padding,
            max_z + padding,
        ), point_width, point_length

    def _axle_groups(self, wheels: list[WheelLayout], tolerance: float = 0.35) -> list[list[WheelLayout]]:
        groups: list[list[WheelLayout]] = []
        for wheel in sorted(wheels, key=lambda item: item.local_z, reverse=True):
            if not groups:
                groups.append([wheel])
                continue
            mean_z = sum(item.local_z for item in groups[-1]) / len(groups[-1])
            if abs(wheel.local_z - mean_z) <= tolerance:
                groups[-1].append(wheel)
            else:
                groups.append([wheel])
        return groups

    def _front_sign(self, wheels: list[WheelLayout], hook: tuple[float, float] | None, kind: str) -> float:
        if kind == "trailer" and hook is not None and wheels:
            wheel_z = sum(wheel.local_z for wheel in wheels) / len(wheels)
            return 1.0 if hook[1] > wheel_z else -1.0
        steerable = [wheel.local_z for wheel in wheels if wheel.steerable]
        fixed = [wheel.local_z for wheel in wheels if not wheel.steerable]
        if steerable and fixed:
            return 1.0 if sum(steerable) / len(steerable) > sum(fixed) / len(fixed) else -1.0
        return -1.0

    def redraw(self) -> None:
        self.canvas.delete("all")
        width = max(self.canvas.winfo_width(), 10)
        height = max(self.canvas.winfo_height(), 10)
        self._draw_grid(width, height)
        if not self.last_units:
            self.canvas.create_text(width / 2, height / 2, text="等待 telemetry 数据", fill="#d8dee9", font=("Microsoft YaHei UI", 16))
            return
        to_canvas = self._viewport(width, height)
        for unit in reversed(self.last_units):
            self._draw_unit(to_canvas, unit)
        self._draw_coupling_marker(to_canvas)
        self._draw_legend(width)

    def _viewport(self, width: int, height: int):
        points: list[tuple[float, float]] = []
        for unit in self.last_units:
            min_x, max_x, min_z, max_z = unit.bounds
            for local in ((min_x, min_z), (min_x, max_z), (max_x, min_z), (max_x, max_z)):
                points.append(self._local_to_world(unit.pose, *local))
            for wheel in unit.wheels:
                points.append(self._local_to_world(unit.pose, wheel.local_x, wheel.local_z))
            if unit.hook:
                points.append(self._local_to_world(unit.pose, *unit.hook))
        min_x = min(point[0] for point in points)
        max_x = max(point[0] for point in points)
        min_z = min(point[1] for point in points)
        max_z = max(point[1] for point in points)
        margin = 42
        span_x = max(max_x - min_x, 1.0)
        span_z = max(max_z - min_z, 1.0)
        scale = min((width - margin * 2) / span_x, (height - margin * 2) / span_z)
        center_x = (min_x + max_x) / 2.0
        center_z = (min_z + max_z) / 2.0

        def convert(x: float, z: float) -> tuple[float, float]:
            return width / 2.0 + (x - center_x) * scale, height / 2.0 - (z - center_z) * scale

        return convert

    def _draw_unit(self, to_canvas, unit: UnitLayout) -> None:
        if unit.body_bounds is not None:
            self._draw_verified_body(to_canvas, unit)
        self._draw_skeleton(to_canvas, unit)
        self._draw_front_rear(to_canvas, unit)
        bounds = unit.body_bounds or unit.bounds
        label_local = ((bounds[0] + bounds[1]) / 2.0, (bounds[2] + bounds[3]) / 2.0)
        cx, cy = to_canvas(*self._local_to_world(unit.pose, *label_local))
        self.canvas.create_text(cx + 10, cy - 12, text=unit.name, fill="#f5f7fa", anchor="w", font=("Microsoft YaHei UI", 10, "bold"))
        for index, wheel in enumerate(unit.wheels):
            self._draw_wheel(to_canvas, unit.pose, wheel, index, unit.front_sign)
        if unit.hook:
            label = "" if getattr(self, "compact_labels", False) else ("鞍座" if unit.kind == "truck" else "挂点")
            color = "#66d9ef" if unit.kind == "truck" else "#ffb86c"
            self._draw_marker(to_canvas, unit.pose, unit.hook, color, label)

    def _draw_verified_body(self, to_canvas, unit: UnitLayout) -> None:
        assert unit.body_contour is not None
        world_points = [self._local_to_world(unit.pose, x, z) for x, z in unit.body_contour]
        flat = [coord for point in world_points for coord in to_canvas(*point)]
        self.canvas.create_polygon(flat, fill=unit.color, outline=unit.outline, width=3)

    def _draw_skeleton(self, to_canvas, unit: UnitLayout) -> None:
        points = [(wheel.local_x, wheel.local_z) for wheel in unit.wheels]
        if unit.hook is not None:
            points.append(unit.hook)
        if not points:
            points.append((0.0, 0.0))
        min_z = min(point[1] for point in points)
        max_z = max(point[1] for point in points)
        start = to_canvas(*self._local_to_world(unit.pose, 0.0, min_z))
        end = to_canvas(*self._local_to_world(unit.pose, 0.0, max_z))
        self.canvas.create_line(*start, *end, fill=unit.outline, width=3, dash=(7, 4))

        for axle_index, axle in enumerate(self._axle_groups(unit.wheels), start=1):
            axle_z = sum(wheel.local_z for wheel in axle) / len(axle)
            min_x = min(wheel.local_x for wheel in axle)
            max_x = max(wheel.local_x for wheel in axle)
            if math.isclose(min_x, max_x, abs_tol=1e-4):
                min_x -= 0.35
                max_x += 0.35
            left = to_canvas(*self._local_to_world(unit.pose, min_x, axle_z))
            right = to_canvas(*self._local_to_world(unit.pose, max_x, axle_z))
            self.canvas.create_line(*left, *right, fill=unit.outline, width=2)
            if not getattr(self, "compact_labels", False):
                center = to_canvas(*self._local_to_world(unit.pose, 0.0, axle_z))
                self.canvas.create_text(center[0] + 7, center[1] + 7, text=f"轴{axle_index}", fill="#88929d", anchor="nw", font=("Microsoft YaHei UI", 8))

        origin_x, origin_y = to_canvas(unit.pose.x, unit.pose.z)
        self.canvas.create_line(origin_x - 4, origin_y, origin_x + 4, origin_y, fill="#8b949e")
        self.canvas.create_line(origin_x, origin_y - 4, origin_x, origin_y + 4, fill="#8b949e")

    def _draw_front_rear(self, to_canvas, unit: UnitLayout) -> None:
        bounds = unit.body_bounds or unit.bounds
        _, _, min_z, max_z = bounds
        front_z = max_z if unit.front_sign > 0 else min_z
        rear_z = min_z if unit.front_sign > 0 else max_z
        front_mid = self._local_to_world(unit.pose, 0.0, front_z + unit.front_sign * 0.25)
        rear_mid = self._local_to_world(unit.pose, 0.0, rear_z - unit.front_sign * 0.15)
        center = self._local_to_world(unit.pose, 0.0, front_z - unit.front_sign * 0.45)
        fx, fy = to_canvas(*front_mid)
        rx, ry = to_canvas(*rear_mid)
        cx, cy = to_canvas(*center)
        self.canvas.create_line(cx, cy, fx, fy, fill="#ffffff", arrow=tk.LAST, width=2)
        self.canvas.create_text(fx, fy - 11, text="前", fill="#ffffff", font=("Microsoft YaHei UI", 10, "bold"))
        self.canvas.create_text(rx, ry + 11, text="后", fill="#aeb7c2", font=("Microsoft YaHei UI", 10, "bold"))

    def _draw_wheel(self, to_canvas, pose: Pose, wheel: WheelLayout, index: int, front_sign: float) -> None:
        wx, wz = self._local_to_world(pose, wheel.local_x, wheel.local_z)
        color = "#f6d365" if wheel.steerable else ("#8bd17c" if abs(wheel.velocity) > 0.01 else "#d8dee9")
        heading = pose.yaw + wheel.steering + (math.pi if front_sign < 0 else 0.0)
        tire_half_length = max(0.34, min(0.58, wheel.radius * 0.85))
        corners = [to_canvas(*point) for point in self._wheel_corners_world(pose, wheel, front_sign)]
        flat = [coordinate for point in corners for coordinate in point]
        self.canvas.create_polygon(flat, fill=color, outline="#101418", width=2 if wheel.steerable else 1)

        front = self._rotate_local(heading, 0.0, tire_half_length * 0.8)
        rear = self._rotate_local(heading, 0.0, -tire_half_length * 0.8)
        front_canvas = to_canvas(wx + front[0], wz + front[1])
        rear_canvas = to_canvas(wx + rear[0], wz + rear[1])
        self.canvas.create_line(*rear_canvas, *front_canvas, fill="#101418", width=2, arrow=tk.LAST if wheel.steerable else tk.NONE)

    def _wheel_corners_world(self, pose: Pose, wheel: WheelLayout, front_sign: float = 1.0) -> list[tuple[float, float]]:
        wx, wz = self._local_to_world(pose, wheel.local_x, wheel.local_z)
        heading = pose.yaw + wheel.steering + (math.pi if front_sign < 0 else 0.0)
        tire_half_length = max(0.34, min(0.58, wheel.radius * 0.85))
        tire_half_width = 0.16
        corners: list[tuple[float, float]] = []
        for lateral, longitudinal in (
            (-tire_half_width, -tire_half_length),
            (tire_half_width, -tire_half_length),
            (tire_half_width, tire_half_length),
            (-tire_half_width, tire_half_length),
        ):
            offset_x, offset_z = self._rotate_local(heading, lateral, longitudinal)
            corners.append((wx + offset_x, wz + offset_z))
        return corners

    def _draw_marker(self, to_canvas, pose: Pose, local: tuple[float, float], color: str, label: str) -> None:
        wx, wz = self._local_to_world(pose, *local)
        x, y = to_canvas(wx, wz)
        self.canvas.create_polygon(x, y - 7, x + 7, y, x, y + 7, x - 7, y, fill=color, outline="#101418")
        if label:
            self.canvas.create_text(x + 16, y, text=label, fill=color, anchor="w", font=("Microsoft YaHei UI", 9))

    def _draw_coupling_marker(self, to_canvas) -> None:
        if len(self.last_units) < 2:
            return
        truck_hook = self._unit_hook_world(self.last_units[0])
        if truck_hook is None:
            return
        x, y = to_canvas(*truck_hook)
        self.canvas.create_oval(x - 10, y - 10, x + 10, y + 10, outline="#f7768e", width=2)
        self.canvas.create_text(x, y + 20, text="铰接点", fill="#f7768e", font=("Microsoft YaHei UI", 9, "bold"))

    def _draw_grid(self, width: int, height: int) -> None:
        step = 48
        for x in range(0, width, step):
            self.canvas.create_line(x, 0, x, height, fill="#1d252d")
        for y in range(0, height, step):
            self.canvas.create_line(0, y, width, y, fill="#1d252d")

    def _draw_legend(self, width: int) -> None:
        text = "实线外沿=游戏碰撞轮廓 | 横线=真实车轴 | 黄轮胎=可转向 | 蓝色=鞍座 | 橙色=挂点"
        self.canvas.create_text(width / 2, 20, text=text, fill="#c7d0d9", font=("Microsoft YaHei UI", 10))

    def _refresh_info(self) -> None:
        self.info.delete(*self.info.get_children())
        for unit in self.last_units:
            visible_axle_count = len(self._axle_groups(unit.wheels))
            axle_count = unit.game_axle_count or visible_axle_count
            if unit.has_verified_body:
                summary = f"车身 {unit.length:.2f} m x {unit.width:.2f} m | {axle_count} 轴"
            else:
                summary = f"真实遥测点位 | {len(unit.wheels)} 轮 / {axle_count} 轴"
            parent = self.info.insert("", "end", text=unit.name, values=(summary,), open=True)
            self.info.insert(parent, "end", text="尺寸键", values=(unit.dimension_key,))
            self.info.insert(parent, "end", text="尺寸来源", values=(unit.dimensions_source,))
            if unit.has_verified_body:
                self.info.insert(parent, "end", text="碰撞轮廓长宽", values=(f"{unit.length:.3f} m x {unit.width:.3f} m",))
                self.info.insert(parent, "end", text="轮廓点数量", values=(str(len(unit.body_contour or ())),))
                for path in unit.definition_paths:
                    self.info.insert(parent, "end", text="游戏定义", values=(path,))
                for path in unit.model_paths:
                    self.info.insert(parent, "end", text="游戏碰撞资源", values=(path,))
            else:
                self.info.insert(parent, "end", text="车身轮廓", values=("未找到匹配的游戏碰撞模型",))
                self.info.insert(parent, "end", text="遥测点位纵向跨度", values=(f"{unit.length:.3f} m（不是车身长度）",))
                self.info.insert(parent, "end", text="遥测点位横向跨度", values=(f"{unit.width:.3f} m（不是车身宽度）",))
            self.info.insert(parent, "end", text="绘制坐标", values=(f"X {unit.pose.x:.3f}, Z {unit.pose.z:.3f}",))
            self.info.insert(parent, "end", text="Telemetry 坐标", values=(f"X {unit.telemetry_pose.x:.3f}, Z {unit.telemetry_pose.z:.3f}",))
            self.info.insert(parent, "end", text="水平航向", values=(f"{math.degrees(unit.pose.yaw):+.2f}°",))
            wheel_value = str(len(unit.wheels))
            if unit.reported_wheel_count:
                wheel_value += f"（游戏报告 {unit.reported_wheel_count}，Telemetry 有效 {unit.telemetry_wheel_count}）"
            self.info.insert(parent, "end", text="车轮数量", values=(wheel_value,))
            axle_source = "游戏定义" if unit.game_axle_count else "Telemetry 可见轴组"
            self.info.insert(parent, "end", text="轴组数量", values=(f"{axle_count}（{axle_source}）",))
            steerable_wheels = [wheel for wheel in unit.wheels if wheel.steerable]
            if steerable_wheels:
                self.info.insert(parent, "end", text="可转向轮", values=(f"{len(steerable_wheels)} 个，转角仅在图中显示",))
            wheel_parent = self.info.insert(parent, "end", text="车轮位置（Telemetry / 游戏 PMG）", values=(f"{len(unit.wheels)} 个",))
            for wheel_index, wheel in enumerate(unit.wheels, start=1):
                self.info.insert(
                    wheel_parent,
                    "end",
                    text=f"车轮 {wheel_index}",
                    values=(f"X {wheel.local_x:.3f}, Z {wheel.local_z:.3f} | {wheel.source}",),
                )
            if unit.hook:
                self.info.insert(parent, "end", text="鞍座/挂点本地坐标", values=(f"X {unit.hook[0]:.3f}, Z {unit.hook[1]:.3f}",))
                hook_world = self._unit_hook_world(unit)
                if hook_world:
                    self.info.insert(parent, "end", text="鞍座/挂点世界坐标", values=(f"X {hook_world[0]:.3f}, Z {hook_world[1]:.3f}",))
            if unit.aligned_to_coupling:
                self.info.insert(parent, "end", text="挂点绘制校正", values=("已与车头鞍座重合",))
        self.info.insert("", "end", text="车头-挂车水平夹角", values=(self._articulation_text(),))
        self.info.insert("", "end", text="尺寸表文件", values=(str(DIMENSIONS_FILE),))

    def _articulation_text(self) -> str:
        if len(self.last_units) < 2:
            return "--"
        angle = math.degrees(normalize_angle(self.last_units[0].pose.yaw - self.last_units[1].pose.yaw))
        return f"{angle:+.2f}°"

    def _unit_hook_world(self, unit: UnitLayout) -> tuple[float, float] | None:
        if unit.hook is None:
            return None
        return self._local_to_world(unit.pose, *unit.hook)

    def _pose_with_local_point_at_world(self, pose: Pose, local: tuple[float, float], world: tuple[float, float]) -> Pose:
        local_world_offset = self._rotate_local(pose.yaw, *local)
        return Pose(world[0] - local_world_offset[0], world[1] - local_world_offset[1], pose.yaw)

    def _rotate_local(self, yaw: float, local_x: float, local_z: float) -> tuple[float, float]:
        cos_yaw = math.cos(yaw)
        sin_yaw = math.sin(yaw)
        return local_x * cos_yaw - local_z * sin_yaw, local_x * sin_yaw + local_z * cos_yaw

    def _local_to_world(self, pose: Pose, local_x: float, local_z: float) -> tuple[float, float]:
        offset_x, offset_z = self._rotate_local(pose.yaw, local_x, local_z)
        return pose.x + offset_x, pose.z + offset_z

    def _truck_dimension_key(self) -> str:
        config = self.data.get("configString", {})
        return str(config.get("truckId") or config.get("truckName") or "unknown_truck")

    def _trailer_dimension_key(self, trailer: dict[str, Any]) -> str:
        strings = trailer.get("conString", {})
        parts = [
            strings.get("id"),
            strings.get("bodyType"),
            strings.get("chainType"),
            strings.get("name"),
        ]
        return "|".join(str(part).strip() for part in parts if str(part or "").strip()) or "unknown_trailer"

    def _dimension_spec(self, kind: str, key: str) -> DimensionSpec | None:
        raw = self.dimensions.get(kind, {}).get(key)
        if not isinstance(raw, dict):
            return None
        if raw.get("verified") is not True:
            return None
        try:
            width = float(raw["width"])
            front_z = float(raw["front_z"])
            rear_z = float(raw["rear_z"])
        except (KeyError, TypeError, ValueError):
            return None
        if width <= 0 or front_z <= rear_z:
            return None
        return DimensionSpec(key=key, width=width, front_z=front_z, rear_z=rear_z, source="已验证外形尺寸")

    def _load_dimensions(self) -> dict[str, Any]:
        if not DIMENSIONS_FILE.exists():
            return {"truck": {}, "trailer": {}}
        try:
            data = json.loads(DIMENSIONS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"truck": {}, "trailer": {}}
        if not isinstance(data, dict):
            return {"truck": {}, "trailer": {}}
        data.setdefault("truck", {})
        data.setdefault("trailer", {})
        return data

    def _optional_point(self, x_value: Any, z_value: Any) -> tuple[float, float] | None:
        try:
            x = float(x_value)
            z = float(z_value)
        except (TypeError, ValueError):
            return None
        return x, z

    def _safe_int(self, value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default


def main() -> None:
    root = tk.Tk()
    VehicleGeometryVisualizer(root)
    root.mainloop()

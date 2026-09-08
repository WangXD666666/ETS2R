"""Tkinter app for inspecting live SCS telemetry data."""

from __future__ import annotations

import json
import math
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from automatic_reversing.full_telemetry import FullSCSTelemetryReader
from automatic_reversing.geometry import normalize_angle, scs_rotation_to_radians
from automatic_reversing.scs_sdk import SharedMemoryUnavailable
from automatic_reversing.telemetry import attached_trailers

FieldSpec = tuple[str, tuple[str, ...], str]
GroupSpec = tuple[str, list[FieldSpec]]

FIELD_GROUPS: list[GroupSpec] = [
    ("游戏状态", [("游戏", ("scsValues", "game"), ""), ("是否暂停", ("pause",), "bool"), ("SDK 是否激活", ("sdkActive",), "bool"), ("Telemetry 插件版本", ("scsValues", "telemetryPluginRevision"), ""), ("Telemetry 游戏版本", ("scsValues", "telemetryVersionGameMajor"), "version:scsValues.telemetryVersionGameMinor")]),
    ("车辆配置", [("品牌", ("configString", "truckBrand"), ""), ("车型", ("configString", "truckName"), ""), ("车辆 ID", ("configString", "truckId"), ""), ("车牌", ("configString", "truckLicensePlate"), ""), ("车牌国家", ("configString", "truckLicensePlateCountry"), ""), ("车轮数量", ("configUI", "truckWheelCount"), "count"), ("油箱容量", ("configFloat", "fuelCapacity"), "l"), ("最大转速", ("configFloat", "engineRpmMax"), "rpm"), ("前进挡数量", ("configUI", "gears"), "count"), ("倒挡数量", ("configUI", "gearsReverse"), "count"), ("变速器类型", ("configString", "shifterType"), "")]),
    ("车辆动态", [("速度", ("truckFloat", "speed"), "kmh"), ("发动机转速", ("truckFloat", "engineRpm"), "rpm"), ("气压", ("truckFloat", "airPressure"), "bar"), ("刹车温度", ("truckFloat", "brakeTemperature"), "num"), ("油量", ("truckFloat", "fuel"), "l"), ("尿素", ("truckFloat", "adblue"), "l"), ("机油压力", ("truckFloat", "oilPressure"), "bar"), ("机油温度", ("truckFloat", "oilTemperature"), "deg"), ("水温", ("truckFloat", "waterTemperature"), "deg"), ("电压", ("truckFloat", "batteryVoltage"), "v"), ("限速", ("truckFloat", "speedLimit"), "kmh")]),
    ("输入状态", [("用户方向盘", ("truckFloat", "userSteer"), "ratio"), ("用户油门", ("truckFloat", "userThrottle"), "ratio"), ("用户刹车", ("truckFloat", "userBrake"), "ratio"), ("用户离合", ("truckFloat", "userClutch"), "ratio"), ("游戏实际方向", ("truckFloat", "gameSteer"), "ratio"), ("游戏实际油门", ("truckFloat", "gameThrottle"), "ratio"), ("游戏实际刹车", ("truckFloat", "gameBrake"), "ratio"), ("游戏实际离合", ("truckFloat", "gameClutch"), "ratio"), ("当前挡位", ("truckInt", "gear"), ""), ("仪表挡位", ("truckInt", "gearDashboard"), "")]),
    ("灯光和开关", [("手刹", ("truckBool", "parkBrake"), "bool"), ("发动机制动", ("truckBool", "motorBrake"), "bool"), ("电源开启", ("truckBool", "electricEnabled"), "bool"), ("发动机开启", ("truckBool", "engineEnabled"), "bool"), ("雨刷", ("truckBool", "wipers"), "bool"), ("左转向灯", ("truckBool", "blinkerLeftActive"), "bool"), ("右转向灯", ("truckBool", "blinkerRightActive"), "bool"), ("近光灯", ("truckBool", "lightsBeamLow"), "bool"), ("远光灯", ("truckBool", "lightsBeamHigh"), "bool"), ("刹车灯", ("truckBool", "lightsBrake"), "bool"), ("倒车灯", ("truckBool", "lightsReverse"), "bool"), ("双闪", ("truckBool", "lightsHazard"), "bool")]),
    ("损耗数据", [("发动机磨损", ("truckFloat", "wearEngine"), "percent"), ("变速箱磨损", ("truckFloat", "wearTransmission"), "percent"), ("驾驶室磨损", ("truckFloat", "wearCabin"), "percent"), ("底盘磨损", ("truckFloat", "wearChassis"), "percent"), ("车轮磨损", ("truckFloat", "wearWheels"), "percent"), ("里程表", ("truckFloat", "truckOdometer"), "km")]),
    ("位置姿态", [("车头世界 X", ("truckPlacement", "coordinateX"), "m"), ("车头世界 Y", ("truckPlacement", "coordinateY"), "m"), ("车头世界 Z", ("truckPlacement", "coordinateZ"), "m"), ("车头旋转 X / 水平航向", ("truckPlacement", "rotationX"), "angle"), ("车头旋转 Y / 俯仰参考", ("truckPlacement", "rotationY"), "angle"), ("车头旋转 Z", ("truckPlacement", "rotationZ"), "angle"), ("驾驶室位置 X", ("configVector", "cabinPositionX"), "m"), ("驾驶室位置 Y", ("configVector", "cabinPositionY"), "m"), ("驾驶室位置 Z", ("configVector", "cabinPositionZ"), "m"), ("头部位置 X", ("configVector", "headPositionX"), "m"), ("头部位置 Y", ("configVector", "headPositionY"), "m"), ("头部位置 Z", ("configVector", "headPositionZ"), "m"), ("驾驶室偏移 X", ("headPlacement", "cabinOffsetX"), "m"), ("驾驶室偏移 Y", ("headPlacement", "cabinOffsetY"), "m"), ("驾驶室偏移 Z", ("headPlacement", "cabinOffsetZ"), "m"), ("头部偏移 X", ("headPlacement", "headOffsetX"), "m"), ("头部偏移 Y", ("headPlacement", "headOffsetY"), "m"), ("头部偏移 Z", ("headPlacement", "headOffsetZ"), "m")]),
    ("货运任务", [("货物", ("configString", "cargo"), ""), ("货物 ID", ("configString", "cargoId"), ""), ("货物重量", ("configFloat", "cargoMass"), "kg"), ("是否已装货", ("configBool", "isCargoLoaded"), "bool"), ("起点城市", ("configString", "citySrc"), ""), ("起点公司", ("configString", "compSrc"), ""), ("终点城市", ("configString", "cityDst"), ""), ("终点公司", ("configString", "compDst"), ""), ("任务收入", ("configLongLong", "jobIncome"), "money"), ("计划距离", ("configUI", "plannedDistanceKm"), "km"), ("剩余距离", ("truckFloat", "routeDistance"), "m"), ("剩余时间", ("truckFloat", "routeTime"), "s"), ("当前限速", ("truckFloat", "speedLimit"), "kmh"), ("货损", ("jobFloat", "cargoDamage"), "percent")]),
    ("事件状态", [("正在任务中", ("specialBool", "onJob"), "bool"), ("任务已完成", ("specialBool", "jobFinished"), "bool"), ("任务已取消", ("specialBool", "jobCancelled"), "bool"), ("任务已交付", ("specialBool", "jobDelivered"), "bool"), ("发生罚款", ("specialBool", "fined"), "bool"), ("收费站", ("specialBool", "tollgate"), "bool"), ("渡轮", ("specialBool", "ferry"), "bool"), ("火车", ("specialBool", "train"), "bool"), ("加油", ("specialBool", "refuel"), "bool"), ("加油已付款", ("specialBool", "refuelPayed"), "bool"), ("罚款原因", ("gameplayString", "fineOffence"), ""), ("罚款金额", ("gameplayLongLong", "fineAmount"), "money")]),
    ("原始解析信息", [("当前解析偏移", ("_parser", "offset"), "bytes"), ("共享内存大小", ("_parser", "telemetrySize"), "bytes"), ("是否包含挂车解析", ("_parser", "includeTrailers"), "bool")]),
]


class TelemetryDataViewer:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("车辆数据实时监视器")
        self.root.geometry("1280x820")
        self.root.minsize(1040, 680)
        self.reader = FullSCSTelemetryReader()
        self.data: dict[str, Any] = {}
        self.paused = False
        self.refresh_ms = 250
        self._build_ui()
        self._bind_shortcuts()
        self._tick()

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)
        toolbar = ttk.Frame(self.root, padding=(10, 10, 10, 6))
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.columnconfigure(1, weight=1)
        self.status_var = tk.StringVar(value="状态：等待遥测")
        ttk.Label(toolbar, textvariable=self.status_var).grid(row=0, column=0, padx=(0, 10), sticky="w")
        self.search_var = tk.StringVar()
        search = ttk.Entry(toolbar, textvariable=self.search_var)
        search.grid(row=0, column=1, padx=4, sticky="ew")
        search.bind("<KeyRelease>", lambda _event: self._refresh_views())
        ttk.Button(toolbar, text="清除搜索", command=self._clear_search).grid(row=0, column=2, padx=4)
        ttk.Button(toolbar, text="暂停刷新", command=self.toggle_pause).grid(row=0, column=3, padx=4)
        ttk.Button(toolbar, text="展开全部", command=self.expand_all).grid(row=0, column=4, padx=4)
        ttk.Button(toolbar, text="折叠全部", command=self.collapse_all).grid(row=0, column=5, padx=4)
        ttk.Button(toolbar, text="复制选中", command=self.copy_selected).grid(row=0, column=6, padx=4)
        ttk.Button(toolbar, text="导出 JSON", command=self.export_json).grid(row=0, column=7, padx=4)

        notebook = ttk.Notebook(self.root)
        notebook.grid(row=1, column=0, padx=10, pady=(0, 8), sticky="nsew")
        summary_frame = ttk.Frame(notebook)
        raw_frame = ttk.Frame(notebook)
        notebook.add(summary_frame, text="汉化概要")
        notebook.add(raw_frame, text="原始数据树")
        self.summary_tree = self._create_tree(summary_frame, "分类 / 字段", "原始路径")
        self.raw_tree = self._create_tree(raw_frame, "路径 / 字段", "类型")

        help_text = "此窗口只读取车辆数据，不会写入控制。搜索框会按中文名称、路径和值过滤；空搜索时显示完整数据。"
        ttk.Label(self.root, text=help_text, padding=(10, 0, 10, 10), wraplength=1220).grid(row=2, column=0, sticky="ew")

    def _create_tree(self, parent: ttk.Frame, first_heading: str, third_heading: str) -> ttk.Treeview:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        tree = ttk.Treeview(parent, columns=("value", "path"), show="tree headings")
        tree.heading("#0", text=first_heading)
        tree.heading("value", text="数值")
        tree.heading("path", text=third_heading)
        tree.column("#0", width=360, stretch=True)
        tree.column("value", width=520, stretch=True)
        tree.column("path", width=420, stretch=True)
        tree.grid(row=0, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(parent, orient="horizontal", command=tree.xview)
        x_scroll.grid(row=1, column=0, sticky="ew")
        tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        return tree

    def _bind_shortcuts(self) -> None:
        self.root.bind("<space>", lambda _event: self.toggle_pause())
        self.root.bind("<F5>", lambda _event: self.force_refresh())
        self.root.bind("<Control-c>", lambda _event: self.copy_selected())

    def _tick(self) -> None:
        if not self.paused:
            self.force_refresh(show_errors=False)
        self.root.after(self.refresh_ms, self._tick)

    def force_refresh(self, show_errors: bool = True) -> None:
        try:
            self.data = self.reader.read(include_trailers=True)
            game = self.data.get("scsValues", {}).get("game", "unknown")
            revision = self.data.get("scsValues", {}).get("telemetryPluginRevision", "unknown")
            sdk = "激活" if self.data.get("sdkActive") else "未激活"
            paused = "暂停" if self.data.get("pause") else "运行"
            truck = self.data.get("configString", {}).get("truckName", "")
            speed = float(self.data.get("truckFloat", {}).get("speed", 0.0)) * 3.6
            angle = self._articulation_angle_deg()
            angle_text = "--" if angle is None else f"{angle:+.1f}°"
            self.status_var.set(f"状态：已连接 | 游戏 {game} | SDK {sdk} | {paused} | 插件版本 {revision} | 车辆 {truck or '未知'} | 速度 {abs(speed):.1f} km/h | 车头-挂车夹角 {angle_text}")
            self._refresh_views()
        except SharedMemoryUnavailable as exc:
            self.status_var.set("状态：未连接遥测共享内存，请启动游戏并确认 telemetry DLL 已安装")
            if show_errors:
                messagebox.showwarning("车辆数据实时监视器", str(exc))
        except Exception as exc:
            self.status_var.set(f"状态：读取失败 - {exc}")
            if show_errors:
                messagebox.showerror("车辆数据实时监视器", str(exc))

    def _refresh_views(self) -> None:
        self._refresh_summary()
        self._refresh_raw_tree()

    def _refresh_summary(self) -> None:
        opened = self._opened_paths(self.summary_tree)
        self.summary_tree.delete(*self.summary_tree.get_children())
        query = self.search_var.get().strip().lower()
        for group_name, rows in self._summary_rows():
            rows = [row for row in rows if self._row_matches(query, group_name, row)]
            if not rows:
                continue
            group_id = f"summary.{group_name}"
            item = self.summary_tree.insert("", "end", iid=group_id, text=group_name, values=(f"{len(rows)} 项", ""))
            self.summary_tree.item(item, open=bool(query) or group_id in opened)
            for index, (label, value, path) in enumerate(rows):
                self.summary_tree.insert(item, "end", iid=f"{group_id}.{index}", text=label, values=(value, path))

    def _summary_rows(self) -> list[tuple[str, list[tuple[str, str, str]]]]:
        groups = [(name, [(label, self._format_field(path, fmt), self._path_text(path)) for label, path, fmt in fields]) for name, fields in FIELD_GROUPS]
        groups.insert(7, ("车轮数据", self._wheel_rows()))
        groups.insert(10, ("挂车数据", self._trailer_rows()))
        groups.insert(11, ("地面材质列表", self._substance_rows()))
        groups.insert(12, ("车头与挂车夹角", self._articulation_rows()))
        return groups

    def _row_matches(self, query: str, group_name: str, row: tuple[str, str, str]) -> bool:
        if not query:
            return True
        label, value, path = row
        text = f"{group_name} {label} {value} {path}".lower()
        return query in text

    def _refresh_raw_tree(self) -> None:
        opened = self._opened_paths(self.raw_tree)
        self.raw_tree.delete(*self.raw_tree.get_children())
        query = self.search_var.get().strip().lower()
        if query:
            for path, value in self._flatten(self.data):
                value_text = self._format_value(value)
                if query in path.lower() or query in value_text.lower():
                    self.raw_tree.insert("", "end", text=path, values=(value_text, type(value).__name__))
            return
        self._insert_node("", "data", self.data, "data", opened)

    def _insert_node(self, parent: str, label: str, value: Any, path: str, opened: set[str]) -> None:
        if isinstance(value, dict):
            item = self.raw_tree.insert(parent, "end", iid=path, text=label, values=(f"{len(value)} 项", "dict"))
            self.raw_tree.item(item, open=path in opened)
            for key, child in value.items():
                self._insert_node(item, str(key), child, f"{path}.{key}", opened)
        elif isinstance(value, list):
            item = self.raw_tree.insert(parent, "end", iid=path, text=label, values=(f"{len(value)} 项", "list"))
            self.raw_tree.item(item, open=path in opened)
            for index, child in enumerate(value):
                self._insert_node(item, f"[{index}]", child, f"{path}[{index}]", opened)
        else:
            self.raw_tree.insert(parent, "end", iid=path, text=label, values=(self._format_value(value), type(value).__name__))

    def _wheel_rows(self) -> list[tuple[str, str, str]]:
        rows: list[tuple[str, str, str]] = []
        wheel_count = max(0, min(self._safe_int(self._get_path(("configUI", "truckWheelCount")), 16), 16))
        fields = [("接地", ("truckBool", "truck_wheelOnGround"), "bool"), ("转向", ("truckFloat", "truck_wheelSteering"), "ratio"), ("转速", ("truckFloat", "truck_wheelVelocity"), "num"), ("悬挂", ("truckFloat", "truck_wheelSuspDeflection"), "m"), ("半径", ("configFloat", "truckWheelRadius"), "m"), ("位置X", ("configVector", "truckWheelPositionX"), "m"), ("位置Y", ("configVector", "truckWheelPositionY"), "m"), ("位置Z", ("configVector", "truckWheelPositionZ"), "m"), ("材质编号", ("truckUI", "truckWheelSubstance"), "")]
        for index in range(wheel_count):
            value = " | ".join(f"{label}: {self._format_unit(self._array_value(path, index), fmt)}" for label, path, fmt in fields)
            path = "; ".join(f"{self._path_text(path)}[{index}]" for _label, path, _fmt in fields)
            rows.append((f"车头车轮 {index + 1}", value, path))
        return rows or [("车头车轮", "无数据", "")]

    def _trailer_rows(self) -> list[tuple[str, str, str]]:
        rows: list[tuple[str, str, str]] = []
        trailers = self.data.get("trailers") or []
        attached_count = 0
        for index, trailer in enumerate(trailers[:10]):
            if not isinstance(trailer, dict):
                continue
            strings = trailer.get("conString", {})
            bools = trailer.get("comBool", {})
            floats = trailer.get("comFloat", {})
            doubles = trailer.get("comDouble", {})
            ui = trailer.get("conUI", {})
            attached = bool(bools.get("attached"))
            attached_count += 1 if attached else 0
            value = " | ".join([f"连接: {'是' if attached else '否'}", f"品牌: {strings.get('brand') or '未知'}", f"名称: {strings.get('name') or '未知'}", f"车牌: {strings.get('licensePlate') or '未知'}", f"车轮数: {ui.get('wheelCount', '未知')}", f"货损: {float(floats.get('cargoDamage', 0.0)) * 100:.2f}%", f"位置: X {float(doubles.get('worldX', 0.0)):.3f}, Y {float(doubles.get('worldY', 0.0)):.3f}, Z {float(doubles.get('worldZ', 0.0)):.3f}", f"水平航向: {self._format_unit(doubles.get('rotationX'), 'angle')}", f"轮胎接地: {self._bool_list(bools.get('wheelOnGround'))}"])
            rows.append((f"挂车槽位 {index + 1}", value, f"trailers[{index}]"))
        rows.insert(0, ("已连接挂车数量", str(attached_count), "trailers[].comBool.attached"))
        return rows

    def _substance_rows(self) -> list[tuple[str, str, str]]:
        return [(f"材质编号 {index}", name or "未命名", f"substances[{index}]") for index, name in enumerate(self.data.get("substances") or [])] or [("地面材质", "无数据", "substances")]

    def _articulation_rows(self) -> list[tuple[str, str, str]]:
        angle = self._articulation_angle_deg()
        trailer = self._first_attached_trailer()
        rows = [("车头-首个已连接挂车水平夹角", "--" if angle is None else f"{angle:+.2f}°", "truckPlacement.rotationX - trailers[].comDouble.rotationX")]
        rows.append(("车头水平航向 rotationX", self._format_unit(self._get_path(("truckPlacement", "rotationX")), "angle"), "truckPlacement.rotationX"))
        if trailer:
            rows.append(("挂车水平航向 rotationX", self._format_unit(trailer.get("comDouble", {}).get("rotationX"), "angle"), "trailers[].comDouble.rotationX"))
            rows.append(("rotationY 差值参考", self._rotation_delta_text("rotationY", trailer), "truckPlacement.rotationY - trailers[].comDouble.rotationY"))
            rows.append(("rotationZ 差值参考", self._rotation_delta_text("rotationZ", trailer), "truckPlacement.rotationZ - trailers[].comDouble.rotationZ"))
        else:
            rows.append(("挂车水平航向", "没有已连接挂车", "trailers[].comBool.attached"))
        return rows

    def _articulation_angle_deg(self) -> float | None:
        trailer = self._first_attached_trailer()
        return self._rotation_delta_deg("rotationX", trailer)

    def _rotation_delta_deg(self, axis: str, trailer: dict[str, Any] | None) -> float | None:
        truck_rotation = self._get_path(("truckPlacement", axis))
        if trailer is None or truck_rotation is None:
            return None
        trailer_rotation = trailer.get("comDouble", {}).get(axis)
        if trailer_rotation is None:
            return None
        truck_yaw = scs_rotation_to_radians(float(truck_rotation))
        trailer_yaw = scs_rotation_to_radians(float(trailer_rotation))
        return math.degrees(normalize_angle(truck_yaw - trailer_yaw))

    def _rotation_delta_text(self, axis: str, trailer: dict[str, Any] | None) -> str:
        angle = self._rotation_delta_deg(axis, trailer)
        return "--" if angle is None else f"{angle:+.2f}°"

    def _first_attached_trailer(self) -> dict[str, Any] | None:
        trailers = attached_trailers(self.data)
        return trailers[0] if trailers else None

    def _get_path(self, path: tuple[str, ...]) -> Any:
        cursor: Any = self.data
        for key in path:
            if not isinstance(cursor, dict) or key not in cursor:
                return None
            cursor = cursor[key]
        return cursor

    def _array_value(self, path: tuple[str, ...], index: int) -> Any:
        values = self._get_path(path)
        if not isinstance(values, list) or index >= len(values):
            return None
        return values[index]

    def _format_field(self, path: tuple[str, ...], fmt: str) -> str:
        value = self._get_path(path)
        if fmt.startswith("version:"):
            minor = self._get_path(tuple(fmt.split(":", 1)[1].split(".")))
            return f"{self._format_value(value)}.{self._format_value(minor)}"
        return self._format_unit(value, fmt)

    def _format_unit(self, value: Any, fmt: str) -> str:
        if value is None:
            return "无数据"
        if fmt == "bool":
            return "是" if bool(value) else "否"
        if fmt == "kmh":
            return f"{float(value) * 3.6:.1f} km/h"
        if fmt == "rpm":
            return f"{float(value):.0f} rpm"
        if fmt == "ratio":
            return f"{float(value):+.3f}"
        if fmt == "percent":
            return f"{float(value) * 100:.2f}%"
        if fmt == "angle":
            degrees = math.degrees(scs_rotation_to_radians(float(value)))
            return f"{float(value):.6f} ({degrees:+.1f}°)"
        if fmt == "m":
            return f"{float(value):.3f} m"
        if fmt == "km":
            return f"{float(value):.1f} km"
        if fmt == "s":
            return f"{float(value):.0f} s"
        if fmt == "l":
            return f"{float(value):.1f} L"
        if fmt == "bar":
            return f"{float(value):.2f}"
        if fmt == "deg":
            return f"{float(value):.1f} °C"
        if fmt == "v":
            return f"{float(value):.2f} V"
        if fmt == "kg":
            return f"{float(value):.0f} kg"
        if fmt == "money":
            return f"{int(value)}"
        if fmt == "bytes":
            return f"{int(value)} bytes"
        if fmt == "count":
            return f"{int(value)}"
        if fmt == "num":
            return f"{float(value):.3f}"
        return self._format_value(value)

    def _format_value(self, value: Any) -> str:
        if isinstance(value, float):
            return f"{value:.6f}"
        if value is None:
            return "null"
        return str(value)

    def _path_text(self, path: tuple[str, ...]) -> str:
        return ".".join(path)

    def _safe_int(self, value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _bool_list(self, values: Any) -> str:
        if not isinstance(values, list):
            return "无数据"
        return ", ".join("是" if bool(value) else "否" for value in values)

    def _opened_paths(self, tree: ttk.Treeview) -> set[str]:
        opened: set[str] = set()
        def walk(item: str) -> None:
            if tree.item(item, "open"):
                opened.add(item)
            for child in tree.get_children(item):
                walk(child)
        for root_item in tree.get_children(""):
            walk(root_item)
        return opened

    def _flatten(self, value: Any, path: str = "data"):
        if isinstance(value, dict):
            for key, child in value.items():
                yield from self._flatten(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                yield from self._flatten(child, f"{path}[{index}]")
        else:
            yield path, value

    def _clear_search(self) -> None:
        self.search_var.set("")
        self._refresh_views()

    def expand_all(self) -> None:
        self._set_open_recursive(self.summary_tree, "", True)
        self._set_open_recursive(self.raw_tree, "", True)

    def collapse_all(self) -> None:
        self._set_open_recursive(self.summary_tree, "", False)
        self._set_open_recursive(self.raw_tree, "", False)

    def _set_open_recursive(self, tree: ttk.Treeview, item: str, open_value: bool) -> None:
        for child in tree.get_children(item):
            tree.item(child, open=open_value)
            self._set_open_recursive(tree, child, open_value)

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        self.status_var.set("状态：已暂停刷新" if self.paused else "状态：继续实时刷新")
        if not self.paused:
            self.force_refresh(show_errors=False)

    def copy_selected(self) -> None:
        lines = []
        for tree in (self.summary_tree, self.raw_tree):
            for item in tree.selection():
                text = tree.item(item, "text")
                values = tree.item(item, "values")
                value = values[0] if values else ""
                path = values[1] if len(values) > 1 else item
                lines.append(f"{text}: {value} ({path})")
        if not lines:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(lines))
        self.status_var.set("状态：已复制选中数据")

    def export_json(self) -> None:
        if not self.data:
            messagebox.showinfo("车辆数据实时监视器", "当前没有可导出的数据。")
            return
        path = filedialog.asksaveasfilename(title="导出车辆数据", defaultextension=".json", initialfile="telemetry_snapshot.json", filetypes=(("JSON 文件", "*.json"), ("所有文件", "*.*")))
        if not path:
            return
        Path(path).write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        self.status_var.set(f"状态：已导出 {path}")


def main() -> None:
    root = tk.Tk()
    TelemetryDataViewer(root)
    root.mainloop()

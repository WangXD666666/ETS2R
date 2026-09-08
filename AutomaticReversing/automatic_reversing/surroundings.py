"""Obstacle snapshots and collision-aware movement-space calculations."""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Sequence

from .models import Pose2D

Point = tuple[float, float]
Polygon = tuple[Point, ...]


@dataclass(frozen=True)
class Obstacle:
    obstacle_id: str
    kind: str
    label: str
    polygon: Polygon
    confidence: float = 1.0


@dataclass(frozen=True)
class SurroundingsSnapshot:
    obstacles: tuple[Obstacle, ...] = ()
    range_m: float = 15.0
    source: str = "none"
    received_at: float = 0.0
    stale: bool = True
    error: str = ""

    @property
    def usable(self) -> bool:
        return not self.stale and not self.error


class SurroundingsFileReader:
    """Read detector output without failing the control loop on partial writes."""

    def __init__(self, path: Path, stale_after_s: float = 1.5) -> None:
        self.path = path
        self.stale_after_s = stale_after_s
        self._last_mtime_ns: int | None = None
        self._last_snapshot = SurroundingsSnapshot()

    def read(self, reference_pose: Pose2D | None, now: float | None = None) -> SurroundingsSnapshot:
        current_time = time.time() if now is None else now
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return SurroundingsSnapshot(error=f"等待 {self.path.name}")
        except OSError as exc:
            return replace(self._last_snapshot, stale=True, error=str(exc))

        if stat.st_mtime_ns != self._last_mtime_ns:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                snapshot = snapshot_from_dict(payload, reference_pose, received_at=stat.st_mtime)
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                return replace(self._last_snapshot, stale=True, error=f"环境数据无效：{exc}")
            self._last_mtime_ns = stat.st_mtime_ns
            self._last_snapshot = snapshot

        stale = current_time - self._last_snapshot.received_at > self.stale_after_s
        return replace(self._last_snapshot, stale=stale, error="" if not stale else "环境数据已过期")


def snapshot_from_dict(
    payload: dict[str, Any],
    reference_pose: Pose2D | None,
    received_at: float | None = None,
) -> SurroundingsSnapshot:
    coordinate_system = str(payload.get("coordinate_system", "vehicle")).lower()
    if coordinate_system not in {"vehicle", "world"}:
        raise ValueError("coordinate_system 必须是 vehicle 或 world")
    if coordinate_system == "vehicle" and reference_pose is None:
        raise ValueError("车辆坐标数据需要有效遥测姿态")

    raw_obstacles = payload.get("obstacles", [])
    if not isinstance(raw_obstacles, list):
        raise ValueError("obstacles 必须是数组")
    obstacles = tuple(
        _obstacle_from_dict(raw, index, coordinate_system, reference_pose)
        for index, raw in enumerate(raw_obstacles)
    )
    range_m = _finite_float(payload.get("range_m", 15.0), "range_m")
    if not 2.0 <= range_m <= 100.0:
        raise ValueError("range_m 必须在 2 到 100 米之间")
    return SurroundingsSnapshot(
        obstacles=obstacles,
        range_m=range_m,
        source=str(payload.get("source") or "外部障碍物检测"),
        received_at=time.time() if received_at is None else received_at,
        stale=False,
    )


def demo_snapshot(reference_pose: Pose2D, received_at: float | None = None) -> SurroundingsSnapshot:
    """A clearly labelled parking-yard scene for UI verification."""

    payload = {
        "coordinate_system": "vehicle",
        "source": "演示环境（非实时检测）",
        "range_m": 14.0,
        "obstacles": [
            {"id": "loading-bay", "type": "wall", "label": "装卸平台", "center": [-5.0, -7.0], "size": [0.7, 13.0]},
            {"id": "parked-truck", "type": "vehicle", "label": "停放车辆", "center": [5.2, -4.0], "size": [2.6, 7.4], "yaw_deg": -8.0},
            {"id": "bollard-a", "type": "bollard", "label": "防撞柱", "center": [-3.0, 5.2], "size": [0.7, 0.7]},
            {"id": "bollard-b", "type": "bollard", "label": "防撞柱", "center": [3.2, 6.4], "size": [0.7, 0.7]},
        ],
    }
    return snapshot_from_dict(payload, reference_pose, received_at=received_at)


def movement_clearance_profile(
    vehicle_polygons: Sequence[Polygon],
    obstacles: Sequence[Obstacle],
    origin: Point,
    max_range: float,
    safety_margin: float = 0.6,
    samples: int = 48,
    step: float = 0.35,
) -> tuple[tuple[float, float], ...]:
    """Return allowed rigid-body translation distance for evenly spaced headings."""

    if samples < 8:
        raise ValueError("samples must be at least 8")
    obstacle_polygons = [obstacle.polygon for obstacle in obstacles]
    if any(polygons_intersect(vehicle, obstacle) for vehicle in vehicle_polygons for obstacle in obstacle_polygons):
        return tuple((math.tau * index / samples, 0.0) for index in range(samples))
    profile: list[tuple[float, float]] = []
    for index in range(samples):
        angle = math.tau * index / samples
        dx, dz = math.cos(angle), math.sin(angle)
        collision_distance = max_range + safety_margin
        distance = step
        while distance <= max_range + safety_margin:
            translated = tuple(
                tuple((x + dx * distance, z + dz * distance) for x, z in polygon)
                for polygon in vehicle_polygons
            )
            if any(polygons_intersect(vehicle, obstacle) for vehicle in translated for obstacle in obstacle_polygons):
                collision_distance = distance
                break
            distance += step
        allowed = min(max_range, max(0.0, collision_distance - safety_margin))
        profile.append((angle, allowed))
    return tuple(profile)


def directional_clearances(profile: Sequence[tuple[float, float]], yaw: float) -> dict[str, float]:
    directions = {
        "前": yaw + math.pi / 2.0,
        "后": yaw - math.pi / 2.0,
        "左": yaw + math.pi,
        "右": yaw,
    }
    result: dict[str, float] = {}
    for label, target in directions.items():
        _, distance = min(profile, key=lambda item: abs(_angle_delta(item[0], target)))
        result[label] = distance
    return result


def polygon_distance(first: Polygon, second: Polygon) -> float:
    if polygons_intersect(first, second):
        return 0.0
    distances = [
        _point_segment_distance(point, start, end)
        for point in first
        for start, end in _edges(second)
    ]
    distances.extend(
        _point_segment_distance(point, start, end)
        for point in second
        for start, end in _edges(first)
    )
    return min(distances, default=math.inf)


def polygons_intersect(first: Polygon, second: Polygon) -> bool:
    if len(first) < 3 or len(second) < 3:
        return False
    if any(_segments_intersect(a, b, c, d) for a, b in _edges(first) for c, d in _edges(second)):
        return True
    return _point_in_polygon(first[0], second) or _point_in_polygon(second[0], first)


def _obstacle_from_dict(
    raw: Any,
    index: int,
    coordinate_system: str,
    reference_pose: Pose2D | None,
) -> Obstacle:
    if not isinstance(raw, dict):
        raise ValueError(f"obstacles[{index}] 必须是对象")
    if "polygon" in raw:
        raw_polygon = raw["polygon"]
        if not isinstance(raw_polygon, list) or len(raw_polygon) < 3:
            raise ValueError(f"obstacles[{index}].polygon 至少需要 3 个点")
        local_polygon = tuple(_point(value, f"obstacles[{index}].polygon") for value in raw_polygon)
    else:
        center = _point(raw.get("center"), f"obstacles[{index}].center")
        size = _point(raw.get("size"), f"obstacles[{index}].size")
        if size[0] <= 0.0 or size[1] <= 0.0:
            raise ValueError(f"obstacles[{index}].size 必须大于 0")
        yaw = math.radians(_finite_float(raw.get("yaw_deg", 0.0), f"obstacles[{index}].yaw_deg"))
        local_polygon = _rectangle(center, size, yaw)

    polygon = local_polygon
    if coordinate_system == "vehicle":
        assert reference_pose is not None
        polygon = tuple(_local_to_world(reference_pose, point) for point in local_polygon)
    confidence = _finite_float(raw.get("confidence", 1.0), f"obstacles[{index}].confidence")
    return Obstacle(
        obstacle_id=str(raw.get("id") or f"obstacle-{index + 1}"),
        kind=str(raw.get("type") or "unknown"),
        label=str(raw.get("label") or _default_label(str(raw.get("type") or "unknown"))),
        polygon=polygon,
        confidence=max(0.0, min(1.0, confidence)),
    )


def _rectangle(center: Point, size: Point, yaw: float) -> Polygon:
    half_x, half_z = size[0] / 2.0, size[1] / 2.0
    cos_yaw, sin_yaw = math.cos(yaw), math.sin(yaw)
    points = []
    for x, z in ((-half_x, -half_z), (half_x, -half_z), (half_x, half_z), (-half_x, half_z)):
        points.append((center[0] + x * cos_yaw - z * sin_yaw, center[1] + x * sin_yaw + z * cos_yaw))
    return tuple(points)


def _local_to_world(pose: Pose2D, point: Point) -> Point:
    cos_yaw, sin_yaw = math.cos(pose.yaw), math.sin(pose.yaw)
    return (
        pose.x + point[0] * cos_yaw - point[1] * sin_yaw,
        pose.z + point[0] * sin_yaw + point[1] * cos_yaw,
    )


def _point(value: Any, field: str) -> Point:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{field} 必须是 [x, z]")
    return _finite_float(value[0], field), _finite_float(value[1], field)


def _finite_float(value: Any, field: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} 必须是有限数值")
    return number


def _edges(polygon: Polygon) -> Iterable[tuple[Point, Point]]:
    return zip(polygon, polygon[1:] + polygon[:1])


def _cross(first: Point, second: Point) -> float:
    return first[0] * second[1] - first[1] * second[0]


def _subtract(first: Point, second: Point) -> Point:
    return first[0] - second[0], first[1] - second[1]


def _segments_intersect(a: Point, b: Point, c: Point, d: Point) -> bool:
    ab, ac, ad = _subtract(b, a), _subtract(c, a), _subtract(d, a)
    cd, ca, cb = _subtract(d, c), _subtract(a, c), _subtract(b, c)
    first = _cross(ab, ac) * _cross(ab, ad)
    second = _cross(cd, ca) * _cross(cd, cb)
    if first < -1e-9 and second < -1e-9:
        return True
    return any(
        abs(_cross(_subtract(q, p), _subtract(r, p))) <= 1e-9
        and min(p[0], q[0]) - 1e-9 <= r[0] <= max(p[0], q[0]) + 1e-9
        and min(p[1], q[1]) - 1e-9 <= r[1] <= max(p[1], q[1]) + 1e-9
        for p, q, r in ((a, b, c), (a, b, d), (c, d, a), (c, d, b))
    )


def _point_in_polygon(point: Point, polygon: Polygon) -> bool:
    inside = False
    previous = polygon[-1]
    for current in polygon:
        if (current[1] > point[1]) != (previous[1] > point[1]):
            x_cross = (previous[0] - current[0]) * (point[1] - current[1]) / (previous[1] - current[1]) + current[0]
            if point[0] < x_cross:
                inside = not inside
        previous = current
    return inside


def _point_segment_distance(point: Point, start: Point, end: Point) -> float:
    dx, dz = end[0] - start[0], end[1] - start[1]
    length_sq = dx * dx + dz * dz
    if length_sq <= 1e-12:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    projection = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dz) / length_sq
    projection = max(0.0, min(1.0, projection))
    nearest = start[0] + projection * dx, start[1] + projection * dz
    return math.hypot(point[0] - nearest[0], point[1] - nearest[1])


def _angle_delta(first: float, second: float) -> float:
    return (first - second + math.pi) % math.tau - math.pi


def _default_label(kind: str) -> str:
    return {
        "vehicle": "车辆",
        "wall": "墙体",
        "bollard": "立柱",
        "pedestrian": "行人",
    }.get(kind, "障碍物")

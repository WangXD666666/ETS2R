"""Read top-down vehicle collision envelopes from extracted SCS game data."""

from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEF_ROOT = PROJECT_ROOT / "game_data" / "def" / "def"
DEFAULT_VEHICLE_ROOT = PROJECT_ROOT / "game_data" / "base_vehicle"


class WheelPoint(Protocol):
    local_x: float
    local_z: float
    steerable: bool
    powered: bool
    liftable: bool


@dataclass(frozen=True)
class GameWheelPoint:
    x: float
    z: float
    steerable: bool = False
    powered: bool = False
    liftable: bool = False


@dataclass(frozen=True)
class GameContour:
    points: tuple[tuple[float, float], ...]
    source: str
    model_paths: tuple[str, ...]
    wheel_points: tuple[GameWheelPoint, ...] = ()
    axle_count: int = 0
    definition_paths: tuple[str, ...] = ()

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        xs = [point[0] for point in self.points]
        zs = [point[1] for point in self.points]
        return min(xs), max(xs), min(zs), max(zs)

    @property
    def width(self) -> float:
        min_x, max_x, _, _ = self.bounds
        return max_x - min_x

    @property
    def length(self) -> float:
        _, _, min_z, max_z = self.bounds
        return max_z - min_z


class GameGeometryError(RuntimeError):
    pass


def convex_hull(points: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    unique = sorted(set(points))
    if len(unique) <= 1:
        return unique

    def cross(origin: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        return (a[0] - origin[0]) * (b[1] - origin[1]) - (a[1] - origin[1]) * (b[0] - origin[0])

    lower: list[tuple[float, float]] = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)

    upper: list[tuple[float, float]] = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def read_pmg_bounds(path: Path) -> tuple[float, float, float, float, float, float]:
    with path.open("rb") as stream:
        header = stream.read(72)
    if len(header) < 72 or struct.unpack_from("<I", header)[0] != 0x506D6715:
        raise GameGeometryError(f"Unsupported PMG file: {path}")
    min_x, min_y, min_z, max_x, max_y, max_z = struct.unpack_from("<6f", header, 48)
    if min_x > max_x or min_y > max_y or min_z > max_z:
        raise GameGeometryError(f"Invalid PMG bounds: {path}")
    return min_x, min_y, min_z, max_x, max_y, max_z


_TOKEN_LETTERS = "\0" + "0123456789" + "abcdefghijklmnopqrstuvwxyz" + "_"


def _token_to_string(value: int) -> str:
    characters: list[str] = []
    while value:
        value, remainder = divmod(value, 38)
        characters.append(_TOKEN_LETTERS[remainder])
    return "".join(characters)


def read_pmg_wheel_points(path: Path) -> list[tuple[float, float]]:
    """Read wheel locator X/Z positions from a PMG v0x15 model."""

    data = path.read_bytes()
    if len(data) < 112 or struct.unpack_from("<I", data)[0] != 0x506D6715:
        raise GameGeometryError(f"Unsupported PMG file: {path}")
    locator_count = struct.unpack_from("<i", data, 20)[0]
    locator_offset = struct.unpack_from("<i", data, 80)[0]
    if locator_count < 0 or locator_count > 1_000_000:
        raise GameGeometryError(f"Invalid PMG locator count: {path}")
    if locator_offset < 0 or locator_offset + locator_count * 44 > len(data):
        raise GameGeometryError(f"Invalid PMG locator table: {path}")

    points: list[tuple[float, float]] = []
    for index in range(locator_count):
        offset = locator_offset + index * 44
        name = _token_to_string(struct.unpack_from("<Q", data, offset)[0])
        if not re.fullmatch(r"wheel_[a-z]+_\d+", name):
            continue
        x, _y, z = struct.unpack_from("<3f", data, offset + 8)
        points.append((x, z))
    return list(dict.fromkeys(points))


def _quat_rotate(point: tuple[float, float, float], quaternion: tuple[float, float, float, float]) -> tuple[float, float, float]:
    w, x, y, z = quaternion
    length = math.sqrt(w * w + x * x + y * y + z * z)
    if length < 1e-8:
        return point
    w, x, y, z = w / length, x / length, y / length, z / length
    px, py, pz = point
    tx = 2.0 * (y * pz - z * py)
    ty = 2.0 * (z * px - x * pz)
    tz = 2.0 * (x * py - y * px)
    return (
        px + w * tx + (y * tz - z * ty),
        py + w * ty + (z * tx - x * tz),
        pz + w * tz + (x * ty - y * tx),
    )


def _transform_points(
    points: Iterable[tuple[float, float, float]],
    position: tuple[float, float, float],
    rotation: tuple[float, float, float, float],
) -> list[tuple[float, float, float]]:
    transformed = []
    for point in points:
        x, y, z = _quat_rotate(point, rotation)
        transformed.append((x + position[0], y + position[1], z + position[2]))
    return transformed


def _box_points(scale: tuple[float, float, float]) -> list[tuple[float, float, float]]:
    half_x, half_y, half_z = (value / 2.0 for value in scale)
    return [
        (x, y, z)
        for x in (-half_x, half_x)
        for y in (-half_y, half_y)
        for z in (-half_z, half_z)
    ]


def _cylinder_points(radius: float, depth: float, segments: int = 20) -> list[tuple[float, float, float]]:
    points = []
    for index in range(segments):
        angle = math.tau * index / segments
        for z in (-depth / 2.0, depth / 2.0):
            points.append((math.cos(angle) * radius, math.sin(angle) * radius, z))
    return points


def _sphere_points(radius: float, segments: int = 20) -> list[tuple[float, float, float]]:
    return [
        (math.cos(math.tau * index / segments) * radius, 0.0, math.sin(math.tau * index / segments) * radius)
        for index in range(segments)
    ]


def read_pmc_points(path: Path, variant_index: int = 0) -> list[tuple[float, float, float]]:
    """Return points from one PMC collision variant.

    The layout follows ConverterPIX's documented PMC v6 structures. Collision
    locators transform reusable convex pieces or primitive shapes into model
    coordinates; returning raw reusable pieces would produce incorrect bounds.
    """

    data = path.read_bytes()
    if len(data) < 40:
        raise GameGeometryError(f"PMC file is truncated: {path}")
    (
        version,
        _look_count,
        variant_count,
        _material_count,
        piece_count,
        _look_offset,
        _variant_offset,
        _material_offset,
        piece_offset,
        variant_def_offset,
    ) = struct.unpack_from("<10I", data)
    if version != 6:
        raise GameGeometryError(f"Unsupported PMC version {version}: {path}")

    pieces: list[list[tuple[float, float, float]]] = []
    for index in range(piece_count):
        descriptor = piece_offset + index * 16
        if descriptor + 16 > len(data):
            raise GameGeometryError(f"Invalid PMC piece table: {path}")
        _edge_count, vertex_count, vertex_offset, _face_offset = struct.unpack_from("<4I", data, descriptor)
        end = vertex_offset + vertex_count * 12
        if vertex_count > 1_000_000 or end > len(data):
            raise GameGeometryError(f"Invalid PMC vertex stream: {path}")
        pieces.append([
            struct.unpack_from("<3f", data, vertex_offset + vertex * 12)
            for vertex in range(vertex_count)
        ])

    if variant_count == 0:
        return [point for piece in pieces for point in piece]
    variant_index = min(max(variant_index, 0), variant_count - 1)
    definition_entry = variant_def_offset + variant_index * 4
    if definition_entry + 4 > len(data):
        raise GameGeometryError(f"Invalid PMC variant table: {path}")
    offset = struct.unpack_from("<I", data, definition_entry)[0]
    points: list[tuple[float, float, float]] = []
    seen_offsets: set[int] = set()

    while offset + 8 <= len(data):
        if offset in seen_offsets:
            raise GameGeometryError(f"PMC locator loop: {path}")
        seen_offsets.add(offset)
        _locator_type, data_size = struct.unpack_from("<ii", data, offset)
        if data_size == -1:
            break
        if data_size not in (40, 56, 60, 64) or offset + data_size > len(data):
            raise GameGeometryError(f"Unsupported PMC locator size {data_size}: {path}")
        position = struct.unpack_from("<3f", data, offset + 24)

        if data_size == 40:
            radius = struct.unpack_from("<f", data, offset + 36)[0]
            local_points = _sphere_points(radius)
            rotation = (1.0, 0.0, 0.0, 0.0)
        else:
            rotation = struct.unpack_from("<4f", data, offset + 36)
            if data_size == 56:
                piece_index = struct.unpack_from("<I", data, offset + 52)[0]
                if piece_index >= len(pieces):
                    raise GameGeometryError(f"Invalid PMC convex piece {piece_index}: {path}")
                local_points = pieces[piece_index]
            elif data_size == 60:
                radius, depth = struct.unpack_from("<2f", data, offset + 52)
                local_points = _cylinder_points(radius, depth)
            else:
                scale = struct.unpack_from("<3f", data, offset + 52)
                local_points = _box_points(scale)
        points.extend(_transform_points(local_points, position, rotation))
        offset += data_size

    return points


def pmc_top_down_contour(path: Path, variant_index: int = 0) -> list[tuple[float, float]]:
    points = read_pmc_points(path, variant_index=variant_index)
    return convex_hull((x, z) for x, _y, z in points)


_ATTRIBUTE_RE = re.compile(r"^\s*([A-Za-z0-9_]+)(?:\[\])?\s*:\s*(.*?)\s*(?:#.*)?$")


def read_sii_attributes(path: Path) -> dict[str, list[str]]:
    attributes: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = _ATTRIBUTE_RE.match(line)
        if not match:
            continue
        value = match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        attributes.setdefault(match.group(1), []).append(value)
    return attributes


def _bool_values(attributes: dict[str, list[str]], key: str) -> list[bool]:
    return [value.lower() in ("true", "1") for value in attributes.get(key, [])]


def _vector3(value: str) -> tuple[float, float, float] | None:
    match = re.fullmatch(r"\(\s*([-+0-9.eE]+)\s*,\s*([-+0-9.eE]+)\s*,\s*([-+0-9.eE]+)\s*\)", value)
    if not match:
        return None
    return tuple(float(match.group(index)) for index in range(1, 4))  # type: ignore[return-value]


class GameGeometryResolver:
    def __init__(self, def_root: Path = DEFAULT_DEF_ROOT, vehicle_root: Path = DEFAULT_VEHICLE_ROOT) -> None:
        self.def_root = def_root
        self.vehicle_root = vehicle_root
        self._cache: dict[str, GameContour | None] = {}

    @property
    def available(self) -> bool:
        return self.def_root.is_dir() and self.vehicle_root.is_dir()

    def clear_cache(self) -> None:
        self._cache.clear()

    def truck_contour(
        self,
        truck_id: str,
        wheels: Sequence[WheelPoint],
        axle_count: int,
        selection: dict[str, str] | None = None,
    ) -> GameContour | None:
        token = truck_id.removeprefix("vehicle.")
        cache_key = f"truck|{token}|{axle_count}|{self._wheel_signature(wheels)}|{self._selection_signature(selection)}"
        if cache_key not in self._cache:
            self._cache[cache_key] = self._resolve_truck(token, wheels, axle_count, selection)
        return self._cache[cache_key]

    def trailer_contour(
        self,
        trailer_id: str,
        body_type: str,
        chain_type: str,
        wheels: Sequence[WheelPoint],
        hook: tuple[float, float] | None,
        axle_count: int,
        selection: dict[str, str] | None = None,
    ) -> GameContour | None:
        token = trailer_id.removeprefix("vehicle.")
        point_span = self._point_span(wheels, hook)
        hook_signature = "none" if hook is None else f"{hook[0]:.2f},{hook[1]:.2f}"
        cache_key = (
            f"trailer|{token}|{body_type}|{chain_type}|{axle_count}|"
            f"{round(point_span, 1)}|{hook_signature}|{self._wheel_signature(wheels)}|"
            f"{self._selection_signature(selection)}"
        )
        if cache_key not in self._cache:
            self._cache[cache_key] = self._resolve_trailer(token, body_type, wheels, hook, axle_count, selection)
        return self._cache[cache_key]

    def truck_definition_candidates(self, truck_id: str) -> dict[str, list[str]]:
        token = truck_id.removeprefix("vehicle.")
        definition_dir = self.def_root / "vehicle" / "truck" / token
        return {
            "chassis": self._definition_candidates(definition_dir / "chassis"),
            "cabin": self._definition_candidates(definition_dir / "cabin"),
        }

    def trailer_definition_candidates(self, trailer_id: str) -> dict[str, list[str]]:
        token = trailer_id.removeprefix("vehicle.")
        definition_dir, legacy = self._trailer_definition_dir(token)
        if definition_dir is None:
            return {"chassis": [], "body": []}
        chassis_dir = definition_dir if legacy else definition_dir / "chassis"
        body_dir = definition_dir / "accessory" if legacy else definition_dir / "body"
        return {
            "chassis": self._definition_candidates(chassis_dir),
            "body": self._definition_candidates(body_dir),
        }

    def _resolve_truck(
        self,
        token: str,
        wheels: Sequence[WheelPoint],
        axle_count: int,
        selection: dict[str, str] | None,
    ) -> GameContour | None:
        definition_dir = self.def_root / "vehicle" / "truck" / token
        if not definition_dir.is_dir():
            return None
        candidates = self.truck_definition_candidates(token)
        chassis = self._selected_definition(selection, "chassis", candidates["chassis"])
        if chassis is None:
            chassis = self._best_chassis(
                definition_dir / "chassis", wheels, axle_count, telemetry_id=token, steerable_first_default=True
            )
        if chassis is None:
            return None
        model_defs = [chassis]
        chassis_attributes = read_sii_attributes(chassis)
        cabin_def = self._selected_definition(selection, "cabin", candidates["cabin"])
        if cabin_def is None:
            cabin_def = self._truck_cabin_definition(definition_dir, chassis_attributes)
        if cabin_def is not None:
            model_defs.append(cabin_def)
        return self._contour_from_definitions(model_defs, f"游戏碰撞模型：{token}", self._reference_points(wheels, None))

    def _best_chassis(
        self,
        chassis_dir: Path,
        wheels: Sequence[WheelPoint],
        axle_count: int,
        telemetry_id: str = "",
        steerable_first_default: bool = False,
    ) -> Path | None:
        if not chassis_dir.is_dir():
            return None
        telemetry_axles = self._axle_properties(wheels)
        candidates: list[tuple[float, Path]] = []
        for path in chassis_dir.glob("*.sii"):
            attributes = read_sii_attributes(path)
            definition_axles = len(attributes.get("residual_travel", []))
            if definition_axles == 0:
                continue
            score = abs(definition_axles - axle_count) * 100.0
            definition_powered = self._definition_flags(attributes, "powered_axle", definition_axles)
            definition_liftable = self._definition_flags(attributes, "liftable_axle", definition_axles)
            definition_steerable = self._definition_flags(
                attributes, "steerable_axle", definition_axles, first_default=steerable_first_default
            )
            for index in range(min(len(telemetry_axles), definition_axles)):
                steerable, powered, liftable = telemetry_axles[index]
                score += 16.0 if definition_powered[index] != powered else 0.0
                score += 12.0 if definition_liftable[index] != liftable else 0.0
                score += 8.0 if definition_steerable[index] != steerable else 0.0

            telemetry_zs = self._axle_z_positions(wheels)
            model_zs = self._definition_axle_z_positions(attributes)
            if (
                telemetry_zs
                and len(model_zs) == definition_axles
                and definition_axles == len(telemetry_zs)
            ):
                score += sum(min(abs(z - model_z) for model_z in model_zs) for z in telemetry_zs) * 40.0

            compact_id = self._compact_token(telemetry_id)
            compact_stem = self._compact_token(path.stem)
            if compact_stem and compact_stem in compact_id:
                # Freight-market IDs encode the exact chassis (for example
                # scs_lowbed.ch_7_3x2esii) even when telemetry truncates wheels.
                score -= 400.0
            if axle_count == 2 and "4x2" in path.stem.lower():
                score -= 5.0
            candidates.append((score, path))
        return min(candidates, default=(0.0, None), key=lambda item: (item[0], item[1].name))[1]

    def _truck_cabin_definition(self, definition_dir: Path, chassis_attributes: dict[str, list[str]]) -> Path | None:
        for value in chassis_attributes.get("defaults", []):
            if "/cabin/" in value:
                path = self._definition_path(value)
                if path.is_file():
                    return path
        data_file = definition_dir / "data.sii"
        if data_file.is_file():
            for value in read_sii_attributes(data_file).get("fallback", []):
                if value.startswith("cabin|"):
                    path = definition_dir / value.replace("|", "/")
                    if path.is_file():
                        return path
        return None

    def _resolve_trailer(
        self,
        token: str,
        body_type: str,
        wheels: Sequence[WheelPoint],
        hook: tuple[float, float] | None,
        axle_count: int,
        selection: dict[str, str] | None,
    ) -> GameContour | None:
        definition_dir, legacy = self._trailer_definition_dir(token)
        if definition_dir is None:
            return None
        chassis_dir = definition_dir if legacy else definition_dir / "chassis"
        body_dir = definition_dir / "accessory" if legacy else definition_dir / "body"
        candidates = self.trailer_definition_candidates(token)
        chassis = self._selected_definition(selection, "chassis", candidates["chassis"])
        if chassis is None:
            chassis = self._best_chassis(chassis_dir, wheels, axle_count, telemetry_id=token)
        body = self._selected_definition(selection, "body", candidates["body"])
        if body is None:
            body = self._best_trailer_body(
                body_dir,
                body_type,
                self._point_span(wheels, hook),
                chassis.stem if chassis is not None else "",
            )
        model_defs = [path for path in (chassis, body) if path is not None]
        if not model_defs:
            return None
        return self._contour_from_definitions(
            model_defs, f"游戏碰撞模型：{token}/{body_type}", self._reference_points(wheels, hook)
        )

    def _best_trailer_body(
        self,
        body_dir: Path,
        body_type: str,
        point_span: float,
        chassis_hint: str = "",
    ) -> Path | None:
        if not body_dir.is_dir():
            return None
        aliases = {
            "refrigerated": "reefer",
            "refrigerator": "reefer",
            "dryvan": "dryvan",
            "dry_van": "dryvan",
            "curtain_sider": "curtain",
            "curtainsider": "curtain",
            "insulated": "insulated",
        }
        wanted = aliases.get(body_type.lower(), body_type.lower())
        expected_length = 13.6 if point_span >= 5.2 else 7.8
        extended_chassis = "ext" in chassis_hint.lower() or "long" in chassis_hint.lower()
        candidates: list[tuple[float, Path]] = []
        for path in body_dir.glob("*.sii"):
            attributes = read_sii_attributes(path)
            declared_type = " ".join(attributes.get("body_type", [])).lower()
            searchable = f"{path.stem.lower()} {declared_type}"
            type_score = 0.0 if wanted and wanted in searchable else 50.0
            size = _vector3(attributes.get("total_size", [""])[0])
            length_score = abs(size[2] - expected_length) if size is not None else 20.0
            length_name_score = 0.0
            if "long" in searchable or "short" in searchable:
                matches_chassis = (extended_chassis and "long" in searchable) or (
                    not extended_chassis and "short" in searchable
                )
                length_name_score = 0.0 if matches_chassis else 100.0
            candidates.append((type_score + length_score + length_name_score, path))
        return min(candidates, default=(0.0, None), key=lambda item: (item[0], item[1].name))[1]

    def _contour_from_definitions(
        self,
        definitions: Sequence[Path],
        source: str,
        reference_points: Sequence[tuple[float, float]] = (),
    ) -> GameContour | None:
        points: list[tuple[float, float]] = []
        model_paths: list[str] = []
        model_wheel_positions: list[tuple[float, float]] = []
        seen_paths: set[Path] = set()
        for definition in definitions:
            attributes = read_sii_attributes(definition)
            for collision_path in attributes.get("collision", []):
                path = self._vehicle_path(collision_path)
                if not path.is_file() or path in seen_paths:
                    continue
                seen_paths.add(path)
                try:
                    contour, variant_index, variant_count = self._best_collision_variant(path, reference_points)
                    points.extend(contour)
                except (OSError, GameGeometryError, struct.error):
                    continue
                variant_suffix = f" [变体 {variant_index}]" if variant_count > 1 else ""
                model_paths.append(collision_path + variant_suffix)
                pmg_path = path.with_suffix(".pmg")
                if pmg_path.is_file():
                    try:
                        model_wheel_positions.extend(read_pmg_wheel_points(pmg_path))
                    except (OSError, GameGeometryError, struct.error):
                        pass
        hull = convex_hull(points)
        if len(hull) < 3:
            return None
        model_wheel_positions = list(dict.fromkeys(model_wheel_positions))
        axle_count, axle_flags = self._definition_axles(definitions)
        wheel_points = self._model_wheel_points(model_wheel_positions, axle_flags)
        definition_paths = tuple(self._definition_key(path) for path in definitions)
        return GameContour(tuple(hull), source, tuple(model_paths), tuple(wheel_points), axle_count, definition_paths)

    def _definition_path(self, game_path: str) -> Path:
        relative = game_path.replace("/", "\\").lstrip("\\")
        if relative.lower().startswith("def\\"):
            relative = relative[4:]
        return self.def_root / Path(relative)

    def _vehicle_path(self, game_path: str) -> Path:
        relative = game_path.replace("/", "\\").lstrip("\\")
        return self.vehicle_root / Path(relative)

    def _point_span(self, wheels: Sequence[WheelPoint], hook: tuple[float, float] | None) -> float:
        zs = [wheel.local_z for wheel in wheels]
        if hook is not None:
            zs.append(hook[1])
        return max(zs) - min(zs) if zs else 0.0

    def _best_collision_variant(
        self, path: Path, reference_points: Sequence[tuple[float, float]]
    ) -> tuple[list[tuple[float, float]], int, int]:
        with path.open("rb") as stream:
            header = stream.read(12)
        if len(header) < 12:
            raise GameGeometryError(f"PMC file is truncated: {path}")
        variant_count = struct.unpack_from("<I", header, 8)[0]
        count = max(variant_count, 1)
        candidates: list[tuple[float, float, int, list[tuple[float, float]]]] = []
        for index in range(count):
            try:
                contour = pmc_top_down_contour(path, variant_index=index)
            except (GameGeometryError, struct.error):
                continue
            if len(contour) < 3:
                continue
            xs = [point[0] for point in contour]
            zs = [point[1] for point in contour]
            min_x, max_x, min_z, max_z = min(xs), max(xs), min(zs), max(zs)
            width = max_x - min_x
            length = max_z - min_z
            missing = 0.0
            for x, z in reference_points:
                missing += max(min_x - x - 0.65, 0.0, x - max_x - 0.65)
                missing += max(min_z - z - 0.65, 0.0, z - max_z - 0.65)
            implausible = max(1.5 - width, 0.0) + max(width - 3.8, 0.0)
            score = missing * 100.0 + implausible * 30.0
            candidates.append((score, -(width * length), index, contour))
        if not candidates:
            raise GameGeometryError(f"PMC has no usable collision variants: {path}")
        score, _negative_area, index, contour = min(candidates, key=lambda item: (item[0], item[1], item[2]))
        return contour, index, variant_count

    def _trailer_definition_dir(self, token: str) -> tuple[Path | None, bool]:
        owned_root = self.def_root / "vehicle" / "trailer_owned"
        exact = owned_root / token
        if exact.is_dir():
            return exact, False

        family = token.split(".ch_", 1)[0]
        compact_family = self._compact_token(family)

        legacy_root = self.def_root / "vehicle" / "trailer"
        hierarchical = legacy_root.joinpath(*family.split("."))
        if hierarchical.is_dir():
            return hierarchical, True
        base_family = family.split(".", 1)[0]
        base_family_dir = legacy_root / base_family
        if base_family_dir.is_dir():
            return base_family_dir, True
        legacy_names = (family, family.replace(".", "_"), family.replace("_", "."))
        for name in legacy_names:
            path = legacy_root / name
            if path.is_dir():
                return path, True
        for path in legacy_root.iterdir() if legacy_root.is_dir() else ():
            if path.is_dir() and self._compact_token(path.name) == compact_family:
                return path, True
        for path in owned_root.iterdir() if owned_root.is_dir() else ():
            if path.is_dir() and self._compact_token(path.name) == compact_family:
                return path, False
        return None, False

    def _reference_points(
        self, wheels: Sequence[WheelPoint], hook: tuple[float, float] | None
    ) -> tuple[tuple[float, float], ...]:
        points = [(wheel.local_x, wheel.local_z) for wheel in wheels]
        if hook is not None:
            points.append(hook)
        return tuple(points)

    def _wheel_signature(self, wheels: Sequence[WheelPoint]) -> str:
        return ";".join(
            f"{wheel.local_x:.2f},{wheel.local_z:.2f},"
            f"{int(bool(getattr(wheel, 'steerable', False)))},"
            f"{int(bool(getattr(wheel, 'powered', False)))},"
            f"{int(bool(getattr(wheel, 'liftable', False)))}"
            for wheel in wheels
        )

    def _axle_properties(self, wheels: Sequence[WheelPoint], tolerance: float = 0.35) -> list[tuple[bool, bool, bool]]:
        groups: list[list[WheelPoint]] = []
        for wheel in wheels:
            if groups and abs(wheel.local_z - sum(item.local_z for item in groups[-1]) / len(groups[-1])) <= tolerance:
                groups[-1].append(wheel)
            else:
                groups.append([wheel])
        return [
            (
                any(bool(getattr(wheel, "steerable", False)) for wheel in group),
                any(bool(getattr(wheel, "powered", False)) for wheel in group),
                any(bool(getattr(wheel, "liftable", False)) for wheel in group),
            )
            for group in groups
        ]

    def _definition_flags(
        self,
        attributes: dict[str, list[str]],
        key: str,
        count: int,
        first_default: bool = False,
    ) -> list[bool]:
        values = _bool_values(attributes, key)
        if not values:
            values = [first_default] + [False] * (count - 1) if count else []
        return (values + [False] * count)[:count]

    def _definition_axle_z_positions(self, attributes: dict[str, list[str]]) -> list[float]:
        for collision_path in attributes.get("collision", []):
            pmg_path = self._vehicle_path(collision_path).with_suffix(".pmg")
            if not pmg_path.is_file():
                continue
            try:
                points = read_pmg_wheel_points(pmg_path)
            except (OSError, GameGeometryError, struct.error):
                continue
            return self._group_z_positions(z for _x, z in points)
        return []

    def _axle_z_positions(self, wheels: Sequence[WheelPoint]) -> list[float]:
        return self._group_z_positions(wheel.local_z for wheel in wheels)

    def _group_z_positions(self, values: Iterable[float], tolerance: float = 0.35) -> list[float]:
        groups: list[list[float]] = []
        for value in sorted(values):
            if groups and abs(value - sum(groups[-1]) / len(groups[-1])) <= tolerance:
                groups[-1].append(value)
            else:
                groups.append([value])
        return [sum(group) / len(group) for group in groups]

    def _definition_axles(
        self, definitions: Sequence[Path]
    ) -> tuple[int, list[tuple[bool, bool, bool]]]:
        selected: dict[str, list[str]] = {}
        axle_count = 0
        for definition in definitions:
            attributes = read_sii_attributes(definition)
            count = len(attributes.get("residual_travel", []))
            if count > axle_count:
                axle_count = count
                selected = attributes
        if axle_count == 0:
            return 0, []
        steerable = self._definition_flags(selected, "steerable_axle", axle_count)
        powered = self._definition_flags(selected, "powered_axle", axle_count)
        liftable = self._definition_flags(selected, "liftable_axle", axle_count)
        return axle_count, list(zip(steerable, powered, liftable))

    def _model_wheel_points(
        self,
        positions: Sequence[tuple[float, float]],
        axle_flags: Sequence[tuple[bool, bool, bool]],
        tolerance: float = 0.35,
    ) -> list[GameWheelPoint]:
        groups: list[list[tuple[float, float]]] = []
        for point in sorted(positions, key=lambda item: (item[1], item[0])):
            if groups and abs(point[1] - sum(item[1] for item in groups[-1]) / len(groups[-1])) <= tolerance:
                groups[-1].append(point)
            else:
                groups.append([point])
        wheels: list[GameWheelPoint] = []
        for axle_index, group in enumerate(groups):
            flags = axle_flags[axle_index] if axle_index < len(axle_flags) else (False, False, False)
            wheels.extend(GameWheelPoint(x, z, *flags) for x, z in group)
        return wheels

    def _compact_token(self, value: str) -> str:
        return re.sub(r"[^a-z0-9]", "", value.lower())

    def _definition_candidates(self, directory: Path) -> list[str]:
        if not directory.is_dir():
            return []
        candidates = []
        for path in directory.glob("*.sii"):
            attributes = read_sii_attributes(path)
            if any(key in attributes for key in ("collision", "model", "detail_model", "body_type", "total_size")):
                candidates.append(self._definition_key(path))
        return sorted(candidates)

    def _definition_key(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.def_root.resolve()).as_posix()
        except ValueError:
            return path.as_posix()

    def _selected_definition(
        self,
        selection: dict[str, str] | None,
        field: str,
        candidates: Sequence[str],
    ) -> Path | None:
        if not selection:
            return None
        selected = selection.get(field, "")
        if selected not in candidates:
            return None
        path = self.def_root / Path(selected)
        return path if path.is_file() else None

    def _selection_signature(self, selection: dict[str, str] | None) -> str:
        if not selection:
            return "auto"
        return ";".join(f"{key}={selection[key]}" for key in sorted(selection))

    def _powered_axle_count(self, wheels: Sequence[WheelPoint], tolerance: float = 0.35) -> int:
        powered_zs = sorted((wheel.local_z for wheel in wheels if wheel.powered), reverse=True)
        groups: list[list[float]] = []
        for z in powered_zs:
            if groups and abs(z - sum(groups[-1]) / len(groups[-1])) <= tolerance:
                groups[-1].append(z)
            else:
                groups.append([z])
        return len(groups)

"""Adapters from ETS2LA/SCS telemetry dictionaries to core dataclasses."""

from __future__ import annotations

from typing import Any

from .geometry import normalize_angle, scs_rotation_to_radians
from .models import Pose2D, VehicleState


class TelemetryError(ValueError):
    pass


def _num(data: dict[str, Any], *path: str, default: float | None = None) -> float:
    cursor: Any = data
    for key in path:
        if not isinstance(cursor, dict) or key not in cursor:
            if default is not None:
                return default
            raise TelemetryError("missing telemetry field: " + ".".join(path))
        cursor = cursor[key]
    try:
        return float(cursor)
    except (TypeError, ValueError) as exc:
        raise TelemetryError("invalid telemetry field: " + ".".join(path)) from exc


def pose_from_placement(placement: dict[str, Any]) -> Pose2D:
    return Pose2D(
        x=_num(placement, "coordinateX"),
        z=_num(placement, "coordinateZ"),
        yaw=scs_rotation_to_radians(_num(placement, "rotationX")),
    )


def attached_trailers(api_data: dict[str, Any]) -> list[dict[str, Any]]:
    trailers = api_data.get("trailers") or []
    return [trailer for trailer in trailers if trailer.get("comBool", {}).get("attached")]


def vehicle_signature(api_data: dict[str, Any], trailer: dict[str, Any] | None) -> str:
    config = api_data.get("configString", {})
    trailer_strings = (trailer or {}).get("conString", {})
    trailer_ui = (trailer or {}).get("conUI", {})
    parts = [
        config.get("truckId", "unknown_truck"),
        config.get("cargoId", "unknown_cargo"),
        trailer_strings.get("bodyType", "unknown_body"),
        trailer_strings.get("chainType", "unknown_chain"),
        str(trailer_ui.get("wheelCount", "unknown_wheels")),
    ]
    return "|".join(str(part).strip() or "unknown" for part in parts)


def vehicle_state_from_api(api_data: dict[str, Any]) -> VehicleState:
    trailers = attached_trailers(api_data)
    truck_pose = pose_from_placement(api_data["truckPlacement"])
    if len(trailers) != 1:
        trailer_pose = truck_pose
        trailer_count = len(trailers)
        signature = vehicle_signature(api_data, None)
    else:
        trailer = trailers[0]
        trailer_pose = Pose2D(
            x=_num(trailer, "comDouble", "worldX"),
            z=_num(trailer, "comDouble", "worldZ"),
            yaw=scs_rotation_to_radians(_num(trailer, "comDouble", "rotationX")),
        )
        trailer_count = 1
        signature = vehicle_signature(api_data, trailer)

    return VehicleState(
        truck_pose=truck_pose,
        trailer_pose=trailer_pose,
        speed=_num(api_data, "truckFloat", "speed", default=0.0),
        gear=int(_num(api_data, "truckInt", "gearDashboard", default=0.0)),
        articulation_angle=normalize_angle(truck_pose.yaw - trailer_pose.yaw),
        vehicle_signature=signature,
        trailer_count=trailer_count,
        paused=bool(api_data.get("pause", False)),
        user_throttle=_num(api_data, "truckFloat", "userThrottle", default=0.0),
        user_brake=_num(api_data, "truckFloat", "userBrake", default=0.0),
    )

"""Geometry helpers for world-space reversing control."""

from __future__ import annotations

import math


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def normalize_angle(angle: float) -> float:
    """Normalize radians to [-pi, pi]."""
    while angle > math.pi:
        angle -= math.tau
    while angle < -math.pi:
        angle += math.tau
    return angle


def scs_rotation_to_radians(value: float) -> float:
    """Convert SCS telemetry rotation values to radians.

    ETS2LA forwards SCS placement rotation values directly. In practice these
    are usually normalized turns in [0, 1), while recorded/simulated data can be
    radians. This accepts both forms so tests and future standalone tools can
    feed ordinary radians without special casing.
    """
    if -1.5 <= value <= 1.5:
        return normalize_angle(value * math.tau)
    return normalize_angle(value)


def signed_angle_delta(current: float, target: float) -> float:
    return normalize_angle(current - target)


def distance_2d(ax: float, az: float, bx: float, bz: float) -> float:
    return math.hypot(ax - bx, az - bz)


def vehicle_frame_vectors(yaw: float) -> tuple[tuple[float, float], tuple[float, float]]:
    """Return planar forward and right vectors for an SCS placement yaw."""
    forward = (math.sin(yaw), -math.cos(yaw))
    right = (math.cos(yaw), math.sin(yaw))
    return forward, right


def offset_in_vehicle_frame(
    x: float,
    z: float,
    yaw: float,
    forward: float = 0.0,
    right: float = 0.0,
) -> tuple[float, float]:
    """Move a planar world point using an SCS vehicle's front/right axes."""
    forward_axis, right_axis = vehicle_frame_vectors(yaw)
    return (
        x + forward_axis[0] * forward + right_axis[0] * right,
        z + forward_axis[1] * forward + right_axis[1] * right,
    )


def target_frame_error(current_x: float, current_z: float, target_x: float, target_z: float, target_yaw: float) -> tuple[float, float]:
    """Return longitudinal and lateral error in the target's frame."""
    dx = current_x - target_x
    dz = current_z - target_z
    (forward_x, forward_z), (right_x, right_z) = vehicle_frame_vectors(target_yaw)
    longitudinal = dx * forward_x + dz * forward_z
    lateral = dx * right_x + dz * right_z
    return longitudinal, lateral

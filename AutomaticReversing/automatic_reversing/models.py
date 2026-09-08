"""Shared dataclasses used by the reversing controller and adapters."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Pose2D:
    x: float
    z: float
    yaw: float


@dataclass(frozen=True)
class VehicleState:
    truck_pose: Pose2D
    trailer_pose: Pose2D
    speed: float
    gear: int
    articulation_angle: float
    vehicle_signature: str = "unknown"
    trailer_count: int = 1
    paused: bool = False
    user_throttle: float = 0.0
    user_brake: float = 0.0


@dataclass(frozen=True)
class TargetPose:
    pose: Pose2D
    tolerance_m: float = 0.75
    tolerance_deg: float = 8.0


@dataclass(frozen=True)
class ControlCommand:
    steering: float = 0.0
    throttle: float = 0.0
    brake: float = 0.0
    reverse: bool = False
    drive: bool = False
    active: bool = False
    reason: str = "idle"


@dataclass(frozen=True)
class ControllerConfig:
    target_speed_kmh: float = 1.5
    max_speed_kmh: float = 3.0
    max_articulation_deg: float = 45.0
    steering_gain_lateral: float = 0.18
    steering_gain_heading: float = 0.75
    steering_gain_articulation: float = 0.45
    max_steering: float = 0.85
    max_steering_delta_per_s: float = 1.6
    throttle: float = 0.18
    brake_when_fast: float = 0.35
    stop_brake: float = 0.8
    user_throttle_cancel_threshold: float = 0.2
    user_brake_cancel_threshold: float = 0.2
    steering_sign: float = -1.0

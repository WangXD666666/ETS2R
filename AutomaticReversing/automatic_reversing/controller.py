"""Low-speed trailer-first parking controller."""

from __future__ import annotations

import math
import time

from .geometry import clamp, distance_2d, signed_angle_delta, target_frame_error, vehicle_frame_vectors
from .models import ControlCommand, ControllerConfig, TargetPose, VehicleState


class ReversingController:
    """Stateful controller for one-button forward/reverse parking.

    A motion direction is selected once per run from the target's position
    relative to the truck. The controller then uses low throttle and steering,
    and fails closed when the target is reached or telemetry becomes unsafe.
    """

    def __init__(self, config: ControllerConfig | None = None) -> None:
        self.config = config or ControllerConfig()
        self.target: TargetPose | None = None
        self.active = False
        self.last_steering = 0.0
        self.last_update_time: float | None = None
        self.motion_reverse: bool | None = None
        self.last_output_throttle = 0.0
        self.last_output_brake = 0.0
        self.last_reason = "idle"

    def set_config(self, config: ControllerConfig) -> None:
        self.config = config

    def set_target(self, target: TargetPose) -> None:
        self.target = target
        self.last_reason = "target_set"

    def clear_target(self, reason: str = "target_cleared") -> ControlCommand:
        self.target = None
        if self.active:
            return self.cancel(reason)
        self.last_steering = 0.0
        self.last_update_time = None
        self.last_reason = reason
        return ControlCommand(reason=reason)

    def start(self) -> bool:
        if self.target is None:
            self.last_reason = "no_target"
            return False
        self.active = True
        self.last_update_time = None
        self.motion_reverse = None
        self.last_output_throttle = 0.0
        self.last_output_brake = 0.0
        self.last_reason = "active"
        return True

    def cancel(self, reason: str = "cancelled") -> ControlCommand:
        self.active = False
        self.last_steering = 0.0
        self.last_update_time = None
        self.motion_reverse = None
        self.last_output_throttle = 0.0
        self.last_output_brake = 0.0
        self.last_reason = reason
        return ControlCommand(brake=self.config.stop_brake, active=False, reason=reason)

    @staticmethod
    def _is_manual_pedal(value: float, previous_output: float, threshold: float) -> bool:
        if value < threshold:
            return False
        return previous_output <= 0.0 or abs(value - previous_output) > 0.03

    def update(self, state: VehicleState | None, now: float | None = None) -> ControlCommand:
        if now is None:
            now = time.time()
        if state is None:
            return self.cancel("missing_telemetry")
        if not self.active:
            return ControlCommand(reason=self.last_reason)
        if self.target is None:
            return self.cancel("no_target")
        if state.paused:
            return self.cancel("game_paused")
        if self._is_manual_pedal(
            state.user_brake,
            self.last_output_brake,
            self.config.user_brake_cancel_threshold,
        ):
            return self.cancel("user_brake")
        if self._is_manual_pedal(
            state.user_throttle,
            self.last_output_throttle,
            self.config.user_throttle_cancel_threshold,
        ):
            return self.cancel("user_throttle")
        if state.trailer_count != 1:
            return self.cancel("unsupported_trailer_count")

        articulation_limit = math.radians(self.config.max_articulation_deg)
        if abs(state.articulation_angle) > articulation_limit:
            return self.cancel("articulation_limit")

        target = self.target
        distance = distance_2d(state.trailer_pose.x, state.trailer_pose.z, target.pose.x, target.pose.z)
        heading_error = signed_angle_delta(state.trailer_pose.yaw, target.pose.yaw)
        if distance <= target.tolerance_m and abs(math.degrees(heading_error)) <= target.tolerance_deg:
            if abs(state.speed) > 0.05:
                self.last_reason = "target_stopping"
                self.last_output_throttle = 0.0
                self.last_output_brake = self.config.stop_brake
                return ControlCommand(brake=self.config.stop_brake, active=True, reason=self.last_reason)
            return self.cancel("target_reached")

        longitudinal, lateral = target_frame_error(
            state.trailer_pose.x,
            state.trailer_pose.z,
            target.pose.x,
            target.pose.z,
            target.pose.yaw,
        )
        if self.motion_reverse is None:
            forward_axis, _ = vehicle_frame_vectors(state.truck_pose.yaw)
            target_dx = target.pose.x - state.trailer_pose.x
            target_dz = target.pose.z - state.trailer_pose.z
            target_ahead = target_dx * forward_axis[0] + target_dz * forward_axis[1]
            self.motion_reverse = target_ahead <= 0.0

        raw_steering = self.config.steering_sign * (
            self.config.steering_gain_lateral * lateral
            + self.config.steering_gain_heading * heading_error
            + self.config.steering_gain_articulation * state.articulation_angle
        )
        if not self.motion_reverse:
            raw_steering = -raw_steering
        raw_steering = clamp(raw_steering, -self.config.max_steering, self.config.max_steering)

        if self.last_update_time is None:
            dt = 0.05
        else:
            dt = max(0.001, min(0.25, now - self.last_update_time))
        self.last_update_time = now

        max_delta = self.config.max_steering_delta_per_s * dt
        steering = clamp(raw_steering, self.last_steering - max_delta, self.last_steering + max_delta)
        self.last_steering = steering

        speed_kmh = abs(state.speed) * 3.6
        if speed_kmh > self.config.max_speed_kmh:
            throttle = 0.0
            brake = self.config.brake_when_fast
            reason = "slowing"
        elif speed_kmh < self.config.target_speed_kmh:
            throttle = self.config.throttle
            brake = 0.0
            reason = "active"
        else:
            throttle = 0.0
            brake = 0.0
            reason = "coasting"

        # Retain longitudinal in the reason for debugging without changing the
        # public command shape.
        direction = "reverse" if self.motion_reverse else "forward"
        self.last_reason = f"{reason}_{direction}:dist={distance:.2f}:long={longitudinal:.2f}:lat={lateral:.2f}:yaw={math.degrees(heading_error):.1f}"
        self.last_output_throttle = throttle
        self.last_output_brake = brake
        return ControlCommand(
            steering=steering,
            throttle=throttle,
            brake=brake,
            reverse=self.motion_reverse,
            drive=not self.motion_reverse,
            active=True,
            reason=self.last_reason,
        )

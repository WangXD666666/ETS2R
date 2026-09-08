import math

from automatic_reversing.controller import ReversingController
from automatic_reversing.models import ControllerConfig, Pose2D, TargetPose, VehicleState


def state(
    trailer_x=0.0,
    trailer_z=5.0,
    trailer_yaw=0.0,
    truck_yaw=0.0,
    speed=0.0,
    trailer_count=1,
    paused=False,
    user_throttle=0.0,
    user_brake=0.0,
):
    return VehicleState(
        truck_pose=Pose2D(0.0, 7.0, truck_yaw),
        trailer_pose=Pose2D(trailer_x, trailer_z, trailer_yaw),
        speed=speed,
        gear=-1,
        articulation_angle=truck_yaw - trailer_yaw,
        trailer_count=trailer_count,
        paused=paused,
        user_throttle=user_throttle,
        user_brake=user_brake,
    )


def active_controller():
    controller = ReversingController(ControllerConfig(max_steering_delta_per_s=100.0))
    controller.set_target(TargetPose(Pose2D(0.0, 0.0, 0.0)))
    assert controller.start()
    return controller


def test_controller_outputs_reverse_low_speed_command():
    command = active_controller().update(state(trailer_x=1.0, trailer_z=-5.0), now=10.0)

    assert command.active
    assert command.reverse
    assert command.throttle > 0
    assert command.brake == 0
    assert -1.0 <= command.steering <= 1.0


def test_controller_selects_drive_when_target_is_in_front_of_truck():
    command = active_controller().update(state(trailer_z=5.0), now=10.0)

    assert command.active
    assert command.drive
    assert not command.reverse
    assert command.reason.startswith("active_forward")


def test_controller_locks_motion_direction_until_restarted():
    controller = active_controller()
    first = controller.update(state(trailer_z=-5.0), now=10.0)
    second = controller.update(state(trailer_z=5.0), now=10.1)

    assert first.reverse
    assert second.reverse


def test_controller_brakes_when_too_fast():
    command = active_controller().update(state(speed=2.0), now=10.0)

    assert command.active
    assert command.throttle == 0
    assert command.brake > 0
    assert command.reason.startswith("slowing")


def test_controller_ignores_its_own_throttle_and_brake_telemetry_echo():
    throttle_controller = active_controller()
    throttle_command = throttle_controller.update(state(trailer_z=-5.0), now=10.0)
    throttle_echo = throttle_controller.update(
        state(trailer_z=-5.0, user_throttle=throttle_command.throttle),
        now=10.1,
    )

    brake_controller = active_controller()
    brake_command = brake_controller.update(state(trailer_z=-5.0, speed=2.0), now=10.0)
    brake_echo = brake_controller.update(
        state(trailer_z=-5.0, speed=2.0, user_brake=brake_command.brake),
        now=10.1,
    )

    assert throttle_echo.active
    assert brake_echo.active


def test_controller_cancels_on_articulation_limit():
    controller = active_controller()
    command = controller.update(state(truck_yaw=math.radians(60)), now=10.0)

    assert not command.active
    assert command.brake > 0
    assert command.reason == "articulation_limit"


def test_controller_cancels_on_user_brake_pause_and_trailer_count():
    for kwargs, reason in [
        ({"user_brake": 0.3}, "user_brake"),
        ({"user_throttle": 0.3}, "user_throttle"),
        ({"paused": True}, "game_paused"),
        ({"trailer_count": 0}, "unsupported_trailer_count"),
        ({"trailer_count": 2}, "unsupported_trailer_count"),
    ]:
        controller = active_controller()
        command = controller.update(state(**kwargs), now=10.0)
        assert not command.active
        assert command.reason == reason


def test_controller_stops_at_target():
    controller = active_controller()
    command = controller.update(state(trailer_x=0.1, trailer_z=0.1), now=10.0)

    assert not command.active
    assert command.brake > 0
    assert command.reason == "target_reached"


def test_controller_brakes_until_vehicle_stops_inside_target():
    controller = active_controller()
    command = controller.update(state(trailer_x=0.1, trailer_z=0.1, speed=0.2), now=10.0)

    assert command.active
    assert command.brake > 0
    assert not command.drive
    assert not command.reverse
    assert command.reason == "target_stopping"


def test_steering_rate_limit():
    config = ControllerConfig(max_steering_delta_per_s=1.0)
    controller = ReversingController(config)
    controller.set_target(TargetPose(Pose2D(0.0, 0.0, 0.0)))
    controller.start()

    first = controller.update(state(trailer_x=5.0), now=10.0)
    second = controller.update(state(trailer_x=-5.0), now=10.1)

    assert abs(second.steering - first.steering) <= 0.1000001


def test_clear_target_only_brakes_when_controller_was_active():
    idle = ReversingController()
    idle.set_target(TargetPose(Pose2D(0.0, 0.0, 0.0)))

    idle_command = idle.clear_target("vehicle_changed")
    active = active_controller()
    active_command = active.clear_target("vehicle_changed")

    assert idle.target is None
    assert idle_command.brake == 0.0
    assert active.target is None
    assert active_command.brake > 0.0

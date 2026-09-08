import math

from automatic_reversing.models import ControlCommand, Pose2D, TargetPose, VehicleState
from automatic_reversing_app.app import AutomaticReversingApp


class TargetRecorder:
    def __init__(self):
        self.target = None

    def set_target(self, target):
        self.target = target


def make_app(state: VehicleState) -> AutomaticReversingApp:
    app = AutomaticReversingApp.__new__(AutomaticReversingApp)
    app.last_state = state
    app.session_target = None
    app.controller = TargetRecorder()
    app._float_setting = lambda key: {"tolerance_m": 0.75, "tolerance_deg": 8.0}[key]
    app.save_vehicle_profile = lambda show_message=False: None
    app.status = ""
    return app


def vehicle_state(truck_yaw: float, trailer_yaw: float) -> VehicleState:
    return VehicleState(
        truck_pose=Pose2D(10.0, 20.0, truck_yaw),
        trailer_pose=Pose2D(7.0, 12.0, trailer_yaw),
        speed=0.0,
        gear=0,
        articulation_angle=truck_yaw - trailer_yaw,
    )


def test_set_target_uses_trailer_position_and_truck_heading_for_straight_bay():
    app = make_app(vehicle_state(math.pi / 2, -0.4))

    app.set_target()

    assert app.session_target is not None
    assert app.session_target.pose.x == 7.0
    assert app.session_target.pose.z == 12.0
    assert app.session_target.pose.yaw == math.pi / 2
    assert app.controller.target == app.session_target


def test_target_nudges_use_current_truck_front_and_right_axes():
    app = make_app(vehicle_state(0.0, 0.7))
    app.session_target = TargetPose(Pose2D(7.0, 12.0, 0.3))

    app.nudge_target(forward=2.0, lateral=-1.0)

    assert app.session_target is not None
    assert math.isclose(app.session_target.pose.x, 6.0)
    assert math.isclose(app.session_target.pose.z, 10.0)
    assert math.isclose(app.session_target.pose.yaw, 0.3)


def test_rotating_bay_does_not_change_truck_relative_translation_reference():
    app = make_app(vehicle_state(math.pi / 2, 0.0))
    app.session_target = TargetPose(Pose2D(7.0, 12.0, -1.0))

    app.nudge_target(forward=2.0, yaw_delta=0.5)

    assert app.session_target is not None
    assert math.isclose(app.session_target.pose.x, 9.0)
    assert math.isclose(app.session_target.pose.z, 12.0)
    assert math.isclose(app.session_target.pose.yaw, -0.5)


def test_tick_releases_controls_immediately_after_controller_cancels():
    class Controller:
        def set_config(self, _config):
            pass

        def update(self, _state):
            return ControlCommand(brake=0.8, active=False, reason="user_brake")

    class Root:
        def after(self, _delay, _callback):
            pass

    app = AutomaticReversingApp.__new__(AutomaticReversingApp)
    app.controller = Controller()
    app.last_state = vehicle_state(0.0, 0.0)
    app.root = Root()
    app._read_telemetry_once = lambda: True
    app._config_from_settings = lambda: object()
    app._localize_message = lambda reason: reason
    app._refresh_labels = lambda: None
    written = []
    app._write_command = written.append

    app._tick()

    assert len(written) == 1
    assert written[0].brake == 0.0
    assert written[0].throttle == 0.0
    assert not written[0].drive
    assert not written[0].reverse
    assert written[0].reason == "user_brake"

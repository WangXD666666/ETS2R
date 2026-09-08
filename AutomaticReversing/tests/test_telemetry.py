import math

from automatic_reversing.telemetry import TelemetryError, attached_trailers, vehicle_state_from_api


def api_payload(trailers):
    return {
        "pause": False,
        "truckPlacement": {
            "coordinateX": 10.0,
            "coordinateY": 0.0,
            "coordinateZ": 20.0,
            "rotationX": 0.0,
        },
        "truckFloat": {
            "speed": -0.2,
            "userThrottle": 0.15,
            "userBrake": 0.0,
        },
        "truckInt": {
            "gearDashboard": -1,
        },
        "configString": {
            "truckId": "truck.a",
            "cargoId": "cargo.b",
        },
        "trailers": trailers,
    }


def trailer(attached=True, yaw=0.25):
    return {
        "comBool": {"attached": attached},
        "comDouble": {
            "worldX": 11.0,
            "worldY": 0.0,
            "worldZ": 19.0,
            "rotationX": yaw,
        },
        "conString": {
            "bodyType": "box",
            "chainType": "single",
        },
        "conUI": {
            "wheelCount": 6,
        },
    }


def test_attached_trailers_filters_detached_entries():
    trailers = attached_trailers(api_payload([trailer(False), trailer(True)]))
    assert len(trailers) == 1


def test_vehicle_state_extracts_single_trailer_pose_and_signature():
    state = vehicle_state_from_api(api_payload([trailer(True)]))

    assert state.trailer_count == 1
    assert state.trailer_pose.x == 11.0
    assert state.trailer_pose.z == 19.0
    assert math.isclose(state.trailer_pose.yaw, math.pi / 2)
    assert state.speed == -0.2
    assert state.gear == -1
    assert state.user_throttle == 0.15
    assert state.vehicle_signature == "truck.a|cargo.b|box|single|6"


def test_vehicle_state_marks_no_or_multiple_trailers_as_unsupported():
    assert vehicle_state_from_api(api_payload([])).trailer_count == 0
    assert vehicle_state_from_api(api_payload([trailer(True), trailer(True)])).trailer_count == 2


def test_vehicle_state_requires_truck_placement():
    payload = api_payload([trailer(True)])
    del payload["truckPlacement"]

    try:
        vehicle_state_from_api(payload)
    except (KeyError, TelemetryError):
        return
    raise AssertionError("expected telemetry parsing to fail")

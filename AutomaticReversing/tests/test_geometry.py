import math

from automatic_reversing.geometry import (
    normalize_angle,
    offset_in_vehicle_frame,
    scs_rotation_to_radians,
    target_frame_error,
    vehicle_frame_vectors,
)


def test_normalize_angle_wraps_to_signed_pi():
    assert math.isclose(normalize_angle(math.tau + 0.2), 0.2)
    assert math.isclose(normalize_angle(-math.tau - 0.3), -0.3)


def test_scs_rotation_accepts_turns_and_radians():
    assert math.isclose(scs_rotation_to_radians(0.25), math.pi / 2)
    assert math.isclose(scs_rotation_to_radians(math.pi / 2), math.pi / 2)


def test_target_frame_error_uses_target_heading():
    longitudinal, lateral = target_frame_error(8.0, -1.0, 8.0, 1.0, 0.0)
    assert math.isclose(longitudinal, 2.0)
    assert math.isclose(lateral, 0.0)

    longitudinal, lateral = target_frame_error(10.0, 1.0, 8.0, 1.0, math.pi / 2)
    assert math.isclose(longitudinal, 2.0)
    assert abs(lateral) < 1e-9


def test_scs_vehicle_frame_uses_negative_z_as_forward_at_zero_yaw():
    forward, right = vehicle_frame_vectors(0.0)

    assert forward == (0.0, -1.0)
    assert right == (1.0, 0.0)
    assert offset_in_vehicle_frame(10.0, 20.0, 0.0, forward=3.0) == (10.0, 17.0)
    assert offset_in_vehicle_frame(10.0, 20.0, 0.0, right=-2.0) == (8.0, 20.0)


def test_scs_vehicle_frame_rotates_with_truck_yaw():
    x, z = offset_in_vehicle_frame(10.0, 20.0, math.pi / 2, forward=3.0, right=2.0)

    assert math.isclose(x, 13.0)
    assert math.isclose(z, 22.0)

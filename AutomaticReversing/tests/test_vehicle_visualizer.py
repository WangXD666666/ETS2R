import math
from copy import deepcopy
from types import SimpleNamespace

from automatic_reversing.game_geometry import GameContour, GameWheelPoint
from automatic_reversing_visualizer.app import Pose, VehicleGeometryVisualizer, WheelLayout


def make_viewer(data, dimensions=None):
    viewer = VehicleGeometryVisualizer.__new__(VehicleGeometryVisualizer)
    viewer.data = data
    viewer.dimensions = dimensions or {"truck": {}, "trailer": {}}
    viewer.game_geometry = SimpleNamespace(available=False, clear_cache=lambda: None)
    viewer.configuration_signature = None
    viewer.pending_configuration_signature = None
    viewer.pending_configuration_frames = 0
    viewer.configuration_generation = 0
    return viewer


def sample_data():
    return {
        "truckPlacement": {"coordinateX": 0.0, "coordinateZ": 0.0, "rotationX": 0.25},
        "configString": {"truckId": "truck.a", "truckName": "Truck A"},
        "configUI": {"truckWheelCount": 4},
        "configFloat": {"truckWheelRadius": [0.52, 0.52, 0.52, 0.52]},
        "configBool": {
            "truckWheelSteerable": [True, True, False, False],
            "truckWheelPowered": [False, False, True, True],
            "truckWheelLiftable": [False, False, False, False],
        },
        "configVector": {
            "truckWheelPositionX": [-1.0, 1.0, -1.0, 1.0],
            "truckWheelPositionZ": [2.0, 2.0, -2.0, -2.0],
            "truckHookPositionX": 0.0,
            "truckHookPositionZ": -1.5,
        },
        "truckFloat": {
            "truck_wheelSteering": [0.1, 0.1, 0.0, 0.0],
            "truck_wheelVelocity": [1.0, 1.0, 1.0, 1.0],
        },
        "trailers": [
            {
                "comBool": {"attached": True},
                "comDouble": {"worldX": 20.0, "worldZ": -30.0, "rotationX": 0.125},
                "conUI": {"wheelCount": 4},
                "conFloat": {"wheelRadius": [0.49, 0.49, 0.49, 0.49]},
                "conBool": {
                    "wheelSteerable": [False, False, False, False],
                    "wheelPowered": [False, False, False, False],
                    "wheelLiftable": [False, False, False, False],
                },
                "conVector": {
                    "wheelPositionX": [-1.0, 1.0, -1.0, 1.0],
                    "wheelPositionZ": [-3.0, -3.0, -4.0, -4.0],
                    "hookPositionX": 0.0,
                    "hookPositionZ": 3.0,
                },
                "comFloat": {
                    "wheelSteering": [0.0, 0.0, 0.0, 0.0],
                    "wheelVelocity": [0.0, 0.0, 0.0, 0.0],
                },
                "conString": {"id": "trailer.a", "bodyType": "box", "chainType": "single", "name": "Box"},
            }
        ],
    }


def test_vehicle_visualizer_builds_truck_and_trailer_layouts():
    viewer = make_viewer(sample_data())

    units = viewer._build_layouts()
    viewer.last_units = units

    assert len(units) == 2
    assert units[0].name == "车头"
    assert len(units[0].wheels) == 4
    assert units[0].hook == (0.0, -1.5)
    assert units[0].dimensions_source == "遥测点位跨度（非车身尺寸）"
    assert units[0].body_bounds is None
    assert units[1].name == "Box"
    assert len(units[1].wheels) == 4
    assert units[1].aligned_to_coupling is True
    assert math.isclose(units[0].length, 4.0)
    assert len(viewer._axle_groups(units[0].wheels)) == 2
    assert len(viewer._axle_groups(units[1].wheels)) == 2
    assert viewer._articulation_text().startswith("+45.00")


def test_dimension_table_overrides_estimated_body_size():
    viewer = make_viewer(
        sample_data(),
        dimensions={
            "truck": {"truck.a": {"width": 2.55, "front_z": 4.2, "rear_z": -1.8, "verified": True}},
            "trailer": {"trailer.a|box|single|Box": {"width": 2.6, "front_z": 3.4, "rear_z": -10.2, "verified": True}},
        },
    )

    truck, trailer = viewer._build_layouts()

    assert truck.dimensions_source == "已验证外形尺寸表"
    assert truck.body_bounds == truck.bounds
    assert math.isclose(truck.width, 2.55)
    assert math.isclose(truck.length, 6.0)
    assert truck.bounds == (-1.275, 1.275, -1.8, 4.2)
    assert trailer.dimensions_source == "已验证外形尺寸表"
    assert math.isclose(trailer.width, 2.6)
    assert math.isclose(trailer.length, 13.6)


def test_connected_trailer_hook_is_drawn_on_truck_saddle():
    viewer = make_viewer(sample_data())
    units = viewer._build_layouts()

    truck_hook = viewer._unit_hook_world(units[0])
    trailer_hook = viewer._unit_hook_world(units[1])

    assert truck_hook is not None
    assert trailer_hook is not None
    assert math.isclose(truck_hook[0], trailer_hook[0], abs_tol=1e-9)
    assert math.isclose(truck_hook[1], trailer_hook[1], abs_tol=1e-9)


def test_steerable_wheels_keep_steering_angle():
    viewer = make_viewer(sample_data())
    truck = viewer._build_layouts()[0]
    steerable = [wheel for wheel in truck.wheels if wheel.steerable]

    assert len(steerable) == 2
    assert all(math.isclose(wheel.steering, 0.1) for wheel in steerable)
    assert all(math.isclose(wheel.radius, 0.52) for wheel in steerable)


def test_unverified_dimensions_are_not_drawn_as_vehicle_body():
    viewer = make_viewer(
        sample_data(),
        dimensions={
            "truck": {"truck.a": {"width": 9.0, "front_z": 20.0, "rear_z": -20.0, "verified": False}},
            "trailer": {},
        },
    )

    truck = viewer._build_layouts()[0]

    assert truck.body_bounds is None
    assert truck.dimensions_source == "遥测点位跨度（非车身尺寸）"
    assert math.isclose(truck.length, 4.0)


def test_steering_rotates_the_visual_tire_shape():
    viewer = make_viewer(sample_data())
    pose = Pose(0.0, 0.0, 0.0)
    straight = WheelLayout(0.0, 0.0, 0.0, 0.0, True, 0.5)
    turned = WheelLayout(0.0, 0.0, math.pi / 2.0, 0.0, True, 0.5)

    straight_corners = viewer._wheel_corners_world(pose, straight)
    turned_corners = viewer._wheel_corners_world(pose, turned)
    straight_x_span = max(x for x, _ in straight_corners) - min(x for x, _ in straight_corners)
    straight_z_span = max(z for _, z in straight_corners) - min(z for _, z in straight_corners)
    turned_x_span = max(x for x, _ in turned_corners) - min(x for x, _ in turned_corners)
    turned_z_span = max(z for _, z in turned_corners) - min(z for _, z in turned_corners)

    assert straight_z_span > straight_x_span
    assert turned_x_span > turned_z_span


def test_zero_local_hook_coordinate_is_preserved():
    viewer = make_viewer(sample_data())

    assert viewer._optional_point(0.0, 0.0) == (0.0, 0.0)


def test_game_collision_contour_has_priority_over_dimension_table():
    contour = GameContour(
        points=((-1.4, -3.0), (1.4, -3.0), (1.2, 2.0), (-1.2, 2.0)),
        source="游戏碰撞模型：fixture",
        model_paths=("/vehicle/fixture.pmc",),
    )
    viewer = make_viewer(
        sample_data(),
        dimensions={
            "truck": {"truck.a": {"width": 9.0, "front_z": 20.0, "rear_z": -20.0, "verified": True}},
            "trailer": {},
        },
    )
    viewer.game_geometry = SimpleNamespace(
        available=True,
        truck_contour=lambda *_args: contour,
        trailer_contour=lambda *_args: None,
    )

    truck = viewer._build_layouts()[0]

    assert truck.body_contour == contour.points
    assert truck.dimensions_source == "游戏碰撞模型：fixture"
    assert truck.model_paths == ("/vehicle/fixture.pmc",)
    assert math.isclose(truck.width, 2.8)
    assert math.isclose(truck.length, 5.0)


def test_game_model_wheels_fill_telemetry_slot_limit():
    viewer = make_viewer(sample_data())
    live = [
        WheelLayout(-1.0, 0.0, 0.1, 1.0, True, 0.5),
        WheelLayout(1.0, 0.0, 0.1, 1.0, True, 0.5),
    ]
    contour = GameContour(
        points=((-1.3, -1.0), (1.3, -1.0), (1.3, 3.0), (-1.3, 3.0)),
        source="fixture",
        model_paths=("/vehicle/fixture.pmc",),
        wheel_points=(
            GameWheelPoint(-1.0, 0.0, True),
            GameWheelPoint(1.0, 0.0, True),
            GameWheelPoint(-1.0, 2.0, True),
            GameWheelPoint(1.0, 2.0, True),
        ),
        axle_count=2,
    )

    merged = viewer._merge_model_wheels(live, contour, 4)

    assert len(merged) == 4
    assert len(viewer._axle_groups(merged)) == 2
    assert sum(wheel.source == "游戏 PMG" for wheel in merged) == 2
    assert all(math.isclose(wheel.steering, 0.1) for wheel in merged)


def test_configuration_signature_tracks_vehicle_types_but_not_pose():
    viewer = make_viewer(sample_data())
    original = viewer._configuration_signature(viewer.data)
    moved = deepcopy(viewer.data)
    moved["truckPlacement"]["coordinateX"] = 100.0
    changed_truck = deepcopy(viewer.data)
    changed_truck["configString"]["truckId"] = "truck.b"
    changed_trailer = deepcopy(viewer.data)
    changed_trailer["trailers"][0]["conString"]["bodyType"] = "refrigerated"

    assert viewer._configuration_signature(moved) == original
    assert viewer._configuration_signature(changed_truck) != original
    assert viewer._configuration_signature(changed_trailer) != original


def test_configuration_change_is_committed_after_two_stable_frames():
    viewer = make_viewer(sample_data())
    changed = deepcopy(viewer.data)
    changed["configString"]["truckId"] = "truck.b"

    assert viewer._accept_configuration_snapshot(viewer.data) is True
    assert viewer.configuration_generation == 1
    assert viewer._accept_configuration_snapshot(changed) is False
    assert viewer.configuration_generation == 1
    assert viewer._accept_configuration_snapshot(changed) is True
    assert viewer.configuration_generation == 2
    assert viewer.data["configString"]["truckId"] == "truck.b"


def test_saved_geometry_selection_is_keyed_to_complete_configuration():
    viewer = make_viewer(sample_data())
    key = viewer._geometry_override_key("truck")
    viewer.geometry_overrides = {
        "version": 1,
        "truck": {key: {"label": "Truck A", "selection": {"chassis": "vehicle/truck/a.sii"}}},
        "trailer": {},
    }

    assert viewer._geometry_selection("truck") == {"chassis": "vehicle/truck/a.sii"}

    viewer.data["configString"]["truckId"] = "truck.b"
    assert viewer._geometry_selection("truck") is None

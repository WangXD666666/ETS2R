import json
import math

from automatic_reversing.models import Pose2D
from automatic_reversing.surroundings import (
    Obstacle,
    SurroundingsFileReader,
    directional_clearances,
    movement_clearance_profile,
    polygon_distance,
    snapshot_from_dict,
)


def rectangle(min_x, max_x, min_z, max_z):
    return ((min_x, min_z), (max_x, min_z), (max_x, max_z), (min_x, max_z))


def test_vehicle_coordinate_obstacle_is_transformed_to_world():
    snapshot = snapshot_from_dict(
        {
            "coordinate_system": "vehicle",
            "obstacles": [{"center": [2.0, 0.0], "size": [2.0, 2.0]}],
        },
        Pose2D(10.0, 20.0, math.pi / 2.0),
        received_at=100.0,
    )

    xs = [point[0] for point in snapshot.obstacles[0].polygon]
    zs = [point[1] for point in snapshot.obstacles[0].polygon]
    assert math.isclose(min(xs), 9.0)
    assert math.isclose(max(xs), 11.0)
    assert math.isclose(min(zs), 21.0)
    assert math.isclose(max(zs), 23.0)


def test_movement_space_stops_before_obstacle_with_margin():
    vehicle = rectangle(-1.0, 1.0, -1.0, 1.0)
    obstacle = Obstacle("wall", "wall", "墙", rectangle(4.0, 4.5, -5.0, 5.0))

    profile = movement_clearance_profile(
        (vehicle,),
        (obstacle,),
        (0.0, 0.0),
        max_range=10.0,
        safety_margin=0.5,
        samples=8,
        step=0.1,
    )

    east = profile[0][1]
    west = profile[4][1]
    assert 2.4 <= east <= 2.6
    assert math.isclose(west, 10.0)


def test_direction_labels_follow_vehicle_heading():
    profile = tuple((math.tau * index / 8, float(index)) for index in range(8))
    clearances = directional_clearances(profile, 0.0)

    assert clearances == {"前": 2.0, "后": 6.0, "左": 4.0, "右": 0.0}


def test_movement_space_is_disabled_when_vehicle_already_overlaps_obstacle():
    vehicle = rectangle(-1.0, 1.0, -1.0, 1.0)
    obstacle = Obstacle("overlap", "wall", "墙", rectangle(0.5, 2.0, -1.0, 1.0))

    profile = movement_clearance_profile((vehicle,), (obstacle,), (0.0, 0.0), max_range=10.0, samples=8)

    assert all(distance == 0.0 for _, distance in profile)


def test_polygon_distance_reports_edge_clearance():
    assert math.isclose(
        polygon_distance(rectangle(-1.0, 1.0, -1.0, 1.0), rectangle(3.0, 4.0, -1.0, 1.0)),
        2.0,
    )


def test_file_reader_marks_old_detector_data_stale(tmp_path):
    path = tmp_path / "surroundings.json"
    path.write_text(json.dumps({"coordinate_system": "world", "obstacles": []}), encoding="utf-8")
    reader = SurroundingsFileReader(path, stale_after_s=1.0)

    snapshot = reader.read(None, now=path.stat().st_mtime + 2.0)

    assert snapshot.stale
    assert snapshot.error == "环境数据已过期"

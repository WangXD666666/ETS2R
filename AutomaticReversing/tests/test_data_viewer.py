import math

from automatic_reversing_data_viewer.app import TelemetryDataViewer


def viewer_with_data(data):
    viewer = TelemetryDataViewer.__new__(TelemetryDataViewer)
    viewer.data = data
    return viewer


def test_articulation_uses_rotation_x_heading_not_rotation_y():
    viewer = viewer_with_data(
        {
            "truckPlacement": {
                "rotationX": 0.25,
                "rotationY": 0.0,
                "rotationZ": 0.0,
            },
            "trailers": [
                {
                    "comBool": {"attached": True},
                    "comDouble": {
                        "rotationX": 0.125,
                        "rotationY": 0.75,
                        "rotationZ": 0.0,
                    },
                }
            ],
        }
    )

    assert math.isclose(viewer._articulation_angle_deg(), 45.0)

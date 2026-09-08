import math
import struct

from automatic_reversing.models import ControlCommand
from automatic_reversing.scs_sdk import TELEMETRY_SIZE, control_state_from_command, pack_control_state, parse_telemetry_buffer


def write_string(buffer, offset, value):
    raw = value.encode("utf-8")
    buffer[offset : offset + len(raw)] = raw


def test_parse_telemetry_buffer_extracts_standalone_fields():
    buffer = bytearray(TELEMETRY_SIZE)
    struct.pack_into("?", buffer, 0, True)
    struct.pack_into("?", buffer, 4, False)
    struct.pack_into("i", buffer, 40, 2)
    struct.pack_into("i", buffer, 504, -1)
    struct.pack_into("i", buffer, 508, -1)
    struct.pack_into("f", buffer, 948, -0.3)
    struct.pack_into("f", buffer, 960, 0.2)
    struct.pack_into("f", buffer, 964, 0.1)
    struct.pack_into("d", buffer, 2200, 10.0)
    struct.pack_into("d", buffer, 2216, 20.0)
    struct.pack_into("d", buffer, 2232, 0.25)
    write_string(buffer, 2428, "truck.id")
    write_string(buffer, 2556, "cargo.id")

    base = 6000
    struct.pack_into("?", buffer, base + 80, True)
    struct.pack_into("i", buffer, base + 148, 6)
    struct.pack_into("d", buffer, base + 872, 11.0)
    struct.pack_into("d", buffer, base + 888, 21.0)
    struct.pack_into("d", buffer, base + 904, 0.5)
    write_string(buffer, base + 1048, "box")
    write_string(buffer, base + 1304, "single")

    data = parse_telemetry_buffer(buffer)

    assert data["sdkActive"] is True
    assert data["truckPlacement"]["coordinateX"] == 10.0
    assert data["truckPlacement"]["coordinateZ"] == 20.0
    assert data["truckPlacement"]["rotationY"] == 0.25
    assert data["truckFloat"]["speed"] < 0
    assert data["truckFloat"]["userThrottle"] > 0
    assert data["truckFloat"]["userBrake"] > 0
    assert data["configString"]["truckId"] == "truck.id"
    assert data["trailers"][0]["comBool"]["attached"] is True
    assert data["trailers"][0]["comDouble"]["worldX"] == 11.0
    assert data["trailers"][0]["conString"]["chainType"] == "single"


def test_pack_control_state_matches_sdk_controller_layout():
    command = ControlCommand(steering=0.25, throttle=0.15, brake=0.0, reverse=True, active=True)
    state = control_state_from_command(command)
    packed = pack_control_state(state)
    floats = struct.unpack_from("ffff", packed, 0)
    bools = struct.unpack_from("38?", packed, 16)

    assert math.isclose(floats[0], 0.25, rel_tol=1e-6)
    assert math.isclose(floats[1], 0.15, rel_tol=1e-6)
    assert floats[2] == 0.0
    assert bools[12] is False
    assert bools[13] is True

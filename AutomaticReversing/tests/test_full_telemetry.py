import struct

from automatic_reversing.full_telemetry import parse_full_telemetry_buffer
from automatic_reversing.scs_sdk import TELEMETRY_SIZE


def write_string(buffer, offset, value):
    raw = value.encode("utf-8")
    buffer[offset : offset + len(raw)] = raw


def test_full_parser_reads_main_truck_and_job_fields():
    buffer = bytearray(TELEMETRY_SIZE)
    struct.pack_into("?", buffer, 0, True)
    struct.pack_into("?", buffer, 4, False)
    struct.pack_into("Q", buffer, 8, 123)
    struct.pack_into("i", buffer, 40, 2)
    struct.pack_into("i", buffer, 52, 1)
    struct.pack_into("i", buffer, 80, 16)
    struct.pack_into("i", buffer, 504, -1)
    struct.pack_into("i", buffer, 508, -1)
    struct.pack_into("f", buffer, 948, -0.4)
    struct.pack_into("f", buffer, 960, 0.2)
    struct.pack_into("?", buffer, 1576, True)
    struct.pack_into("f", buffer, 1732, 1.25)
    struct.pack_into("d", buffer, 2200, 10.0)
    struct.pack_into("d", buffer, 2216, 20.0)
    struct.pack_into("d", buffer, 2232, 0.5)
    write_string(buffer, 2428, "truck.id")
    write_string(buffer, 2492, "Truck Name")
    write_string(buffer, 2556, "cargo.id")
    write_string(buffer, 2620, "Cargo Name")
    write_string(buffer, 2812, "city.dst")
    write_string(buffer, 2940, "company.dst")
    struct.pack_into("Q", buffer, 4000, 123456)
    struct.pack_into("?", buffer, 4300, True)

    data = parse_full_telemetry_buffer(buffer, include_trailers=False)

    assert data["sdkActive"] is True
    assert data["time"] == 123
    assert data["scsValues"]["game"] == "ETS2"
    assert data["configUI"]["truckWheelCount"] == 16
    assert data["truckInt"]["gear"] == -1
    assert data["truckFloat"]["speed"] < 0
    assert data["truckBool"]["engineEnabled"] is True
    assert data["truckPlacement"]["coordinateX"] == 10.0
    assert data["configString"]["truckId"] == "truck.id"
    assert data["configString"]["cargo"] == "Cargo Name"
    assert data["configLongLong"]["jobIncome"] == 123456
    assert data["specialBool"]["onJob"] is True


def test_full_parser_reads_trailer_slots():
    buffer = bytearray(TELEMETRY_SIZE)
    base = 6000
    struct.pack_into("?", buffer, base + 80, True)
    struct.pack_into("i", buffer, base + 148, 6)
    struct.pack_into("f", buffer, base + 152, 0.12)
    struct.pack_into("f", buffer, base + 616, 1.0)
    struct.pack_into("d", buffer, base + 872, 11.0)
    struct.pack_into("d", buffer, base + 888, 21.0)
    struct.pack_into("d", buffer, base + 904, 0.25)
    write_string(buffer, base + 920, "trailer.id")
    write_string(buffer, base + 1048, "box")
    write_string(buffer, base + 1240, "Trailer Name")
    write_string(buffer, base + 1304, "single")

    data = parse_full_telemetry_buffer(buffer, include_trailers=True)
    first = data["trailers"][0]

    assert len(data["trailers"]) == 10
    assert first["comBool"]["attached"] is True
    assert first["conUI"]["wheelCount"] == 6
    assert first["comFloat"]["cargoDamage"] > 0
    assert first["comVector"]["linearVelocityX"] == 1.0
    assert first["comDouble"]["worldX"] == 11.0
    assert first["conString"]["id"] == "trailer.id"
    assert first["conString"]["bodyType"] == "box"

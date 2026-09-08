"""Standalone SCS shared-memory adapters.

These adapters talk directly to the shared memory blocks created by the game
plugins bundled with ETS2LA's first-time setup. They intentionally avoid any
ETS2LA imports so the app can run on its own.
"""

from __future__ import annotations

import mmap
import struct
from dataclasses import dataclass
from typing import Any

from .models import ControlCommand


TELEMETRY_MMAP = "Local\\SCSTelemetry"
TELEMETRY_SIZE = 32 * 1024
CONTROLS_MMAP = "Local\\SCSControls"
CONTROLS_SIZE = 4 * 4 + 38
STRING_SIZE = 64
TRAILER_SIZE = 1560
TRAILER_BASE = 6000


class SharedMemoryUnavailable(RuntimeError):
    pass


def _read_bool(buffer: mmap.mmap | bytes, offset: int) -> bool:
    return struct.unpack_from("?", buffer, offset)[0]


def _read_int(buffer: mmap.mmap | bytes, offset: int) -> int:
    return struct.unpack_from("i", buffer, offset)[0]


def _read_float(buffer: mmap.mmap | bytes, offset: int) -> float:
    return struct.unpack_from("f", buffer, offset)[0]


def _read_double(buffer: mmap.mmap | bytes, offset: int) -> float:
    return struct.unpack_from("d", buffer, offset)[0]


def _read_string(buffer: mmap.mmap | bytes, offset: int, size: int = STRING_SIZE) -> str:
    raw = bytes(buffer[offset : offset + size])
    return raw.split(b"\x00", 1)[0].decode("utf-8", errors="ignore")


def parse_telemetry_buffer(buffer: mmap.mmap | bytes) -> dict[str, Any]:
    """Parse the subset of SCS telemetry needed by AutomaticReversing."""
    data: dict[str, Any] = {
        "sdkActive": _read_bool(buffer, 0),
        "pause": _read_bool(buffer, 4),
        "scsValues": {
            "telemetryPluginRevision": _read_int(buffer, 40),
            "versionMajor": _read_int(buffer, 44),
            "versionMinor": _read_int(buffer, 48),
        },
        "truckInt": {
            "gear": _read_int(buffer, 504),
            "gearDashboard": _read_int(buffer, 508),
        },
        "truckFloat": {
            "speed": _read_float(buffer, 948),
            "userSteer": _read_float(buffer, 956),
            "userThrottle": _read_float(buffer, 960),
            "userBrake": _read_float(buffer, 964),
        },
        "truckPlacement": {
            "coordinateX": _read_double(buffer, 2200),
            "coordinateY": _read_double(buffer, 2208),
            "coordinateZ": _read_double(buffer, 2216),
            "rotationX": _read_double(buffer, 2224),
            "rotationY": _read_double(buffer, 2232),
            "rotationZ": _read_double(buffer, 2240),
        },
        "configString": {
            "truckBrandId": _read_string(buffer, 2300),
            "truckBrand": _read_string(buffer, 2364),
            "truckId": _read_string(buffer, 2428),
            "truckName": _read_string(buffer, 2492),
            "cargoId": _read_string(buffer, 2556),
            "cargo": _read_string(buffer, 2620),
        },
        "trailers": [],
    }

    trailers = []
    for index in range(10):
        base = TRAILER_BASE + index * TRAILER_SIZE
        attached = _read_bool(buffer, base + 80)
        trailers.append(
            {
                "comBool": {"attached": attached},
                "conUI": {"wheelCount": _read_int(buffer, base + 148)},
                "comDouble": {
                    "worldX": _read_double(buffer, base + 872),
                    "worldY": _read_double(buffer, base + 880),
                    "worldZ": _read_double(buffer, base + 888),
                    "rotationX": _read_double(buffer, base + 896),
                    "rotationY": _read_double(buffer, base + 904),
                    "rotationZ": _read_double(buffer, base + 912),
                },
                "conString": {
                    "id": _read_string(buffer, base + 920),
                    "cargoAcessoryId": _read_string(buffer, base + 984),
                    "bodyType": _read_string(buffer, base + 1048),
                    "brandId": _read_string(buffer, base + 1112),
                    "brand": _read_string(buffer, base + 1176),
                    "name": _read_string(buffer, base + 1240),
                    "chainType": _read_string(buffer, base + 1304),
                },
            }
        )
    data["trailers"] = trailers
    return data


@dataclass
class SCSControlState:
    steering: float = 0.0
    acceleration: float = 0.0
    brake: float = 0.0
    clutch: float = 0.0
    pause: bool = False
    parking_brake: bool = False
    wipers: bool = False
    cruise_control: bool = False
    cruise_control_increase: bool = False
    cruise_control_decrease: bool = False
    cruise_control_reset: bool = False
    lights: bool = False
    high_beams: bool = False
    left_blinker: bool = False
    right_blinker: bool = False
    quickpark: bool = False
    drive: bool = False
    reverse: bool = False
    cycle_zoom: bool = False
    trip_reset: bool = False
    rear_wipers: bool = False
    wipers0: bool = False
    wipers1: bool = False
    wipers2: bool = False
    wipers3: bool = False
    wipers4: bool = False
    horn: bool = False
    airhorn: bool = False
    light_horn: bool = False
    cam1: bool = False
    cam2: bool = False
    cam3: bool = False
    cam4: bool = False
    cam5: bool = False
    cam6: bool = False
    cam7: bool = False
    cam8: bool = False
    map_zoom_in: bool = False
    map_zoom_out: bool = False
    acc_mode: bool = False
    show_mirrors: bool = False
    hazards: bool = False


def control_state_from_command(command: ControlCommand) -> SCSControlState:
    return SCSControlState(
        steering=command.steering,
        acceleration=command.throttle,
        brake=command.brake,
        drive=command.drive,
        reverse=command.reverse,
    )


def pack_control_state(state: SCSControlState) -> bytes:
    return struct.pack(
        "ffff38?",
        state.steering,
        state.acceleration,
        state.brake,
        state.clutch,
        state.pause,
        state.parking_brake,
        state.wipers,
        state.cruise_control,
        state.cruise_control_increase,
        state.cruise_control_decrease,
        state.cruise_control_reset,
        state.lights,
        state.high_beams,
        state.left_blinker,
        state.right_blinker,
        state.quickpark,
        state.drive,
        state.reverse,
        state.cycle_zoom,
        state.trip_reset,
        state.rear_wipers,
        state.wipers0,
        state.wipers1,
        state.wipers2,
        state.wipers3,
        state.wipers4,
        state.horn,
        state.airhorn,
        state.light_horn,
        state.cam1,
        state.cam2,
        state.cam3,
        state.cam4,
        state.cam5,
        state.cam6,
        state.cam7,
        state.cam8,
        state.map_zoom_in,
        state.map_zoom_out,
        state.acc_mode,
        state.show_mirrors,
        state.hazards,
    )


class SCSTelemetryReader:
    def read(self) -> dict[str, Any]:
        try:
            with mmap.mmap(0, TELEMETRY_SIZE, TELEMETRY_MMAP) as mm:
                return parse_telemetry_buffer(mm)
        except OSError as exc:
            raise SharedMemoryUnavailable("SCS telemetry shared memory is unavailable") from exc


class SCSControlsWriter:
    def __init__(self) -> None:
        self._mm: mmap.mmap | None = None

    def _open(self) -> mmap.mmap:
        if self._mm is None:
            try:
                self._mm = mmap.mmap(0, CONTROLS_SIZE, CONTROLS_MMAP)
            except OSError as exc:
                raise SharedMemoryUnavailable("SCS controls shared memory is unavailable") from exc
        return self._mm

    def write(self, state: SCSControlState) -> None:
        mm = self._open()
        mm[:] = pack_control_state(state)

    def write_command(self, command: ControlCommand) -> None:
        self.write(control_state_from_command(command))

    def close(self) -> None:
        if self._mm is not None:
            self._mm.close()
            self._mm = None

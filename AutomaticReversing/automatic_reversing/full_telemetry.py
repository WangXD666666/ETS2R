"""Full standalone parser for SCS telemetry shared memory."""

from __future__ import annotations

import mmap
import struct
from typing import Any

from .scs_sdk import SharedMemoryUnavailable, TELEMETRY_MMAP, TELEMETRY_SIZE


STRING_SIZE = 64
SUBSTANCE_SIZE = 25
MAX_TRAILERS = 10


class _BufferReader:
    def __init__(self, buffer: mmap.mmap | bytes) -> None:
        self.buffer = buffer
        self.offset = 0

    def skip(self, count: int) -> None:
        self.offset += count

    def bool(self, count: int = 1):
        if count == 1:
            value = struct.unpack_from("?", self.buffer, self.offset)[0]
            self.offset += 1
            return value
        values = [struct.unpack_from("?", self.buffer, self.offset + index)[0] for index in range(count)]
        self.offset += count
        return values

    def int(self, count: int = 1):
        if count == 1:
            value = struct.unpack_from("i", self.buffer, self.offset)[0]
            self.offset += 4
            return value
        values = [struct.unpack_from("i", self.buffer, self.offset + index * 4)[0] for index in range(count)]
        self.offset += count * 4
        return values

    def float(self, count: int = 1):
        if count == 1:
            value = struct.unpack_from("f", self.buffer, self.offset)[0]
            self.offset += 4
            return value
        values = [struct.unpack_from("f", self.buffer, self.offset + index * 4)[0] for index in range(count)]
        self.offset += count * 4
        return values

    def ulonglong(self) -> int:
        value = struct.unpack_from("Q", self.buffer, self.offset)[0]
        self.offset += 8
        return value

    def double(self, count: int = 1):
        if count == 1:
            value = struct.unpack_from("d", self.buffer, self.offset)[0]
            self.offset += 8
            return value
        values = [struct.unpack_from("d", self.buffer, self.offset + index * 8)[0] for index in range(count)]
        self.offset += count * 8
        return values

    def string(self, count: int) -> str:
        raw = bytes(self.buffer[self.offset : self.offset + count])
        self.offset += count
        return raw.split(b"\x00", 1)[0].decode("utf-8", errors="ignore")

    def string_array(self, count: int, string_size: int) -> list[str]:
        return [self.string(string_size) for _ in range(count)]

    def game(self) -> str:
        value = self.int()
        if value == 1:
            return "ETS2"
        if value == 2:
            return "ATS"
        return "unknown"

    def trailer(self) -> dict[str, Any]:
        data: dict[str, Any] = {}

        data["conBool"] = {
            "wheelSteerable": self.bool(16),
            "wheelSimulated": self.bool(16),
            "wheelPowered": self.bool(16),
            "wheelLiftable": self.bool(16),
        }
        data["comBool"] = {
            "wheelOnGround": self.bool(16),
            "attached": self.bool(),
        }
        data["bufferBool"] = self.string(3)

        data["comUI"] = {
            "wheelSubstance": self.int(16),
        }
        data["conUI"] = {
            "wheelCount": self.int(),
        }

        data["comFloat"] = {
            "cargoDamage": self.float(),
            "wearChassis": self.float(),
            "wearWheels": self.float(),
            "wearBody": self.float(),
            "wheelSuspDeflection": self.float(16),
            "wheelVelocity": self.float(16),
            "wheelSteering": self.float(16),
            "wheelRotation": self.float(16),
            "wheelLift": self.float(16),
            "wheelLiftOffset": self.float(16),
        }
        data["conFloat"] = {
            "wheelRadius": self.float(16),
        }

        data["comVector"] = {
            "linearVelocityX": self.float(),
            "linearVelocityY": self.float(),
            "linearVelocityZ": self.float(),
            "angularVelocityX": self.float(),
            "angularVelocityY": self.float(),
            "angularVelocityZ": self.float(),
            "linearAccelerationX": self.float(),
            "linearAccelerationY": self.float(),
            "linearAccelerationZ": self.float(),
            "angularAccelerationX": self.float(),
            "angularAccelerationY": self.float(),
            "angularAccelerationZ": self.float(),
        }
        data["conVector"] = {
            "hookPositionX": self.float(),
            "hookPositionY": self.float(),
            "hookPositionZ": self.float(),
            "wheelPositionX": self.float(16),
            "wheelPositionY": self.float(16),
            "wheelPositionZ": self.float(16),
        }
        data["bufferVector"] = self.string(4)

        data["comDouble"] = {
            "worldX": self.double(),
            "worldY": self.double(),
            "worldZ": self.double(),
            "rotationX": self.double(),
            "rotationY": self.double(),
            "rotationZ": self.double(),
        }

        data["conString"] = {
            "id": self.string(STRING_SIZE),
            "cargoAcessoryId": self.string(STRING_SIZE),
            "bodyType": self.string(STRING_SIZE),
            "brandId": self.string(STRING_SIZE),
            "brand": self.string(STRING_SIZE),
            "name": self.string(STRING_SIZE),
            "chainType": self.string(STRING_SIZE),
            "licensePlate": self.string(STRING_SIZE),
            "licensePlateCountry": self.string(STRING_SIZE),
            "licensePlateCountryId": self.string(STRING_SIZE),
        }
        return data


def parse_full_telemetry_buffer(buffer: mmap.mmap | bytes, include_trailers: bool = True) -> dict[str, Any]:
    reader = _BufferReader(buffer)
    data: dict[str, Any] = {}

    data["sdkActive"] = reader.bool()
    data["placeHolder"] = reader.string(3)
    data["pause"] = reader.bool()
    data["placeHolder2"] = reader.string(3)
    data["time"] = reader.ulonglong()
    data["simulatedTime"] = reader.ulonglong()
    data["renderTime"] = reader.ulonglong()
    data["multiplayerTimeOffset"] = reader.ulonglong()

    data["scsValues"] = {
        "telemetryPluginRevision": reader.int(),
        "versionMajor": reader.int(),
        "versionMinor": reader.int(),
        "game": reader.game(),
        "telemetryVersionGameMajor": reader.int(),
        "telemetryVersionGameMinor": reader.int(),
    }
    data["commonUI"] = {"timeAbs": reader.int()}
    data["configUI"] = {
        "gears": reader.int(),
        "gearsReverse": reader.int(),
        "retarderStepCount": reader.int(),
        "truckWheelCount": reader.int(),
        "selectorCount": reader.int(),
        "timeAbsDelivery": reader.int(),
        "maxTrailerCount": reader.int(),
        "unitCount": reader.int(),
        "plannedDistanceKm": reader.int(),
    }
    data["truckUI"] = {
        "shifterSlot": reader.int(),
        "retarderBrake": reader.int(),
        "lightsAuxFront": reader.int(),
        "lightsAuxRoof": reader.int(),
        "truckWheelSubstance": reader.int(16),
        "hshifterPosition": reader.int(32),
        "hshifterBitmask": reader.int(32),
    }
    data["gameplayUI"] = {
        "jobDeliveredDeliveryTime": reader.int(),
        "jobStartingTime": reader.int(),
        "jobFinishedTime": reader.int(),
    }
    data["bufferUI"] = reader.string(48)

    data["commonInt"] = {"restStop": reader.int()}
    data["truckInt"] = {
        "gear": reader.int(),
        "gearDashboard": reader.int(),
        "hshifterResulting": reader.int(32),
    }
    data["bufferInt"] = reader.string(56)
    reader.skip(4)

    data["commonFloat"] = {"scale": reader.float()}
    data["configFloat"] = {
        "fuelCapacity": reader.float(),
        "fuelWarningFactor": reader.float(),
        "adblueCapacity": reader.float(),
        "adblueWarningFactor": reader.float(),
        "airPressureWarning": reader.float(),
        "airPressureEmergency": reader.float(),
        "oilPressureWarning": reader.float(),
        "waterTemperatureWarning": reader.float(),
        "batteryVoltageWarning": reader.float(),
        "engineRpmMax": reader.float(),
        "gearDifferential": reader.float(),
        "cargoMass": reader.float(),
        "truckWheelRadius": reader.float(16),
        "gearRatiosForward": reader.float(24),
        "gearRatiosReverse": reader.float(8),
        "unitMass": reader.float(),
    }
    data["truckFloat"] = {
        "speed": reader.float(),
        "engineRpm": reader.float(),
        "userSteer": reader.float(),
        "userThrottle": reader.float(),
        "userBrake": reader.float(),
        "userClutch": reader.float(),
        "gameSteer": reader.float(),
        "gameThrottle": reader.float(),
        "gameBrake": reader.float(),
        "gameClutch": reader.float(),
        "cruiseControlSpeed": reader.float(),
        "airPressure": reader.float(),
        "brakeTemperature": reader.float(),
        "fuel": reader.float(),
        "fuelAvgConsumption": reader.float(),
        "fuelRange": reader.float(),
        "adblue": reader.float(),
        "oilPressure": reader.float(),
        "oilTemperature": reader.float(),
        "waterTemperature": reader.float(),
        "batteryVoltage": reader.float(),
        "lightsDashboard": reader.float(),
        "wearEngine": reader.float(),
        "wearTransmission": reader.float(),
        "wearCabin": reader.float(),
        "wearChassis": reader.float(),
        "wearWheels": reader.float(),
        "truckOdometer": reader.float(),
        "routeDistance": reader.float(),
        "routeTime": reader.float(),
        "speedLimit": reader.float(),
        "truck_wheelSuspDeflection": reader.float(16),
        "truck_wheelVelocity": reader.float(16),
        "truck_wheelSteering": reader.float(16),
        "truck_wheelRotation": reader.float(16),
        "truck_wheelLift": reader.float(16),
        "truck_wheelLiftOffset": reader.float(16),
    }
    data["gameplayFloat"] = {
        "jobDeliveredCargoDamage": reader.float(),
        "jobDeliveredDistanceKm": reader.float(),
        "refuelAmount": reader.float(),
    }
    data["jobFloat"] = {"cargoDamage": reader.float()}
    data["bufferFloat"] = reader.string(28)

    data["configBool"] = {
        "truckWheelSteerable": reader.bool(16),
        "truckWheelSimulated": reader.bool(16),
        "truckWheelPowered": reader.bool(16),
        "truckWheelLiftable": reader.bool(16),
        "isCargoLoaded": reader.bool(),
        "specialJob": reader.bool(),
    }
    data["truckBool"] = {
        "parkBrake": reader.bool(),
        "motorBrake": reader.bool(),
        "airPressureWarning": reader.bool(),
        "airPressureEmergency": reader.bool(),
        "fuelWarning": reader.bool(),
        "adblueWarning": reader.bool(),
        "oilPressureWarning": reader.bool(),
        "waterTemperatureWarning": reader.bool(),
        "batteryVoltageWarning": reader.bool(),
        "electricEnabled": reader.bool(),
        "engineEnabled": reader.bool(),
        "wipers": reader.bool(),
        "blinkerLeftActive": reader.bool(),
        "blinkerRightActive": reader.bool(),
        "blinkerLeftOn": reader.bool(),
        "blinkerRightOn": reader.bool(),
        "lightsParking": reader.bool(),
        "lightsBeamLow": reader.bool(),
        "lightsBeamHigh": reader.bool(),
        "lightsBeacon": reader.bool(),
        "lightsBrake": reader.bool(),
        "lightsReverse": reader.bool(),
        "lightsHazard": reader.bool(),
        "cruiseControl": reader.bool(),
        "truck_wheelOnGround": reader.bool(16),
        "shifterToggle": reader.bool(2),
        "differentialLock": reader.bool(),
        "liftAxle": reader.bool(),
        "liftAxleIndicator": reader.bool(),
        "trailerLiftAxle": reader.bool(),
        "trailerLiftAxleIndicator": reader.bool(),
    }
    data["gameplayBool"] = {
        "jobDeliveredAutoparkUsed": reader.bool(),
        "jobDeliveredAutoloadUsed": reader.bool(),
    }
    data["bufferBool"] = reader.string(25)

    data["configVector"] = {
        "cabinPositionX": reader.float(),
        "cabinPositionY": reader.float(),
        "cabinPositionZ": reader.float(),
        "headPositionX": reader.float(),
        "headPositionY": reader.float(),
        "headPositionZ": reader.float(),
        "truckHookPositionX": reader.float(),
        "truckHookPositionY": reader.float(),
        "truckHookPositionZ": reader.float(),
        "truckWheelPositionX": reader.float(16),
        "truckWheelPositionY": reader.float(16),
        "truckWheelPositionZ": reader.float(16),
    }
    data["truckVector"] = {
        "lv_accelerationX": reader.float(),
        "lv_accelerationY": reader.float(),
        "lv_accelerationZ": reader.float(),
        "av_accelerationX": reader.float(),
        "av_accelerationY": reader.float(),
        "av_accelerationZ": reader.float(),
        "accelerationX": reader.float(),
        "accelerationY": reader.float(),
        "accelerationZ": reader.float(),
        "aa_accelerationX": reader.float(),
        "aa_accelerationY": reader.float(),
        "aa_accelerationZ": reader.float(),
        "cabinAVX": reader.float(),
        "cabinAVY": reader.float(),
        "cabinAVZ": reader.float(),
        "cabinAAX": reader.float(),
        "cabinAAY": reader.float(),
        "cabinAAZ": reader.float(),
    }
    data["bufferVector"] = reader.string(60)

    data["headPlacement"] = {
        "cabinOffsetX": reader.float(),
        "cabinOffsetY": reader.float(),
        "cabinOffsetZ": reader.float(),
        "cabinOffsetrotationX": reader.float(),
        "cabinOffsetrotationY": reader.float(),
        "cabinOffsetrotationZ": reader.float(),
        "headOffsetX": reader.float(),
        "headOffsetY": reader.float(),
        "headOffsetZ": reader.float(),
        "headOffsetrotationX": reader.float(),
        "headOffsetrotationY": reader.float(),
        "headOffsetrotationZ": reader.float(),
    }
    data["bufferHeadPlacement"] = reader.string(152)

    data["truckPlacement"] = {
        "coordinateX": reader.double(),
        "coordinateY": reader.double(),
        "coordinateZ": reader.double(),
        "rotationX": reader.double(),
        "rotationY": reader.double(),
        "rotationZ": reader.double(),
    }
    data["bufferTruckPlacement"] = reader.string(52)

    data["configString"] = {
        "truckBrandId": reader.string(STRING_SIZE),
        "truckBrand": reader.string(STRING_SIZE),
        "truckId": reader.string(STRING_SIZE),
        "truckName": reader.string(STRING_SIZE),
        "cargoId": reader.string(STRING_SIZE),
        "cargo": reader.string(STRING_SIZE),
        "cityDstId": reader.string(STRING_SIZE),
        "cityDst": reader.string(STRING_SIZE),
        "compDstId": reader.string(STRING_SIZE),
        "compDst": reader.string(STRING_SIZE),
        "citySrcId": reader.string(STRING_SIZE),
        "citySrc": reader.string(STRING_SIZE),
        "compSrcId": reader.string(STRING_SIZE),
        "compSrc": reader.string(STRING_SIZE),
        "shifterType": reader.string(16),
        "truckLicensePlate": reader.string(STRING_SIZE),
        "truckLicensePlateCountryId": reader.string(STRING_SIZE),
        "truckLicensePlateCountry": reader.string(STRING_SIZE),
        "jobMarket": reader.string(32),
    }
    data["gameplayString"] = {
        "fineOffence": reader.string(32),
        "ferrySourceName": reader.string(STRING_SIZE),
        "ferryTargetName": reader.string(STRING_SIZE),
        "ferrySourceId": reader.string(STRING_SIZE),
        "ferryTargetId": reader.string(STRING_SIZE),
        "trainSourceName": reader.string(STRING_SIZE),
        "trainTargetName": reader.string(STRING_SIZE),
        "trainSourceId": reader.string(STRING_SIZE),
        "trainTargetId": reader.string(STRING_SIZE),
    }
    data["bufferString"] = reader.string(20)

    data["configLongLong"] = {"jobIncome": reader.ulonglong()}
    data["bufferConfigLongLong"] = reader.string(192)
    data["gameplayLongLong"] = {
        "jobCancelledPenalty": reader.ulonglong(),
        "jobDeliveredRevenue": reader.ulonglong(),
        "fineAmount": reader.ulonglong(),
        "tollgatePayAmount": reader.ulonglong(),
        "ferryPayAmount": reader.ulonglong(),
        "trainPayAmount": reader.ulonglong(),
    }
    data["bufferGameplayLongLong"] = reader.string(52)

    data["specialBool"] = {
        "onJob": reader.bool(),
        "jobFinished": reader.bool(),
        "jobCancelled": reader.bool(),
        "jobDelivered": reader.bool(),
        "fined": reader.bool(),
        "tollgate": reader.bool(),
        "ferry": reader.bool(),
        "train": reader.bool(),
        "refuel": reader.bool(),
        "refuelPayed": reader.bool(),
    }
    data["bufferSpecial"] = reader.string(90)
    data["substances"] = reader.string_array(SUBSTANCE_SIZE, STRING_SIZE)

    if include_trailers:
        data["trailers"] = [reader.trailer() for _ in range(MAX_TRAILERS)]

    data["_parser"] = {
        "offset": reader.offset,
        "telemetrySize": TELEMETRY_SIZE,
        "includeTrailers": include_trailers,
    }
    return data


class FullSCSTelemetryReader:
    def read(self, include_trailers: bool = True) -> dict[str, Any]:
        try:
            with mmap.mmap(0, TELEMETRY_SIZE, TELEMETRY_MMAP) as mm:
                return parse_full_telemetry_buffer(mm, include_trailers=include_trailers)
        except OSError as exc:
            raise SharedMemoryUnavailable("SCS telemetry shared memory is unavailable") from exc

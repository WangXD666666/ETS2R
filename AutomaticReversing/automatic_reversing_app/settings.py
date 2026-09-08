"""Small JSON settings store for the standalone app."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_SETTINGS = {
    "target_speed_kmh": 1.5,
    "max_speed_kmh": 3.0,
    "max_articulation_deg": 45.0,
    "steering_gain_lateral": 0.18,
    "steering_gain_heading": 0.75,
    "steering_gain_articulation": 0.45,
    "max_steering": 0.85,
    "steering_sign": -1.0,
    "tolerance_m": 0.75,
    "tolerance_deg": 8.0,
    "target_step_m": 0.25,
    "target_step_deg": 2.0,
    "target": None,
    "vehicle_profiles": {},
}


class SettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = DEFAULT_SETTINGS.copy()
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.save()
            return
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            loaded = {}
        self.data.update(loaded)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value
        self.save()

"""Locate and import vehicle definitions from installed SCS/DLC archives."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXTRACTOR = PROJECT_ROOT / "tools" / "scs_extractor" / "scs_extractor.exe"
DEFAULT_DEF_CACHE = PROJECT_ROOT / "game_data" / "def" / "def"
DEFAULT_VEHICLE_CACHE = PROJECT_ROOT / "game_data" / "base_vehicle" / "vehicle"


def steam_libraries() -> list[Path]:
    libraries: list[Path] = []
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"SOFTWARE\Valve\Steam") as key:
            steam_root = Path(winreg.QueryValueEx(key, "SteamPath")[0])
        libraries.append(steam_root)
        library_file = steam_root / "steamapps" / "libraryfolders.vdf"
        if library_file.is_file():
            text = library_file.read_text(encoding="utf-8", errors="ignore")
            libraries.extend(Path(value.replace("\\\\", "\\")) for value in re.findall(r'"path"\s+"([^"]+)"', text))
    except (ImportError, OSError):
        pass
    return list(dict.fromkeys(path.resolve() for path in libraries))


def find_game() -> Path | None:
    for library in steam_libraries():
        for name in ("Euro Truck Simulator 2", "American Truck Simulator"):
            candidate = library / "steamapps" / "common" / name
            if (candidate / "def.scs").is_file() and (candidate / "base_vehicle.scs").is_file():
                return candidate
    return None


def compact_token(value: str) -> str:
    value = value.removeprefix("vehicle.").removeprefix("dlc_")
    return re.sub(r"[^a-z0-9]", "", value.lower())


def archive_candidates(game_dir: Path, vehicle_id: str) -> list[tuple[int, Path]]:
    """Rank installed DLC archives whose names resemble a telemetry vehicle ID."""
    identity = compact_token(vehicle_id)
    words = [part for part in re.split(r"[^a-z0-9]+", vehicle_id.lower()) if part not in {"vehicle", "scs"}]
    candidates: list[tuple[int, Path]] = []
    for archive in game_dir.glob("dlc_*.scs"):
        archive_token = compact_token(archive.stem)
        score = 0
        if identity == archive_token:
            score = 1000
        elif len(identity) >= 5 and (identity in archive_token or archive_token in identity):
            score = 800 + min(len(identity), len(archive_token))
        else:
            score = sum(80 + len(word) for word in words if len(word) >= 3 and word in archive_token)
            if words and words[0] in archive_token:
                score += 40
        if any(term in archive_token for term in ("paint", "tuning", "flags")):
            score -= 250
        if score > 0:
            candidates.append((score, archive))
    return sorted(candidates, key=lambda item: (-item[0], item[1].name))


def best_archive(game_dir: Path, vehicle_id: str, minimum_score: int = 500) -> Path | None:
    candidates = archive_candidates(game_dir, vehicle_id)
    if not candidates or candidates[0][0] < minimum_score:
        return None
    return candidates[0][1]


def import_vehicle_archives(
    archives: Iterable[Path],
    extractor: Path = DEFAULT_EXTRACTOR,
    def_cache: Path = DEFAULT_DEF_CACHE,
    vehicle_cache: Path = DEFAULT_VEHICLE_CACHE,
) -> list[str]:
    """Extract archives once and merge only definitions and vehicle assets."""
    if not extractor.is_file():
        raise FileNotFoundError(f"找不到 SCS 解包工具：{extractor}")
    imported: list[str] = []
    staging_parent = PROJECT_ROOT / "game_data"
    staging_parent.mkdir(parents=True, exist_ok=True)
    for archive in dict.fromkeys(Path(path).resolve() for path in archives):
        if not archive.is_file():
            raise FileNotFoundError(f"找不到车辆资源包：{archive}")
        with tempfile.TemporaryDirectory(prefix="vehicle_resource_", dir=staging_parent) as temporary:
            staging = Path(temporary)
            subprocess.run([str(extractor), str(archive), str(staging)], check=True)
            extracted_def = staging / "def"
            extracted_vehicle = staging / "vehicle"
            if extracted_def.is_dir():
                shutil.copytree(extracted_def, def_cache, dirs_exist_ok=True)
            if extracted_vehicle.is_dir():
                shutil.copytree(extracted_vehicle, vehicle_cache, dirs_exist_ok=True)
            if not extracted_def.is_dir() and not extracted_vehicle.is_dir():
                raise ValueError(f"资源包中没有 def/vehicle 数据：{archive.name}")
        imported.append(archive.name)
    return imported


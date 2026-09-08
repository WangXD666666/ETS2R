"""Extract local ETS2/ATS definitions and vehicle models for contour rendering."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

from automatic_reversing.game_resources import find_game


ROOT = Path(__file__).resolve().parent
DEFAULT_EXTRACTOR = ROOT / "tools" / "scs_extractor" / "scs_extractor.exe"


def extract_archive(extractor: Path, archive: Path, output: Path, force: bool) -> None:
    if output.is_dir() and any(output.iterdir()) and not force:
        print(f"Using existing cache: {output}")
        return
    output.mkdir(parents=True, exist_ok=True)
    print(f"Extracting {archive.name} -> {output}")
    subprocess.run([str(extractor), str(archive), str(output)], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-dir", type=Path, help="ETS2 or ATS installation directory")
    parser.add_argument("--extractor", type=Path, default=DEFAULT_EXTRACTOR, help="SCS Game Archive Extractor executable")
    parser.add_argument("--force", action="store_true", help="Extract again after a game update")
    parser.add_argument("--yes", action="store_true", help="Skip the disk usage confirmation")
    args = parser.parse_args()

    game_dir = (args.game_dir or find_game())
    if game_dir is None:
        raise SystemExit("Could not find ETS2/ATS. Pass --game-dir explicitly.")
    game_dir = game_dir.resolve()
    extractor = args.extractor.resolve()
    if not extractor.is_file():
        raise SystemExit(
            "SCS extractor is missing. Download the 1.55+ Game Archive Extractor from "
            "https://modding.scssoft.com/wiki/Documentation/Tools/Game_Archive_Extractor"
        )

    free_gib = shutil.disk_usage(ROOT).free / (1024**3)
    print(f"Game: {game_dir}")
    print(f"Free disk space: {free_gib:.1f} GiB")
    print("The extracted vehicle cache may use several GiB.")
    if not args.yes and input("Continue? [y/N] ").strip().lower() not in ("y", "yes"):
        raise SystemExit("Cancelled.")

    extract_archive(extractor, game_dir / "def.scs", ROOT / "game_data" / "def", args.force)
    extract_archive(extractor, game_dir / "base_vehicle.scs", ROOT / "game_data" / "base_vehicle", args.force)
    print("Game geometry cache is ready.")


if __name__ == "__main__":
    main()

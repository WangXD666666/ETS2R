import tempfile
from pathlib import Path

from automatic_reversing.game_resources import archive_candidates, best_archive, compact_token


def test_compact_vehicle_and_dlc_tokens_match():
    assert compact_token("vehicle.renault.etech_t") == compact_token("dlc_renault_etech_t")


def test_exact_vehicle_dlc_is_preferred_over_tuning_archives():
    with tempfile.TemporaryDirectory() as directory:
        game_dir = Path(directory)
        (game_dir / "dlc_renault_etech_t.scs").touch()
        (game_dir / "dlc_renault_t_tuning.scs").touch()

        candidates = archive_candidates(game_dir, "vehicle.renault.etech_t")

        assert candidates[0][1].name == "dlc_renault_etech_t.scs"
        assert best_archive(game_dir, "vehicle.renault.etech_t") == candidates[0][1]


def test_weak_brand_only_match_is_not_auto_imported():
    with tempfile.TemporaryDirectory() as directory:
        game_dir = Path(directory)
        (game_dir / "dlc_daf_tuning_pack.scs").touch()

        assert best_archive(game_dir, "vehicle.daf.unknown_future_truck") is None

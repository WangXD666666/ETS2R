import math
import struct
import tempfile
from pathlib import Path
from types import SimpleNamespace

from automatic_reversing.game_geometry import (
    GameGeometryResolver,
    convex_hull,
    pmc_top_down_contour,
    read_pmg_bounds,
    read_pmg_wheel_points,
)


def _temporary_file(data: bytes, suffix: str):
    directory = tempfile.TemporaryDirectory()
    path = Path(directory.name) / f"fixture{suffix}"
    path.write_bytes(data)
    return directory, path


def test_convex_hull_removes_inner_points():
    hull = convex_hull([(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0), (1.0, 1.0)])

    assert len(hull) == 4
    assert (1.0, 1.0) not in hull


def test_pmg_bounds_are_read_from_game_header():
    data = bytearray(72)
    struct.pack_into("<I", data, 0, 0x506D6715)
    struct.pack_into("<6f", data, 48, -1.2, 0.1, -3.4, 1.3, 4.0, 2.6)
    directory, path = _temporary_file(bytes(data), ".pmg")
    try:
        bounds = read_pmg_bounds(path)
    finally:
        directory.cleanup()

    expected = (-1.2, 0.1, -3.4, 1.3, 4.0, 2.6)
    assert all(math.isclose(actual, wanted, abs_tol=1e-6) for actual, wanted in zip(bounds, expected))


def test_pmg_wheel_locators_provide_all_model_wheel_positions():
    def token(value: str) -> int:
        letters = {character: index for index, character in enumerate("\0" + "0123456789abcdefghijklmnopqrstuvwxyz_")}
        return sum(letters[character] * (38 ** index) for index, character in enumerate(value))

    data = bytearray(200)
    struct.pack_into("<I", data, 0, 0x506D6715)
    struct.pack_into("<i", data, 20, 2)
    struct.pack_into("<i", data, 80, 112)
    struct.pack_into("<Q3ff4fi", data, 112, token("wheel_r_00"), -0.9, 0.4, 12.5, 1.0, 1.0, 0.0, 0.0, 0.0, -1)
    struct.pack_into("<Q3ff4fi", data, 156, token("hook"), 0.0, 1.0, -4.0, 1.0, 1.0, 0.0, 0.0, 0.0, -1)
    directory, path = _temporary_file(bytes(data), ".pmg")
    try:
        wheels = read_pmg_wheel_points(path)
    finally:
        directory.cleanup()

    assert len(wheels) == 1
    assert math.isclose(wheels[0][0], -0.9, abs_tol=1e-6)
    assert math.isclose(wheels[0][1], 12.5, abs_tol=1e-6)


def test_pmc_box_locator_becomes_real_top_down_outline():
    locator_offset = 52
    data = bytearray(locator_offset + 64 + 8)
    struct.pack_into("<10I", data, 0, 6, 0, 1, 0, 0, 40, 40, 48, 52, 48)
    struct.pack_into("<I", data, 48, locator_offset)
    struct.pack_into("<iiIfQ3f4f3f", data, locator_offset, 1, 64, 0, 1.0, 0, 1.0, 0.0, 2.0, 1.0, 0.0, 0.0, 0.0, 2.0, 1.0, 4.0)
    struct.pack_into("<ii", data, locator_offset + 64, 0, -1)
    directory, path = _temporary_file(bytes(data), ".pmc")
    try:
        contour = pmc_top_down_contour(path)
    finally:
        directory.cleanup()

    assert len(contour) == 4
    assert math.isclose(min(x for x, _ in contour), 0.0)
    assert math.isclose(max(x for x, _ in contour), 2.0)
    assert math.isclose(min(z for _, z in contour), 0.0)
    assert math.isclose(max(z for _, z in contour), 4.0)


def test_pmc_convex_locator_transforms_reusable_piece():
    piece_offset = 48
    variant_def_offset = 64
    locator_offset = 68
    vertex_offset = 132
    data = bytearray(vertex_offset + 48)
    struct.pack_into("<10I", data, 0, 6, 0, 1, 0, 1, 40, 40, 40, piece_offset, variant_def_offset)
    struct.pack_into("<4I", data, piece_offset, 0, 4, vertex_offset, vertex_offset + 48)
    struct.pack_into("<I", data, variant_def_offset, locator_offset)
    struct.pack_into("<iiIfQ3f4fI", data, locator_offset, 8, 56, 0, 1.0, 0, 10.0, 0.0, 2.0, 1.0, 0.0, 0.0, 0.0, 0)
    struct.pack_into("<ii", data, locator_offset + 56, 0, -1)
    vertices = [(-1.0, 0.0, -2.0), (1.0, 0.0, -2.0), (1.0, 0.0, 2.0), (-1.0, 0.0, 2.0)]
    for index, vertex in enumerate(vertices):
        struct.pack_into("<3f", data, vertex_offset + index * 12, *vertex)
    directory, path = _temporary_file(bytes(data), ".pmc")
    try:
        contour = pmc_top_down_contour(path)
    finally:
        directory.cleanup()

    assert math.isclose(min(x for x, _ in contour), 9.0)
    assert math.isclose(max(x for x, _ in contour), 11.0)
    assert math.isclose(min(z for _, z in contour), 0.0)
    assert math.isclose(max(z for _, z in contour), 4.0)


def test_pmc_cylinder_depth_uses_local_z_axis():
    locator_offset = 52
    data = bytearray(locator_offset + 60 + 8)
    struct.pack_into("<10I", data, 0, 6, 0, 1, 0, 0, 40, 40, 48, 52, 48)
    struct.pack_into("<I", data, 48, locator_offset)
    struct.pack_into(
        "<iiIfQ3f4f2f",
        data,
        locator_offset,
        3,
        60,
        0,
        1.0,
        0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        0.5,
        4.0,
    )
    struct.pack_into("<ii", data, locator_offset + 60, 0, -1)
    directory, path = _temporary_file(bytes(data), ".pmc")
    try:
        contour = pmc_top_down_contour(path)
    finally:
        directory.cleanup()

    assert math.isclose(min(x for x, _ in contour), -0.5)
    assert math.isclose(max(x for x, _ in contour), 0.5)
    assert math.isclose(min(z for _, z in contour), -2.0)
    assert math.isclose(max(z for _, z in contour), 2.0)


def test_chassis_matching_uses_live_axle_properties():
    with tempfile.TemporaryDirectory() as directory:
        chassis_dir = Path(directory) / "chassis"
        chassis_dir.mkdir()
        (chassis_dir / "6x2_midlift.sii").write_text(
            "\n".join((
                "residual_travel[]: 0.1", "residual_travel[]: 0.1", "residual_travel[]: 0.1",
                "powered_axle[]: false", "powered_axle[]: false", "powered_axle[]: true",
                "liftable_axle[]: false", "liftable_axle[]: true", "liftable_axle[]: false",
            )),
            encoding="utf-8",
        )
        (chassis_dir / "6x2_taglift.sii").write_text(
            "\n".join((
                "residual_travel[]: 0.1", "residual_travel[]: 0.1", "residual_travel[]: 0.1",
                "powered_axle[]: false", "powered_axle[]: true", "powered_axle[]: false",
                "liftable_axle[]: false", "liftable_axle[]: false", "liftable_axle[]: true",
            )),
            encoding="utf-8",
        )
        wheels = [
            SimpleNamespace(local_x=x, local_z=z, steerable=index == 0, powered=index == 1, liftable=index == 2)
            for index, z in enumerate((-2.0, 1.0, 2.3))
            for x in (-1.0, 1.0)
        ]
        resolver = GameGeometryResolver(Path(directory), Path(directory))

        selected = resolver._best_chassis(chassis_dir, wheels, 3, steerable_first_default=True)

        assert selected is not None
        assert selected.name == "6x2_taglift.sii"


def test_market_trailer_id_overrides_truncated_telemetry_axles():
    with tempfile.TemporaryDirectory() as directory:
        chassis_dir = Path(directory) / "chassis"
        chassis_dir.mkdir()
        for name, count in (("ch_4_1.sii", 5), ("ch_7_3.sii", 10)):
            (chassis_dir / name).write_text(
                "\n".join(["residual_travel[]: 0.1"] * count + ["steerable_axle[]: true"] * count),
                encoding="utf-8",
            )
        wheels = [
            SimpleNamespace(local_x=x, local_z=float(axle), steerable=True, powered=False, liftable=False)
            for axle in range(7)
            for x in (-1.0, 1.0)
        ]
        resolver = GameGeometryResolver(Path(directory), Path(directory))

        selected = resolver._best_chassis(
            chassis_dir, wheels, 7, telemetry_id="scs_lowbed.ch_7_3x2esii"
        )

        assert selected is not None
        assert selected.name == "ch_7_3.sii"


def test_collision_variant_is_selected_from_live_reference_points():
    first_locator = 56
    second_locator = 128
    data = bytearray(200)
    struct.pack_into("<10I", data, 0, 6, 0, 2, 0, 0, 40, 40, 48, 56, 48)
    struct.pack_into("<2I", data, 48, first_locator, second_locator)
    struct.pack_into(
        "<iiIfQ3f4f3f", data, first_locator, 1, 64, 0, 1.0, 0, 0.0, 0.0, 0.0,
        1.0, 0.0, 0.0, 0.0, 2.0, 1.0, 2.0,
    )
    struct.pack_into("<ii", data, first_locator + 64, 0, -1)
    struct.pack_into(
        "<iiIfQ3f4f3f", data, second_locator, 1, 64, 0, 1.0, 0, 0.0, 0.0, 2.0,
        1.0, 0.0, 0.0, 0.0, 2.5, 1.0, 8.0,
    )
    struct.pack_into("<ii", data, second_locator + 64, 0, -1)
    directory, path = _temporary_file(bytes(data), ".pmc")
    try:
        resolver = GameGeometryResolver()
        contour, variant_index, variant_count = resolver._best_collision_variant(path, ((0.0, -1.0), (0.0, 5.0)))
    finally:
        directory.cleanup()

    assert variant_count == 2
    assert variant_index == 1
    assert math.isclose(max(z for _, z in contour), 6.0)


def test_market_trailer_id_prefers_legacy_game_definition():
    with tempfile.TemporaryDirectory() as directory:
        def_root = Path(directory)
        legacy = def_root / "vehicle" / "trailer" / "scs_lowbed"
        owned = def_root / "vehicle" / "trailer_owned" / "scs.lowbed"
        legacy.mkdir(parents=True)
        owned.mkdir(parents=True)
        resolver = GameGeometryResolver(def_root, def_root)

        selected, is_legacy = resolver._trailer_definition_dir("scs_lowbed.ch_7_3x2esii")

        assert selected == legacy
        assert is_legacy is True


def test_market_trailer_body_suffix_resolves_hierarchical_legacy_definition():
    with tempfile.TemporaryDirectory() as directory:
        def_root = Path(directory)
        expected = def_root / "vehicle" / "trailer" / "scs_box" / "curtain_sider"
        expected.mkdir(parents=True)
        resolver = GameGeometryResolver(def_root, def_root)

        selected, is_legacy = resolver._trailer_definition_dir("scs_box.curtain_sider")

        assert selected == expected
        assert is_legacy is True


def test_market_trailer_compact_variant_suffix_resolves_base_family():
    with tempfile.TemporaryDirectory() as directory:
        def_root = Path(directory)
        expected = def_root / "vehicle" / "trailer" / "scs_flatbed"
        expected.mkdir(parents=True)
        resolver = GameGeometryResolver(def_root, def_root)

        selected, is_legacy = resolver._trailer_definition_dir("scs_flatbed.flatbedx2esii")

        assert selected == expected
        assert is_legacy is True


def test_explicit_definition_selection_is_validated_against_candidates():
    with tempfile.TemporaryDirectory() as directory:
        def_root = Path(directory)
        selected_file = def_root / "vehicle" / "truck" / "fixture" / "chassis" / "selected.sii"
        selected_file.parent.mkdir(parents=True)
        selected_file.write_text("collision: \"/vehicle/selected.pmc\"", encoding="utf-8")
        resolver = GameGeometryResolver(def_root, def_root)
        candidates = resolver.truck_definition_candidates("vehicle.fixture")["chassis"]

        selected = resolver._selected_definition(
            {"chassis": "vehicle/truck/fixture/chassis/selected.sii"}, "chassis", candidates
        )
        rejected = resolver._selected_definition({"chassis": "../../outside.sii"}, "chassis", candidates)

        assert selected == selected_file
        assert rejected is None


def test_extended_trailer_chassis_selects_long_body():
    with tempfile.TemporaryDirectory() as directory:
        body_dir = Path(directory)
        (body_dir / "short.sii").write_text(
            "body_type: lowboy\ntotal_size: (2.55, 4.0, 13.62)", encoding="utf-8"
        )
        (body_dir / "long.sii").write_text(
            "body_type: lowboy\ntotal_size: (2.55, 4.0, 17.52)", encoding="utf-8"
        )
        resolver = GameGeometryResolver(body_dir, body_dir)

        selected = resolver._best_trailer_body(body_dir, "lowboy", 9.0, "ch_4_ext")

        assert selected is not None
        assert selected.name == "long.sii"

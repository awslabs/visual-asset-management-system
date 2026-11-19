#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Units and up axis: the pure planning functions, then measured bounding boxes through real
conversions (``conversion_worker.convert_model``)."""

import os
import sys
import zipfile

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import new_format_helpers as helpers  # noqa: E402

import units_axes  # noqa: E402
from units_axes import Options  # noqa: E402


@pytest.mark.unit
class TestOptions:
    def test_a_missing_empty_or_null_value_reads_as_the_default(self):
        assert Options.from_config({}) == Options()
        assert Options.from_config(None) == Options()
        assert Options.from_config({"sourceUnits": "", "targetUnits": None, "sourceUpAxis": "  "}) == Options()
        assert Options().is_default

    def test_values_are_case_insensitive(self):
        options = Options.from_config({"sourceUnits": "MM", "targetUpAxis": "Z"})
        assert (options.source_units, options.target_up_axis) == ("mm", "z")

    @pytest.mark.parametrize("config", [{"sourceUnits": "furlong"}, {"targetUnits": "auto"},
                                        {"sourceUpAxis": "keep"}, {"targetUpAxis": "x"}, {"sourceUnits": 5}])
    def test_a_value_outside_the_allowed_set_is_refused_naming_the_tag(self, config):
        with pytest.raises(ValueError, match=r"^(SOURCE|TARGET)_(UNITS|UP_AXIS) must be one of"):
            Options.from_config(config)


@pytest.mark.unit
class TestValidateRequest:
    def test_the_defaults_are_always_legal(self):
        for input_extension in (".stl", ".usd", ".glb", ".xyz", ".dae", ".3mf"):
            for output_extension in (".glb", ".gltf", ".obj", ".usdc", ".dae", ".3mf", ".off"):
                units_axes.validate_request(input_extension, output_extension, Options())

    @pytest.mark.parametrize("output", [".glb", ".gltf"])
    def test_a_gltf_target_cannot_have_another_unit_or_a_z_axis(self, output):
        with pytest.raises(ValueError, match="always in metres"):
            units_axes.validate_request(".usda", output, Options(target_units="mm"))
        with pytest.raises(ValueError, match="always Y up"):
            units_axes.validate_request(".usda", output, Options(target_up_axis="z"))
        units_axes.validate_request(".usda", output, Options(target_units="m", target_up_axis="y"))

    def test_3mf_does_not_support_km_yd_or_a_y_axis(self):
        for unit in ("km", "yd"):
            with pytest.raises(ValueError, match="3MF does not support"):
                units_axes.validate_request(".usda", ".3mf", Options(target_units=unit))
        with pytest.raises(ValueError, match="always Z up"):
            units_axes.validate_request(".usda", ".3mf", Options(target_up_axis="y"))

    @pytest.mark.parametrize("input_extension", [".stl", ".obj", ".ply", ".off", ".xyz"])
    def test_a_target_with_an_undeclared_source_names_the_option_to_set(self, input_extension):
        with pytest.raises(ValueError, match="SOURCE_UNITS"):
            units_axes.validate_request(input_extension, ".usdc", Options(target_units="mm"))
        with pytest.raises(ValueError, match="SOURCE_UP_AXIS"):
            units_axes.validate_request(input_extension, ".obj", Options(target_up_axis="y"))
        units_axes.validate_request(input_extension, ".usdc", Options(source_units="mm", target_units="m"))

    def test_a_declared_source_needs_no_source_option(self):
        units_axes.validate_request(".usda", ".obj", Options(target_units="mm", target_up_axis="z"))


@pytest.mark.unit
class TestPlan:
    def test_the_defaults_change_nothing_for_an_undeclaring_source(self):
        for output in (".stl", ".obj", ".ply", ".off", ".glb", ".gltf", ".usdc", ".dae"):
            plan = units_axes.plan(Options(), output)
            assert not plan.changes_geometry, output

    def test_a_declared_unit_converts_to_the_fixed_gltf_convention(self):
        plan = units_axes.plan(Options(), ".glb", 0.01, 'z')
        assert plan.scale == pytest.approx(0.01) and plan.rotation == 'z_to_y'
        assert (plan.meters_per_unit, plan.up_axis) == (1.0, 'y')

    def test_usd_keeps_the_declared_unit_and_axis_by_default(self):
        plan = units_axes.plan(Options(), ".usdc", 0.01, 'z')
        assert plan.scale == 1.0 and plan.rotation is None
        assert (plan.meters_per_unit, plan.up_axis) == (0.01, 'z')

    def test_usd_from_an_undeclaring_source_declares_metres_and_y_up(self):
        plan = units_axes.plan(Options(), ".usdc")
        assert (plan.meters_per_unit, plan.up_axis, plan.scale, plan.rotation) == (1.0, 'y', 1.0, None)

    def test_an_explicit_source_overrides_what_the_file_declares(self):
        plan = units_axes.plan(Options(source_units="mm"), ".glb", 0.01, 'y')
        assert plan.scale == pytest.approx(0.001)

    def test_usd_target_units_scale_and_declare(self):
        plan = units_axes.plan(Options(source_units="mm", target_units="cm"), ".usda")
        assert plan.scale == pytest.approx(0.1) and plan.meters_per_unit == pytest.approx(0.01)

    def test_a_target_without_a_known_source_is_refused(self):
        with pytest.raises(ValueError, match="SOURCE_UNITS"):
            units_axes.plan(Options(target_units="mm"), ".obj")
        with pytest.raises(ValueError, match="SOURCE_UP_AXIS"):
            units_axes.plan(Options(target_up_axis="y"), ".obj")

    def test_a_baked_target_applies_to_the_vertices_and_declares_nothing(self):
        plan = units_axes.plan(Options(source_units="in", target_units="mm", source_up_axis="z", target_up_axis="y"),
                               ".stl")
        assert plan.scale == pytest.approx(25.4) and plan.rotation == 'z_to_y'
        assert plan.meters_per_unit is None and plan.up_axis is None

    def test_3mf_is_always_z_up_and_never_km_or_yd(self):
        plan = units_axes.plan(Options(), ".3mf", 1.0, 'y')
        assert plan.rotation == 'y_to_z' and plan.up_axis == 'z' and plan.meters_per_unit == 1.0
        km = units_axes.plan(Options(), ".3mf", 1000.0, 'z')
        assert km.meters_per_unit == 1.0 and km.scale == pytest.approx(1000.0)
        unknown = units_axes.plan(Options(), ".3mf")
        assert (unknown.meters_per_unit, unknown.up_axis, unknown.scale) == (0.001, 'z', 1.0)

    def test_the_matrices_rotate_as_documented(self):
        z_to_y = np.array(units_axes.transform_matrix(units_axes.Plan(rotation='z_to_y')))
        np.testing.assert_allclose(z_to_y @ [1, 2, 3, 1], [1, 3, -2, 1])
        y_to_z = np.array(units_axes.transform_matrix(units_axes.Plan(rotation='y_to_z')))
        np.testing.assert_allclose(y_to_z @ [1, 2, 3, 1], [1, -3, 2, 1])
        assert units_axes.transform_matrix(units_axes.Plan()) is None

    @pytest.mark.parametrize("name,expected", [("millimeter", 0.001), ("inch", 0.0254), ("meters", 1.0),
                                               ("0.01 * meters", 0.01), ("parsec", None), (None, None)])
    def test_unit_names_as_trimesh_spells_them(self, name, expected):
        assert units_axes.meters_per_unit_from_name(name) == expected


def _bounds_extents(path):
    import trimesh
    loaded = trimesh.load(path)
    return np.array(loaded.bounds[1] - loaded.bounds[0])


@pytest.mark.unit
class TestMeasuredBoundingBoxes:
    """A 2 x 3 x 4 box through real conversions, measured on the written file."""

    @pytest.fixture()
    def box_stl(self, tmp_path):
        import trimesh
        path = tmp_path / "src" / "box.stl"
        path.parent.mkdir()
        path.write_bytes(trimesh.creation.box(extents=(2.0, 3.0, 4.0)).export(file_type="stl"))
        return str(path)

    def test_the_defaults_leave_every_existing_conversion_unchanged(self, tmp_path, box_stl):
        for output in (".obj", ".ply", ".glb", ".stl"):
            out = helpers.convert(tmp_path, box_stl, output, run_dir=f"d{output}")
            np.testing.assert_allclose(_bounds_extents(out), [2, 3, 4], atol=1e-6)

    def test_mm_z_up_to_a_glb_is_metres_and_y_up(self, tmp_path, box_stl):
        out = helpers.convert(tmp_path, box_stl, ".glb",
                              {"sourceUnits": "mm", "sourceUpAxis": "z"}, run_dir="glb")
        np.testing.assert_allclose(_bounds_extents(out), [0.002, 0.004, 0.003], atol=1e-9)

    def test_an_obj_is_scaled_and_rotated_in_its_vertices(self, tmp_path, box_stl):
        out = helpers.convert(tmp_path, box_stl, ".obj", {
            "sourceUnits": "mm", "targetUnits": "m", "sourceUpAxis": "z", "targetUpAxis": "y"}, run_dir="obj")
        np.testing.assert_allclose(_bounds_extents(out), [0.002, 0.004, 0.003], atol=1e-9)

    def test_inches_to_millimetres_in_a_ply(self, tmp_path, box_stl):
        out = helpers.convert(tmp_path, box_stl, ".ply",
                              {"sourceUnits": "in", "targetUnits": "mm"}, run_dir="ply")
        np.testing.assert_allclose(_bounds_extents(out), [50.8, 76.2, 101.6], atol=1e-4)

    def test_usd_declares_the_convention_it_wrote(self, tmp_path, box_stl):
        from pxr import Usd, UsdGeom
        out = helpers.convert(tmp_path, box_stl, ".usda", {
            "sourceUnits": "mm", "targetUnits": "cm", "sourceUpAxis": "z", "targetUpAxis": "z"}, run_dir="usd")
        stage = Usd.Stage.Open(out)
        assert UsdGeom.GetStageMetersPerUnit(stage) == pytest.approx(0.01)
        assert str(UsdGeom.GetStageUpAxis(stage)) == "Z"
        assert stage.GetDefaultPrim().GetPath().pathString == "/Model"
        from usd_io import read_usd
        scene = read_usd(out, os.path.dirname(out))[0]
        np.testing.assert_allclose(scene.extents, [0.2, 0.3, 0.4], atol=1e-6)

    def test_an_undeclared_usd_source_is_written_as_metres_and_y_up(self, tmp_path, box_stl):
        from pxr import Usd, UsdGeom
        out = helpers.convert(tmp_path, box_stl, ".usdc", run_dir="usd-default")
        stage = Usd.Stage.Open(out)
        assert UsdGeom.GetStageMetersPerUnit(stage) == 1.0
        assert str(UsdGeom.GetStageUpAxis(stage)) == "Y"

    def test_the_3mf_unit_attribute_is_the_declared_unit(self, tmp_path, box_stl):
        out = helpers.convert(tmp_path, box_stl, ".3mf", {"sourceUnits": "in", "targetUnits": "in"}, run_dir="3mf")
        with zipfile.ZipFile(out) as archive:
            model = next(archive.read(n) for n in archive.namelist() if n.endswith(".model"))
        assert b'unit="inch"' in model and b'unit="millimeter"' not in model

    def test_a_dae_declares_unit_and_axis(self, tmp_path, box_stl):
        out = helpers.convert(tmp_path, box_stl, ".dae", {
            "sourceUnits": "mm", "targetUnits": "cm", "sourceUpAxis": "z", "targetUpAxis": "z"}, run_dir="dae")
        text = open(out, encoding="utf-8").read()
        assert 'meter="0.01"' in text and "<up_axis>Z_UP</up_axis>" in text
        np.testing.assert_allclose(_bounds_extents(out), [0.2, 0.3, 0.4], atol=1e-6)

    def test_a_declared_dae_unit_is_read_back(self, tmp_path, box_stl):
        dae = helpers.convert(tmp_path, box_stl, ".dae", {"sourceUnits": "cm"}, run_dir="dae1")
        out = helpers.convert(tmp_path, dae, ".glb", run_dir="dae2")
        # 2 x 3 x 4 centimetres is 0.02 x 0.03 x 0.04 metres in the glTF convention.
        np.testing.assert_allclose(_bounds_extents(out), [0.02, 0.03, 0.04], atol=1e-6)

    def test_an_illegal_target_for_the_source_fails_naming_the_option(self, tmp_path, box_stl):
        with pytest.raises(ValueError, match="SOURCE_UNITS"):
            helpers.convert(tmp_path, box_stl, ".usdc", {"targetUnits": "mm"}, run_dir="refused")

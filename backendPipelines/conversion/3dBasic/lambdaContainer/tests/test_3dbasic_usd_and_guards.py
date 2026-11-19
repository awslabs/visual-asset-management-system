#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The USD writer and reader, the references a USD file may name, and the guards the XML and archive
formats pass before a loader reads them (real files, real libraries)."""

import io
import os
import sys
import zipfile

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import new_format_helpers as helpers  # noqa: E402

pytest.importorskip("pxr")
trimesh = pytest.importorskip("trimesh")
pytest.importorskip("collada")
pytest.importorskip("PIL")

from pxr import Sdf, Usd, UsdGeom, UsdValidation  # noqa: E402

import model_formats  # noqa: E402
import usd_io  # noqa: E402


def _validation_errors(path):
    """The OpenUSD validators over a written file (ComplianceChecker is not in the usd-core wheel; the
    UsdValidation registry is what it ships)."""
    registry = UsdValidation.ValidationRegistry()
    context = UsdValidation.ValidationContext(registry.GetOrLoadAllValidators())
    errors = list(context.Validate(Usd.Stage.Open(path)))
    if path.endswith(".usdz"):
        errors += list(context.Validate(Sdf.Layer.FindOrOpen(path)))
    return [f"{e.GetName()}: {e.GetMessage()}" for e in errors]


def _scene_with_everything():
    cube = helpers.textured_cube()
    plain = trimesh.creation.icosphere(subdivisions=1)
    plain.visual = trimesh.visual.ColorVisuals(plain, vertex_colors=[200, 40, 40, 255])
    cloud = trimesh.PointCloud(np.random.default_rng(3).random((20, 3)))
    scene = trimesh.Scene()
    scene.add_geometry(cube, node_name="cube", geom_name="cube")
    moved = np.eye(4)
    moved[:3, 3] = [5, 0, 0]
    scene.add_geometry(plain, node_name="ball", geom_name="ball", transform=moved)
    scene.add_geometry(cloud, node_name="cloud", geom_name="cloud")
    return scene


@pytest.mark.unit
class TestUsdOutputIsValid:
    @pytest.mark.parametrize("extension", [".usda", ".usdc", ".usdz"])
    @pytest.mark.parametrize("up_axis,meters", [("y", 1.0), ("z", 0.001)])
    def test_the_openusd_validators_report_nothing(self, tmp_path, extension, up_axis, meters):
        export = tmp_path / "export"
        export.mkdir()
        path = str(export / f"scene{extension}")
        usd_io.write_usd(_scene_with_everything(), path, meters, up_axis, extension)
        assert _validation_errors(path) == []
        stage = Usd.Stage.Open(path)
        assert UsdGeom.GetStageMetersPerUnit(stage) == pytest.approx(meters)
        assert str(UsdGeom.GetStageUpAxis(stage)).lower() == up_axis
        assert stage.GetDefaultPrim()

    def test_a_usdz_is_one_file_that_carries_its_textures(self, tmp_path):
        export = tmp_path / "export"
        export.mkdir()
        path = str(export / "scene.usdz")
        usd_io.write_usd(_scene_with_everything(), path, 1.0, "y", ".usdz")
        assert os.listdir(export) == ["scene.usdz"], "the package staging must not be in the export directory"
        with zipfile.ZipFile(path) as archive:
            assert any(name.endswith(".png") for name in archive.namelist()), archive.namelist()
        scene, _, _ = usd_io.read_usd(path, str(export))
        assert any(getattr(g.visual.material, "baseColorTexture", None) is not None
                   for g in scene.geometry.values() if hasattr(g, "faces") and g.visual.defined)

    def test_a_loose_file_keeps_its_textures_beside_it(self, tmp_path):
        export = tmp_path / "export"
        export.mkdir()
        usd_io.write_usd(_scene_with_everything(), str(export / "scene.usdc"), 1.0, "y", ".usdc")
        assert sorted(os.listdir(export)) == ["scene.usdc", "textures"]

    def test_transforms_become_xforms_and_points_stay_points(self, tmp_path):
        export = tmp_path / "export"
        export.mkdir()
        path = str(export / "scene.usda")
        usd_io.write_usd(_scene_with_everything(), path, 1.0, "y", ".usda")
        stage = Usd.Stage.Open(path)
        assert stage.GetPrimAtPath("/Model/ball").IsA(UsdGeom.Xform)
        assert stage.GetPrimAtPath("/Model/ball").GetAttribute("xformOp:transform").Get() is not None
        assert stage.GetPrimAtPath("/Model/cloud/Geometry").IsA(UsdGeom.Points)
        assert stage.GetPrimAtPath("/Model/cube/Geometry").IsA(UsdGeom.Mesh)
        scene, _, _ = usd_io.read_usd(path, str(export))
        np.testing.assert_allclose(scene.bounds[1][0], 5.0 + 1.0, atol=1e-5)

    def test_names_that_are_not_prim_names_are_made_valid_and_unique(self, tmp_path):
        scene = trimesh.Scene()
        # Three different names that all become the same prim name.
        for index, name in enumerate(("my part", "my_part", "my-part")):
            scene.add_geometry(trimesh.creation.box(), node_name=name, geom_name=f"g{index}")
        export = tmp_path / "export"
        export.mkdir()
        path = str(export / "scene.usda")
        usd_io.write_usd(scene, path, 1.0, "y", ".usda")
        assert _validation_errors(path) == []
        stage = Usd.Stage.Open(path)
        assert len([p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)]) == 3

    def test_an_empty_model_writes_nothing(self, tmp_path):
        with pytest.raises(ValueError, match="contains no geometry"):
            usd_io.write_usd(trimesh.Scene(), str(tmp_path / "x.usda"), 1.0, "y", ".usda")


def _write_layer(path, text):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


_MESH = """
def Mesh "M" {
    point3f[] points = [(0,0,0),(1,0,0),(0,1,0)]
    int[] faceVertexCounts = [3]
    int[] faceVertexIndices = [0,1,2]
}
"""


@pytest.mark.unit
class TestUsdReferences:
    def _stage_dir(self, tmp_path):
        directory = tmp_path / "input"
        directory.mkdir()
        return directory

    def test_a_sublayer_inside_the_asset_is_read(self, tmp_path):
        directory = self._stage_dir(tmp_path)
        _write_layer(directory / "geo.usda", "#usda 1.0\n" + _MESH)
        _write_layer(directory / "root.usda", '#usda 1.0\n(\n    subLayers = [@./geo.usda@]\n)\n')
        scene, _, _ = usd_io.read_usd(str(directory / "root.usda"), str(directory))
        assert len(scene.geometry) == 1

    @pytest.mark.parametrize("reference,why", [
        ("/etc/passwd.usda", "absolute path"),
        ("../outside.usda", "'..'"),
        ("http://example.com/a.usda", "URI scheme"),
        ("s3://bucket/a.usda", "URI scheme"),
    ])
    def test_a_layer_reference_that_is_not_relative_to_the_asset_fails_naming_it(self, tmp_path, reference, why):
        directory = self._stage_dir(tmp_path)
        _write_layer(directory / "root.usda", f'#usda 1.0\n(\n    subLayers = [@{reference}@]\n)\n' + _MESH)
        with pytest.raises(usd_io.UsdReferenceError, match=why):
            usd_io.read_usd(str(directory / "root.usda"), str(directory))

    def test_a_missing_layer_fails_naming_it(self, tmp_path):
        directory = self._stage_dir(tmp_path)
        _write_layer(directory / "root.usda", '#usda 1.0\n(\n    subLayers = [@./gone.usda@]\n)\n' + _MESH)
        with pytest.raises(usd_io.UsdReferenceError, match="gone.usda"):
            usd_io.read_usd(str(directory / "root.usda"), str(directory))

    def test_a_missing_or_unsafe_texture_is_left_out_and_the_geometry_converts(self, tmp_path):
        directory = self._stage_dir(tmp_path)
        _write_layer(directory / "root.usda", """#usda 1.0
def Mesh "M" {
    point3f[] points = [(0,0,0),(1,0,0),(0,1,0)]
    int[] faceVertexCounts = [3]
    int[] faceVertexIndices = [0,1,2]
}
def Scope "S" {
    asset a = @textures/missing.png@
    asset b = @/etc/hostname@
    asset c = @../up.png@
}
""")
        scene, _, _ = usd_io.read_usd(str(directory / "root.usda"), str(directory))
        assert len(scene.geometry) == 1

    def test_a_layer_cycle_terminates(self, tmp_path):
        directory = self._stage_dir(tmp_path)
        _write_layer(directory / "a.usda", '#usda 1.0\n(\n    subLayers = [@./b.usda@]\n)\n' + _MESH)
        _write_layer(directory / "b.usda", '#usda 1.0\n(\n    subLayers = [@./a.usda@]\n)\n')
        # USD itself refuses the cycle when composing; the dependency walk must not loop on it.
        try:
            usd_io.read_usd(str(directory / "a.usda"), str(directory))
        except ValueError:
            pass

    def test_the_dependency_walk_is_bounded(self, tmp_path, monkeypatch):
        directory = self._stage_dir(tmp_path)
        monkeypatch.setattr(usd_io, "MAX_DEPTH", 2)
        for index in range(5):
            _write_layer(directory / f"l{index}.usda",
                         f'#usda 1.0\n(\n    subLayers = [@./l{index + 1}.usda@]\n)\n' + _MESH)
        _write_layer(directory / "l5.usda", "#usda 1.0\n" + _MESH)
        with pytest.raises(usd_io.UsdReferenceError, match="deeper than"):
            usd_io.read_usd(str(directory / "l0.usda"), str(directory))

    def test_a_scene_that_expands_past_the_limit_fails(self, tmp_path):
        import scene_limits
        directory = self._stage_dir(tmp_path)
        _write_layer(directory / "root.usda", "#usda 1.0\n" + "".join(
            _MESH.replace('"M"', f'"M{i}"') for i in range(4)))
        limit = scene_limits.SceneLimit("root.usda", max_triangles=2)
        with pytest.raises(ValueError, match="expands past"):
            usd_io.read_usd(str(directory / "root.usda"), str(directory), limit)

    def test_a_file_that_is_not_usd_fails_as_a_user_error(self, tmp_path):
        directory = self._stage_dir(tmp_path)
        _write_layer(directory / "bad.usda", "this is not usd")
        with pytest.raises(ValueError):
            usd_io.read_usd(str(directory / "bad.usda"), str(directory))

    def test_a_usd_with_no_geometry_is_refused(self, tmp_path):
        directory = self._stage_dir(tmp_path)
        _write_layer(directory / "empty.usda", '#usda 1.0\ndef Xform "X" {}\n')
        with pytest.raises(ValueError, match="no geometry"):
            usd_io.read_usd(str(directory / "empty.usda"), str(directory))

    def test_the_default_unit_of_a_layer_without_one_is_usds_own(self, tmp_path):
        directory = self._stage_dir(tmp_path)
        _write_layer(directory / "root.usda", "#usda 1.0\n" + _MESH)
        _, meters, up = usd_io.read_usd(str(directory / "root.usda"), str(directory))
        assert meters == pytest.approx(0.01) and up == "y"


@pytest.mark.unit
class TestXmlAndArchiveGuards:
    _DTD = '<?xml version="1.0"?><!DOCTYPE COLLADA [<!ENTITY a "aaaa">]><COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema"/>'

    def _job(self, directory, name):
        return {"input_file": str(directory / name), "input_dir": str(directory), "input_relative_path": name,
                "by_reference": {}, "input_name": name}

    def test_a_dae_with_a_doctype_or_entity_is_refused_before_the_loader(self, tmp_path):
        (tmp_path / "bomb.dae").write_text(self._DTD)
        with pytest.raises(ValueError, match="document type declaration|entity"):
            model_formats.load_model(self._job(tmp_path, "bomb.dae"), trimesh)

    def test_a_dae_that_is_not_xml_is_refused(self, tmp_path):
        (tmp_path / "bad.dae").write_text("not xml at all")
        with pytest.raises(ValueError, match="Invalid XML"):
            model_formats.load_model(self._job(tmp_path, "bad.dae"), trimesh)

    def test_a_dae_declaring_x_up_is_refused(self, tmp_path):
        text = trimesh.creation.box().export(file_type="dae").decode().replace("Y_UP", "X_UP")
        (tmp_path / "x.dae").write_text(text)
        with pytest.raises(ValueError, match="X as its up axis"):
            model_formats.load_model(self._job(tmp_path, "x.dae"), trimesh)

    def _threemf(self, tmp_path, mutate):
        data = trimesh.creation.box().export(file_type="3mf")
        out = tmp_path / "m.3mf"
        with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w") as target:
            for info in source.infolist():
                content = source.read(info.filename)
                result = mutate(info.filename, content)
                if result is not None:
                    target.writestr(*result)
        return out

    def test_a_3mf_model_with_a_doctype_is_refused(self, tmp_path):
        def mutate(name, content):
            if name.endswith(".model"):
                content = content.replace(b"<model", b'<!DOCTYPE model [<!ENTITY a "b">]><model', 1)
            return name, content
        self._threemf(tmp_path, mutate)
        with pytest.raises(ValueError, match="3MF package"):
            model_formats.load_model(self._job(tmp_path, "m.3mf"), trimesh)

    def test_a_3mf_member_with_a_path_that_leaves_the_package_is_refused(self, tmp_path):
        def mutate(name, content):
            return name, content
        out = self._threemf(tmp_path, mutate)
        with zipfile.ZipFile(out, "a") as archive:
            archive.writestr("../evil.txt", b"x")
        with pytest.raises(ValueError, match="not allowed"):
            model_formats.load_model(self._job(tmp_path, "m.3mf"), trimesh)

    def test_a_3mf_that_is_not_a_zip_is_refused(self, tmp_path):
        (tmp_path / "m.3mf").write_bytes(b"PK nothing")
        with pytest.raises(ValueError, match="3MF package"):
            model_formats.load_model(self._job(tmp_path, "m.3mf"), trimesh)

    def test_a_3mf_over_the_disk_budget_is_refused_from_its_central_directory(self, tmp_path, monkeypatch):
        import archive_guard
        self._threemf(tmp_path, lambda name, content: (name, content))
        monkeypatch.setattr(archive_guard, "free_disk_bytes", lambda _path: archive_guard.OUTPUT_DISK_HEADROOM_BYTES + 10)
        with pytest.raises(ValueError, match="3MF package"):
            model_formats.load_model(self._job(tmp_path, "m.3mf"), trimesh)

    def test_a_good_3mf_and_dae_load_with_their_declared_unit(self, tmp_path):
        self._threemf(tmp_path, lambda name, content: (name, content))
        loaded, meters, up = model_formats.load_model(self._job(tmp_path, "m.3mf"), trimesh)
        assert meters == pytest.approx(0.001) and up == "z"
        (tmp_path / "a.dae").write_bytes(trimesh.creation.box().export(file_type="dae"))
        loaded, meters, up = model_formats.load_model(self._job(tmp_path, "a.dae"), trimesh)
        assert meters == 1.0 and up == "y"

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


def _validator_names(path):
    return {entry.split(":", 1)[0] for entry in _validation_errors(path)}


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
        # Beside the layer, not in a sub-folder: the workflow's output path extension is inserted before
        # each file's own name, which would separate a sub-folder from its layer (D3).
        assert sorted(os.listdir(export)) == ["material_0.png", "scene.usdc"]
        assert "@./material_0.png@" in Sdf.Layer.FindOrOpen(str(export / "scene.usdc")).ExportToString()

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
        ("../outside.usda", "leaves the asset"),
        ("http://example.com/a.usda", "URI scheme"),
        ("s3://bucket/a.usda", "URI scheme"),
        ("./gone.usda", "not among the files fetched"),
    ])
    def test_a_layer_that_cannot_be_used_is_removed_before_the_stage_opens(self, tmp_path, reference, why):
        directory = self._stage_dir(tmp_path)
        _write_layer(directory / "root.usda", f'#usda 1.0\n(\n    subLayers = [@{reference}@]\n)\n' + _MESH)
        skipped = []
        usd_io.confine_dependencies(str(directory / "root.usda"), str(directory), skipped)
        assert [(name, is_layer) for name, _, is_layer in skipped] == [(reference, True)]
        assert why in skipped[0][1]
        # The local copy no longer names it, so the resolver cannot open it.
        assert reference not in (directory / "root.usda").read_text()
        scene, _, _ = usd_io.read_usd(str(directory / "root.usda"), str(directory))
        assert len(scene.geometry) == 1

    def test_a_scene_whose_only_geometry_layer_is_missing_fails_naming_it(self, tmp_path):
        directory = self._stage_dir(tmp_path)
        _write_layer(directory / "root.usda", '#usda 1.0\n(\n    subLayers = [@./gone.usda@]\n)\n')
        with pytest.raises(ValueError, match=r"no geometry.*gone\.usda"):
            usd_io.read_usd(str(directory / "root.usda"), str(directory))

    def test_a_package_naming_an_absolute_layer_fails(self, tmp_path):
        directory = self._stage_dir(tmp_path)
        _write_layer(directory / "root.usda", '#usda 1.0\n(\n    subLayers = [@/etc/x.usda@]\n)\n' + _MESH)
        # UsdUtils' packager localises every dependency, so the package is written by hand: one stored
        # member, padded so its data starts on a 64-byte boundary as USDZ requires.
        package = directory / "p.usdz"
        data = (directory / "root.usda").read_bytes()
        with zipfile.ZipFile(package, "w", zipfile.ZIP_STORED) as archive:
            info = zipfile.ZipInfo("root.usda")
            header = 30 + len("root.usda")
            info.extra = b"\x00" * ((64 - header % 64) % 64 + 4)
            info.extra = b"\xff\xff" + (len(info.extra) - 4).to_bytes(2, "little") + b"\x00" * (len(info.extra) - 4)
            archive.writestr(info, data)
        with pytest.raises(usd_io.UsdReferenceError, match="absolute path"):
            usd_io.confine_dependencies(str(package), str(directory))

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
        skipped = []
        usd_io.confine_dependencies(str(directory / "l0.usda"), str(directory), skipped)
        assert skipped and "deeper than 2" in skipped[0][1], skipped

    def test_a_scene_that_expands_past_the_limit_fails(self, tmp_path):
        import scene_limits
        directory = self._stage_dir(tmp_path)
        _write_layer(directory / "root.usda", "#usda 1.0\n" + "".join(
            _MESH.replace('"M"', f'"M{i}"') for i in range(4)))
        limit = scene_limits.SceneLimit("root.usda", max_triangles=2)
        with pytest.raises(ValueError, match="expands to more nodes or geometry than this function converts"):
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


@pytest.mark.unit
class TestVertexOnlyOff:
    """An OFF file with vertices and no faces loads as a faceless mesh; it is a point cloud. It keeps
    its points in the point-capable targets and is refused where triangles are required. OFF output is
    refused for it: a point-cloud OFF does not round trip as one (it reloads as a faceless mesh that
    trimesh's own exporters write empty)."""

    def _cloud(self, tmp_path):
        rng = np.random.default_rng(1)
        text = "OFF\n30 0 0\n" + "\n".join(" ".join(repr(float(v)) for v in row) for row in rng.random((30, 3))) + "\n"
        path = tmp_path / "src" / "cloud.off"
        path.parent.mkdir()
        path.write_text(text)
        return str(path)

    @pytest.mark.parametrize("target", [".ply", ".glb", ".gltf", ".obj", ".usda", ".usdc", ".usdz"])
    def test_the_points_are_kept(self, tmp_path, target):
        out = helpers.convert(tmp_path, self._cloud(tmp_path), target, run_dir="r" + target)
        if target in (".usda", ".usdc", ".usdz"):
            scene = usd_io.read_usd(out, os.path.dirname(out))[0]
            assert sum(len(g.vertices) for g in scene.geometry.values()) == 30
        else:
            loaded = trimesh.load(out)
            parts = list(loaded.geometry.values()) if hasattr(loaded, "geometry") else [loaded]
            assert sum(len(p.vertices) for p in parts) == 30

    @pytest.mark.parametrize("target", [".stl", ".off", ".dae", ".3mf"])
    def test_a_triangle_only_target_is_refused(self, tmp_path, target):
        with pytest.raises(ValueError, match="contains no triangles"):
            helpers.convert(tmp_path, self._cloud(tmp_path), target, run_dir="r" + target)


@pytest.mark.unit
class TestUsdzPackaging:
    def test_the_arkit_package_rules_pass_for_the_written_usdz(self, tmp_path):
        from pxr import UsdUtils
        if not hasattr(UsdUtils, "CreateNewARKitUsdzPackage"):
            pytest.skip("usd-core has no CreateNewARKitUsdzPackage")
        export = tmp_path / "export"
        export.mkdir()
        layer = str(export / "scene.usdc")
        usd_io.write_usd(_scene_with_everything(), layer, 1.0, "y", ".usdc")
        arkit = str(tmp_path / "arkit.usdz")
        assert UsdUtils.CreateNewARKitUsdzPackage(Sdf.AssetPath(layer), arkit)
        with zipfile.ZipFile(arkit) as archive:
            assert any(name.endswith(".png") for name in archive.namelist())

    def test_the_usdz_output_is_a_single_file_holding_its_textures(self, tmp_path):
        out = helpers.convert(tmp_path, helpers.write_usd_fixture(str(tmp_path), "m.usdc", helpers.textured_cube()), ".usdz")
        assert os.listdir(os.path.dirname(out)) == [os.path.basename(out)]
        with zipfile.ZipFile(out) as archive:
            assert any(name.endswith(".png") for name in archive.namelist())
            assert archive.testzip() is None



def _two_level_scene(directory):
    """root.usda sublayers parts/wheel.usda and references shared/body.usda; wheel.usda names a texture."""
    (directory / "parts").mkdir()
    (directory / "shared").mkdir()
    (directory / "parts" / "tex").mkdir()
    from PIL import Image
    Image.new("RGB", (4, 4), (200, 10, 10)).save(directory / "parts" / "tex" / "red.png")
    _write_layer(directory / "parts" / "wheel.usda", '#usda 1.0\n(\n    metersPerUnit = 0.01\n)\n' + _MESH +
                 'def Scope "S" {\n    asset t = @./tex/red.png@\n}\n')
    _write_layer(directory / "shared" / "body.usda", "#usda 1.0\n" + _MESH.replace('"M"', '"B"'))
    _write_layer(directory / "root.usda", '#usda 1.0\n(\n    subLayers = [@./parts/wheel.usda@]\n)\n'
                 'def Xform "Body" (references = @./shared/body.usda@) {}\n')


@pytest.mark.unit
class TestReferenceHandling:
    def test_convert_linked_writes_each_layer_leaves_first_and_relinks(self, tmp_path):
        directory = tmp_path / "input"
        directory.mkdir()
        _two_level_scene(directory)
        out = tmp_path / "export"
        out.mkdir()
        written = usd_io.convert_linked(str(directory / "root.usda"), str(directory), str(out / "root.usdc"), ".usdc")
        relative = sorted(os.path.relpath(p, out) for p in written)
        assert relative == ["parts/tex/red.png", "parts/wheel.usdc", "root.usdc", "shared/body.usdc"]
        root_text = Sdf.Layer.FindOrOpen(str(out / "root.usdc")).ExportToString()
        assert "@./parts/wheel.usdc@" in root_text and "@./shared/body.usdc@" in root_text
        assert ".usda@" not in root_text
        wheel = Sdf.Layer.FindOrOpen(str(out / "parts" / "wheel.usdc"))
        assert wheel.GetFileFormat().formatId == "usdc"
        assert "@./tex/red.png@" in wheel.ExportToString()
        stage = Usd.Stage.Open(str(out / "root.usdc"))
        assert stage.GetPrimAtPath("/M").IsA(UsdGeom.Mesh)
        # body.usda has no default prim, so the reference composes nothing; the arc still resolves.
        assert stage.GetPrimAtPath("/Body")
        # Layers are kept as authored, so the output reports no validator error the source does not.
        assert _validator_names(str(out / "root.usdc")) <= _validator_names(str(directory / "root.usda"))

    def test_convert_linked_puts_a_layer_outside_the_selected_folder_under_linked(self, tmp_path):
        directory = tmp_path / "input"
        (directory / "scenes").mkdir(parents=True)
        _write_layer(directory / "lib.usda", "#usda 1.0\n" + _MESH)
        _write_layer(directory / "scenes" / "s.usda", '#usda 1.0\n(\n    subLayers = [@../lib.usda@]\n)\n')
        out = tmp_path / "export"
        out.mkdir()
        usd_io.convert_linked(str(directory / "scenes" / "s.usda"), str(directory), str(out / "s.usda"), ".usda")
        assert (out / "_linked" / "lib.usda").is_file()
        assert "@./_linked/lib.usda@" in (out / "s.usda").read_text()
        assert Usd.Stage.Open(str(out / "s.usda")).GetPrimAtPath("/M")

    def test_convert_linked_refuses_a_reference_cycle(self, tmp_path):
        directory = tmp_path / "input"
        directory.mkdir()
        _write_layer(directory / "a.usda", '#usda 1.0\ndef "A" (references = @./b.usda@) {}\n' + _MESH)
        _write_layer(directory / "b.usda", '#usda 1.0\ndef "B" (references = @./a.usda@) {}\n')
        out = tmp_path / "export"
        out.mkdir()
        with pytest.raises(ValueError, match="cycle: a.usda -> b.usda -> a.usda"):
            usd_io.convert_linked(str(directory / "a.usda"), str(directory), str(out / "a.usdc"), ".usdc")

    def test_convert_linked_refuses_two_sources_with_one_converted_name(self, tmp_path):
        directory = tmp_path / "input"
        directory.mkdir()
        _write_layer(directory / "p.usda", "#usda 1.0\n" + _MESH)
        Sdf.Layer.FindOrOpen(str(directory / "p.usda")).Export(str(directory / "p.usd"))
        _write_layer(directory / "r.usda", '#usda 1.0\n(\n    subLayers = [@./p.usda@, @./p.usd@]\n)\n')
        out = tmp_path / "export"
        out.mkdir()
        with pytest.raises(ValueError, match="to the same file"):
            usd_io.convert_linked(str(directory / "r.usda"), str(directory), str(out / "r.usdc"), ".usdc")

    def test_package_keeps_the_layers_and_holds_the_closure(self, tmp_path):
        directory = tmp_path / "input"
        directory.mkdir()
        _two_level_scene(directory)
        out = tmp_path / "export"
        out.mkdir()
        usd_io.confine_dependencies(str(directory / "root.usda"), str(directory))
        usd_io.package_usdz(str(directory / "root.usda"), str(directory), str(out / "root.usdz"))
        with zipfile.ZipFile(out / "root.usdz") as archive:
            names = archive.namelist()
        assert any(n.endswith("wheel.usda") for n in names) and any(n.endswith("body.usda") for n in names)
        assert any(n.endswith("red.png") for n in names)
        source = _validator_names(str(directory / "root.usda"))
        assert _validator_names(str(out / "root.usdz")) <= source | {"PackageEncapsulationValidator"}


@pytest.mark.unit
class TestFbxUnit:
    def test_the_ascii_unit_scale_factor_is_centimetres_per_unit(self, tmp_path):
        import fbx_fixture
        path = tmp_path / "a.fbx"
        path.write_bytes(fbx_fixture.box_fbx(unit_scale_cm=100.0))
        assert model_formats.fbx_meters_per_unit(str(path)) == pytest.approx(1.0)

    def test_a_binary_property_record_is_read(self, tmp_path):
        import struct

        def field(text):
            return b"S" + struct.pack("<I", len(text)) + text
        record = field(b"UnitScaleFactor") + field(b"double") + field(b"Number") + field(b"") + b"D" + struct.pack("<d", 2.54)
        path = tmp_path / "b.fbx"
        path.write_bytes(b"Kaydara FBX Binary  \x00\x1a\x00" + b"\x00" * 20 + record)
        assert model_formats.fbx_meters_per_unit(str(path)) == pytest.approx(0.0254)

    def test_no_or_a_bad_unit_is_the_fbx_default_centimetre(self, tmp_path):
        path = tmp_path / "c.fbx"
        path.write_bytes(b"; FBX 7.4.0 project file\n")
        assert model_formats.fbx_meters_per_unit(str(path)) == pytest.approx(0.01)
        path.write_bytes(b'P: "UnitScaleFactor", "double", "Number", "",-5\n')
        assert model_formats.fbx_meters_per_unit(str(path)) == pytest.approx(0.01)

    def test_without_the_assimp_tool_an_fbx_fails_saying_so(self, tmp_path, monkeypatch):
        import fbx_fixture
        monkeypatch.setattr(model_formats, "ASSIMP_BIN", str(tmp_path / "missing"))
        (tmp_path / "input").mkdir()
        (tmp_path / "input" / "a.fbx").write_bytes(fbx_fixture.box_fbx())
        job = {"input_file": str(tmp_path / "input" / "a.fbx"), "input_dir": str(tmp_path / "input"),
               "input_relative_path": "a.fbx", "by_reference": {}, "input_name": "a.fbx"}
        with pytest.raises(RuntimeError, match="Assimp tool is not in this image"):
            model_formats.load_model(job, trimesh)


@pytest.mark.unit
class TestGeometryAnOutputCannotHold:
    """A STEP file's edges and annotations load as curves (Path3D) and points beside its solids; OBJ,
    3MF and the other exporters failed on them (found converting NIST STEP test files)."""

    def _scene(self):
        scene = trimesh.Scene()
        scene.add_geometry(trimesh.creation.box(), geom_name="solid")
        scene.add_geometry(trimesh.load_path(np.array([[0, 0, 0], [1, 1, 1.0]])), geom_name="edge")
        scene.add_geometry(trimesh.PointCloud(np.random.default_rng(2).random((5, 3))), geom_name="pmi")
        return scene

    @pytest.mark.parametrize("output,kept", [
        (".obj", {"solid", "pmi"}), (".ply", {"solid", "pmi"}), (".usdc", {"solid", "pmi"}),
        (".3mf", {"solid"}), (".stl", {"solid"}), (".dae", {"solid"}), (".off", {"solid"}),
        (".glb", {"solid", "edge", "pmi"}),
    ])
    def test_only_what_the_output_holds_is_kept_and_the_export_succeeds(self, tmp_path, output, kept):
        scene = self._scene()
        model_formats.prune_for_output(scene, output)
        assert set(scene.geometry) == kept
        export = tmp_path / "export"
        export.mkdir()
        job = {"output_filetype": output, "output_file": str(export / f"o{output}"), "input_name": "s.step"}
        import units_axes
        model_formats.export_model(self._scene(), units_axes.plan(units_axes.Options(), output, 1.0, "z"), job)
        assert os.path.getsize(export / f"o{output}") > 0


def _component_set(directory):
    """An OpenChessSet-style set: set.usda references two component assets. Each component layer
    names itself in its ``assetInfo`` identifier (the ASWF/Pixar component-model layout), carries
    ``kind = component`` and loads its geometry layer, which names a texture, through a payload."""
    from PIL import Image
    for piece in ("Bishop", "Rook"):
        folder = directory / "assets" / piece
        folder.mkdir(parents=True)
        Image.new("RGB", (4, 4), (50, 60, 70)).save(folder / f"{piece}_BaseColor.png")
        _write_layer(folder / f"{piece}_geom.usda", "#usda 1.0\n(\n    defaultPrim = \"Geom\"\n)\n"
                     + _MESH.replace('"M"', '"Geom"').replace("}\n", f'    asset tex = @./{piece}_BaseColor.png@\n}}\n', 1))
        _write_layer(folder / f"{piece}.usd",
                     f'#usda 1.0\n(\n    defaultPrim = "{piece}"\n)\n'
                     f'def "{piece}" (\n    assetInfo = {{\n        asset identifier = @./{piece}.usd@\n'
                     f'        string name = "{piece}"\n    }}\n    kind = "component"\n'
                     f'    payload = @./{piece}_geom.usda@\n) {{\n}}\n')
    _write_layer(directory / "set.usda", '#usda 1.0\n(\n    defaultPrim = "Set"\n)\ndef Xform "Set" {\n'
                 '    def "Bishop" (references = @./assets/Bishop/Bishop.usd@) {}\n'
                 '    def "Rook" (references = @./assets/Rook/Rook.usd@) {}\n}\n')


@pytest.mark.unit
class TestComponentAssets:
    """The assetInfo identifier is metadata, not a composition arc (in-development defect D4): it
    must not make a component layer its own dependency."""

    def test_the_dependency_graph_has_no_self_edge(self, tmp_path):
        directory = tmp_path / "input"
        directory.mkdir()
        _component_set(directory)
        graph = usd_io.dependency_graph(str(directory / "set.usda"), str(directory))
        for layer, targets in graph.items():
            assert layer not in targets, layer
        bishop = os.path.realpath(directory / "assets" / "Bishop" / "Bishop.usd")
        assert graph[bishop] == [os.path.realpath(directory / "assets" / "Bishop" / "Bishop_geom.usda")]

    @pytest.mark.parametrize("output", [".usdc", ".usda"])
    def test_convert_linked_converts_a_component_set(self, tmp_path, output):
        directory = tmp_path / "input"
        directory.mkdir()
        _component_set(directory)
        out = tmp_path / "export"
        out.mkdir()
        written = usd_io.convert_linked(str(directory / "set.usda"), str(directory), str(out / f"set{output}"), output)
        relative = sorted(os.path.relpath(p, out) for p in written)
        assert f"assets/Bishop/Bishop{output}" in relative and f"assets/Rook/Rook_geom{output}" in relative
        assert "assets/Bishop/Bishop_BaseColor.png" in relative
        bishop = Sdf.Layer.FindOrOpen(str(out / "assets" / "Bishop" / f"Bishop{output}")).ExportToString()
        # The identifier names the converted layer itself.
        assert f"asset identifier = @./Bishop{output}@" in bishop
        stage = Usd.Stage.Open(str(out / f"set{output}"))
        assert stage.GetPrimAtPath("/Set/Bishop").IsA(UsdGeom.Mesh)
        assert stage.GetPrimAtPath("/Set/Rook").IsA(UsdGeom.Mesh)

    def test_a_layer_that_sublayers_itself_is_not_a_cycle_of_its_own(self, tmp_path):
        directory = tmp_path / "input"
        directory.mkdir()
        _write_layer(directory / "a.usda", '#usda 1.0\n(\n    subLayers = [@./a.usda@]\n)\n' + _MESH)
        out = tmp_path / "export"
        out.mkdir()
        usd_io.convert_linked(str(directory / "a.usda"), str(directory), str(out / "a.usdc"), ".usdc")
        assert (out / "a.usdc").is_file()


@pytest.mark.unit
class TestUnreadableCollada:
    """A COLLADA document pycollada cannot read is reported as unreadable, with the parser's reason,
    not as empty (in-development defect D5; seen on 3ds Max exports with a malformed effect colour)."""

    def _broken(self, tmp_path):
        text = trimesh.creation.box().export(file_type="dae").decode()
        text = text.replace("<color>0.0 0.0 0.0 1.0</color>", "<color>0.0 not-a-number</color>", 1)
        (tmp_path / "broken.dae").write_text(text)
        return {"input_file": str(tmp_path / "broken.dae"), "input_dir": str(tmp_path),
                "input_relative_path": "broken.dae", "by_reference": {}, "input_name": "broken.dae"}

    def test_the_message_says_it_could_not_be_read_and_why(self, tmp_path):
        with pytest.raises(ValueError, match=r"broken\.dae could not be read as COLLADA: Dae\w+Error"):
            model_formats.load_model(self._broken(tmp_path), trimesh)

    def test_an_empty_but_valid_document_is_still_reported_as_empty(self, tmp_path):
        import conversion_worker
        text = ('<?xml version="1.0"?><COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" '
                'version="1.4.1"><asset><up_axis>Y_UP</up_axis></asset><library_visual_scenes>'
                '<visual_scene id="s"/></library_visual_scenes><scene><instance_visual_scene url="#s"/></scene>'
                '</COLLADA>')
        (tmp_path / "empty.dae").write_text(text)
        job = {"input_file": str(tmp_path / "empty.dae"), "input_dir": str(tmp_path),
               "input_relative_path": "empty.dae", "by_reference": {}, "input_name": "empty.dae"}
        loaded, _, _ = model_formats.load_model(job, trimesh)
        with pytest.raises(ValueError, match="contains no geometry"):
            conversion_worker.check_exportable(loaded, ".glb", "empty.dae")


@pytest.mark.unit
class TestImplicitPrims:
    """UsdGeom Cube, Sphere, Cylinder, Cone, Capsule and Plane are tessellated (in-development defect
    D6: they were dropped, so a scene made of them reported no geometry)."""

    @pytest.mark.parametrize("prim,extents", [
        ('def Cube "C" { double size = 3 }', (3, 3, 3)),
        ('def Cube "C" {}', (2, 2, 2)),
        ('def Sphere "S" { double radius = 2 }', (4, 4, 4)),
        ('def Cylinder "Y" { double radius = 1\n double height = 5\n uniform token axis = "Y" }', (2, 5, 2)),
        ('def Cone "K" { double radius = 1\n double height = 4\n uniform token axis = "X" }', (4, 2, 2)),
        ('def Capsule "P" { double radius = 0.5\n double height = 2 }', (1, 1, 3)),
        ('def Plane "L" { double width = 4\n double length = 6\n uniform token axis = "Y" }', (4, 0, 6)),
    ])
    def test_each_is_tessellated_at_its_authored_size(self, tmp_path, prim, extents):
        directory = tmp_path / "input"
        directory.mkdir()
        _write_layer(directory / "s.usda", "#usda 1.0\n" + prim.replace(" }", "\n}").replace("{ ", "{\n") + "\n")
        scene, _, _ = usd_io.read_usd(str(directory / "s.usda"), str(directory))
        assert len(scene.geometry) == 1
        np.testing.assert_allclose(scene.extents, extents, atol=0.05)

    def test_the_transform_and_display_colour_apply(self, tmp_path):
        directory = tmp_path / "input"
        directory.mkdir()
        _write_layer(directory / "s.usda", '#usda 1.0\ndef Xform "X" (\n) {\n    double3 xformOp:translate = (10, 0, 0)\n'
                     '    uniform token[] xformOpOrder = ["xformOp:translate"]\n'
                     '    def Cube "C" {\n        color3f[] primvars:displayColor = [(1, 0, 0)]\n    }\n}\n')
        scene, _, _ = usd_io.read_usd(str(directory / "s.usda"), str(directory))
        np.testing.assert_allclose(scene.bounds, [[9, -1, -1], [11, 1, 1]], atol=1e-6)
        mesh = next(iter(scene.geometry.values()))
        assert list(mesh.visual.material.baseColorFactor[:3]) == [255, 0, 0]

    def test_a_scene_of_implicit_prims_converts(self, tmp_path):
        directory = tmp_path / "src"
        directory.mkdir()
        _write_layer(directory / "prims.usda", '#usda 1.0\ndef Cube "C" {\n}\ndef Sphere "S" {\n    double radius = 0.5\n}\n')
        out = helpers.convert(tmp_path, str(directory / "prims.usda"), ".glb")
        assert len(trimesh.load(out).geometry) == 2

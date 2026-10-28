#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Two defects that are invisible to every numeric check the pipeline already makes.

The mesh can be geometrically perfect and still present wrong. A Z-up mesh written into a glTF
container renders lying on its side, because glTF declares Y as up -- the vertex data is identical to
the OBJ and STL beside it, so no gate, face count or volume figure differs. A baked atlas full of
unmapped black texels renders as holes peppered across the surface while the geometry underneath is
untouched. Both were found by looking at a preview, so both are asserted here.
"""

import numpy as np
import pytest
import trimesh
from PIL import Image

import export
import texturing


def _tall_mesh():
    """A box that is unambiguously taller than it is wide, so an axis swap is measurable."""
    return trimesh.creation.box(extents=(0.2, 0.3, 1.0))


class TestGltfOrientation:
    def test_the_gltf_copy_puts_the_long_axis_on_y(self):
        """glTF's up axis is Y, so the mesh's height has to land there."""
        rotated = export.gltf_oriented(_tall_mesh())
        assert int(np.argmax(rotated.extents)) == 1, f"extents {rotated.extents}"

    def test_the_source_mesh_is_left_on_z(self):
        """OBJ and STL are written from the original, so rotating must not mutate it."""
        mesh = _tall_mesh()
        export.gltf_oriented(mesh)
        assert int(np.argmax(mesh.extents)) == 2, "the caller's mesh was rotated in place"

    def test_the_rotation_preserves_the_shape(self):
        rotated = export.gltf_oriented(_tall_mesh())
        assert sorted(round(e, 6) for e in rotated.extents) == [0.2, 0.3, 1.0]
        assert rotated.volume == pytest.approx(_tall_mesh().volume)

    def test_gltf_containers_are_written_upright_and_the_others_are_not(self, tmp_path):
        """End to end through the exporter: the GLB comes back Y-up, the STL stays Z-up."""
        export.write_formats(_tall_mesh(), str(tmp_path), "m", ("glb", "stl"))
        glb = trimesh.load(str(tmp_path / "m.glb"), force="mesh")
        stl = trimesh.load(str(tmp_path / "m.stl"), force="mesh")
        assert int(np.argmax(glb.extents)) == 1, f"GLB extents {glb.extents}"
        assert int(np.argmax(stl.extents)) == 2, f"STL extents {stl.extents}"


class TestAtlasFill:
    def _speckled(self, size=64, black_every=3):
        """An atlas whose mapped colour is interrupted by unmapped black texels."""
        a = np.full((size, size, 3), (200, 100, 50), dtype=np.uint8)
        a[::black_every, ::black_every] = (0, 0, 0)
        return Image.fromarray(a)

    def test_unmapped_texels_are_filled(self):
        before = np.array(self._speckled())
        after = np.array(texturing.fill_unmapped_texels(self._speckled()))
        assert (before.sum(axis=-1) == 0).any(), "the fixture must contain unmapped texels"
        assert not (after.sum(axis=-1) == 0).any(), "unmapped texels survived the fill"

    def test_the_mapped_colours_are_not_altered(self):
        """The fill must not repaint colour the bake actually produced."""
        source = self._speckled()
        before = np.array(source)
        after = np.array(texturing.fill_unmapped_texels(source))
        mapped = before.sum(axis=-1) > 0
        assert np.array_equal(after[mapped], before[mapped])

    def test_a_fully_mapped_atlas_is_returned_unchanged(self):
        solid = Image.fromarray(np.full((16, 16, 3), (7, 8, 9), dtype=np.uint8))
        assert np.array_equal(np.array(texturing.fill_unmapped_texels(solid)), np.array(solid))

    def test_transparency_marks_unmapped_texels_too(self):
        """The bake writes [0,0,0,0], so alpha is the marker before it is dropped."""
        a = np.zeros((16, 16, 4), dtype=np.uint8)
        a[:8, :, :3] = (10, 20, 30)
        a[:8, :, 3] = 255
        filled = np.array(texturing.fill_unmapped_texels(Image.fromarray(a, "RGBA")))
        assert filled.shape[-1] == 3
        assert tuple(filled[12, 4]) == (10, 20, 30), "a transparent texel was not filled"

    def test_a_fully_unmapped_atlas_does_not_raise(self):
        blank = Image.fromarray(np.zeros((8, 8, 3), dtype=np.uint8))
        assert np.array(texturing.fill_unmapped_texels(blank)).sum() == 0

    def test_the_bound_texture_is_filled_before_it_reaches_the_mesh(self, tmp_path):
        """The binding path is where the atlas enters the pipeline, so the fill belongs there."""
        path = tmp_path / "texture.png"
        self._speckled().save(path)
        mesh = trimesh.creation.box()
        mesh.visual = trimesh.visual.TextureVisuals(
            uv=np.zeros((len(mesh.vertices), 2)), material=None)
        bound = texturing.attach_generated_texture(mesh, [str(path)])
        image = np.array(texturing.material_image(bound.visual.material).convert("RGB"))
        assert not (image.sum(axis=-1) == 0).any(), "the bound atlas still carries unmapped texels"


class TestUpAxisNormalization:
    """Orientation is per-model, and getting it wrong is invisible to every other check.

    One model reconstructs into a Z-up space; another applies its own transform on the way out and
    hands back a Y-up mesh. A single pipeline-wide rotation therefore turns one of them the wrong way,
    and the result passes the print gate with the same volume, face count and file size as a correct
    one -- it simply lies on its side in the viewer and in a slicer.
    """

    def test_a_z_up_mesh_is_left_alone(self):
        mesh = _tall_mesh()
        assert int(np.argmax(export.normalize_up_axis(mesh, "z").extents)) == 2

    def test_a_y_up_mesh_is_rotated_into_z_up(self):
        """A Y-up model's height has to end up on Z, which is what the rest of the pipeline assumes."""
        y_up = export.gltf_oriented(_tall_mesh())  # a Y-up mesh, by construction
        assert int(np.argmax(y_up.extents)) == 1, "the fixture is not Y-up"
        assert int(np.argmax(export.normalize_up_axis(y_up, "y").extents)) == 2

    def test_normalizing_then_exporting_gives_the_same_orientation_for_either_model(self, tmp_path):
        """The point of normalising: both models' GLBs come out upright and both STLs come out Z-up."""
        z_up = _tall_mesh()
        y_up = export.gltf_oriented(_tall_mesh())
        for label, mesh, axis in (("z-up model", z_up, "z"), ("y-up model", y_up, "y")):
            canonical = export.normalize_up_axis(mesh, axis)
            out = tmp_path / label.replace(" ", "_")
            out.mkdir()
            export.write_formats(canonical, str(out), "m", ("glb", "stl"))
            glb = trimesh.load(str(out / "m.glb"), force="mesh")
            stl = trimesh.load(str(out / "m.stl"), force="mesh")
            assert int(np.argmax(glb.extents)) == 1, f"{label}: GLB not Y-up ({glb.extents})"
            assert int(np.argmax(stl.extents)) == 2, f"{label}: STL not Z-up ({stl.extents})"

    def test_an_unknown_axis_is_rejected(self):
        with pytest.raises(export.ExportError, match="up axis"):
            export.normalize_up_axis(_tall_mesh(), "w")

    def test_the_normalization_preserves_the_shape(self):
        rotated = export.normalize_up_axis(export.gltf_oriented(_tall_mesh()), "y")
        assert sorted(round(e, 6) for e in rotated.extents) == [0.2, 0.3, 1.0]

    def test_every_adapter_declares_a_usable_up_axis(self):
        from models.sam3d import Sam3dAdapter
        from models.stable3dgen import Stable3DGenAdapter
        from models.step1x3d import Step1X3dAdapter
        from models.trellis2 import Trellis2Adapter
        from models.triposr import TripoSRAdapter

        for adapter in (Sam3dAdapter, Stable3DGenAdapter, Trellis2Adapter, TripoSRAdapter,
                        Step1X3dAdapter):
            assert adapter.OUTPUT_UP_AXIS in export.UP_AXES, (
                f"{adapter.KEY} declares an unusable output up axis")

    def test_stable3dgen_declares_y_up_and_triposr_declares_z_up(self):
        """The two measured on a live run: their GLBs came out inverted from each other."""
        from models.stable3dgen import Stable3DGenAdapter
        from models.triposr import TripoSRAdapter

        assert Stable3DGenAdapter.OUTPUT_UP_AXIS == "y"
        assert TripoSRAdapter.OUTPUT_UP_AXIS == "z"

    def test_the_two_geometry_only_models_declare_the_azimuth_each_was_measured_at(self):
        """The photograph's view differs BETWEEN models, which is the whole reason it is a per-model
        constant rather than one pipeline setting.

        Both were pinned and compared rather than taken from the silhouette search alone. Stable3DGen
        settles at 0 on two subjects, decisively on the mug. Step1X-3D settles at 180: its search prefers
        180 but with a margin of only 0.031 over 0, so it was decided by pinning each and measuring what
        the projection reaches -- IoU 0.868 against 0.837, 41.7% of vertices sampled directly against
        36.3%, and a render that puts the face on the mesh's front at 180 and on its back at 0.

        Collapsing these to one shared value would silently paint one model's subject back-to-front, and
        nothing else in the pipeline would report it: the print gate, the volume and the face count are
        all orientation-invariant.
        """
        from models.stable3dgen import Stable3DGenAdapter
        from models.step1x3d import Step1X3dAdapter

        assert Stable3DGenAdapter.INPUT_VIEW_AZIMUTH == 0.0
        assert Step1X3dAdapter.INPUT_VIEW_AZIMUTH == 180.0
        assert Stable3DGenAdapter.INPUT_VIEW_AZIMUTH != Step1X3dAdapter.INPUT_VIEW_AZIMUTH

    def test_a_model_that_generates_its_own_colour_declares_no_azimuth(self):
        """The constant exists for projection, so a model that needs no projection leaves it unset --
        and an unset value must stay None rather than defaulting to 0, which would look like a
        measurement."""
        from models.base import ModelAdapter
        from models.sam3d import Sam3dAdapter
        from models.trellis2 import Trellis2Adapter
        from models.triposr import TripoSRAdapter

        assert ModelAdapter.INPUT_VIEW_AZIMUTH is None
        for adapter in (Sam3dAdapter, Trellis2Adapter, TripoSRAdapter):
            assert adapter.INPUT_VIEW_AZIMUTH is None, (
                f"{adapter.KEY} generates its own colour and needs no declared photograph view")

    def test_sam3d_declares_y_up_because_upstream_rotates_on_export(self):
        """``to_glb`` rotates the vertices from its Z-up reconstruction space to Y-up before building
        the mesh, and does so for every output form rather than only the textured one."""
        from models.sam3d import Sam3dAdapter

        assert Sam3dAdapter.OUTPUT_UP_AXIS == "y"


class TestRembgWeightsAreBakedIn:
    """A per-run weight download is the largest single cost the benchmark would otherwise measure."""

    def test_every_image_points_rembg_at_a_baked_in_cache(self):
        import os
        container = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in ("Dockerfile.sam3d", "Dockerfile.stable3dgen", "Dockerfile.trellis2",
                     "Dockerfile.triposr", "Dockerfile.step1x3d"):
            with open(os.path.join(container, name), encoding="utf-8") as fh:
                body = fh.read()
            assert "U2NET_HOME" in body, f"{name} does not set the rembg cache location"
            assert "u2net.onnx" in body, f"{name} does not stage the rembg weights"


class TestImageCleanupBypass:
    """A clean input must be able to skip the cleanup stage outright.

    A production input is often already segmented, so this is a common path rather than an
    edge case: skipping removes a stage's latency and cost instead of merely doing nothing. It resolves
    to the same "no segmentation" value the backend list already carries, so framing -- which cannot
    locate a subject without a mask -- is bypassed by the same decision rather than a second one.
    """

    def _run_settings(self, cleanup):
        """The settings map after the entry module applies the cleanup switch."""
        import importlib.util
        import os

        container = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        spec = importlib.util.spec_from_file_location(
            "imageto3d_main_for_cleanup", os.path.join(container, "__main__.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        allowed = module.load_allowed_settings()
        return module.resolve_settings(allowed, {"IMAGE_CLEANUP": cleanup}, "triposr")

    def test_the_switch_is_part_of_the_allow_list_and_defaults_to_auto(self):
        assert self._run_settings("")["IMAGE_CLEANUP"] == "auto"

    def test_skip_is_accepted_as_a_value(self):
        assert self._run_settings("skip")["IMAGE_CLEANUP"] == "skip"

    def test_every_template_offers_the_switch_as_an_enum(self):
        import glob
        import json
        import os

        templates = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "vamsSchema", "templates", "image-to-3d-*.json")
        found = glob.glob(templates)
        assert len(found) >= 4, f"template scan found {found}"
        for path in found:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
            tag = next((t for t in doc["tagSchema"] if t["tagKey"] == "IMAGE_CLEANUP"), None)
            assert tag, f"{os.path.basename(path)} does not offer IMAGE_CLEANUP"
            assert tag["type"] == "enum" and set(tag["enumValues"]) == {"auto", "skip"}
            assert '"IMAGE_CLEANUP": "{{IMAGE_CLEANUP}}"' in doc["configBody"]


@pytest.mark.unit
class TestShapeDescriptors:
    """Coarse measurements of the shipped mesh, for comparing models on one subject. Verified against the
    four real baselines: fill 0.217 / 0.435 / 0.436 / 0.936, where the last is a solid billet."""

    def test_a_solid_fills_its_hull_and_a_hollow_vessel_does_not(self):
        import trimesh

        solid = trimesh.creation.box(extents=(2.0, 2.0, 2.0))
        outer = trimesh.creation.cylinder(radius=20.0, height=40.0, sections=48)
        inner = trimesh.creation.cylinder(radius=17.0, height=40.0, sections=48)
        inner.apply_translation((0, 0, 4.0))
        hollow = trimesh.boolean.difference([outer, inner])

        assert export.shape_descriptors(solid)["fillRatio"] > 0.95
        assert export.shape_descriptors(hollow)["fillRatio"] < 0.5

    def test_a_protruding_feature_moves_the_footprint_away_from_round(self):
        import trimesh

        round_body = trimesh.creation.cylinder(radius=10.0, height=20.0, sections=48)
        handle = trimesh.creation.box(extents=(6.0, 2.0, 8.0))
        handle.apply_translation((12.0, 0, 0))
        with_handle = trimesh.util.concatenate([round_body, handle])

        assert export.shape_descriptors(round_body)["footprintAspect"] == pytest.approx(1.0, abs=0.02)
        assert export.shape_descriptors(with_handle)["footprintAspect"] > 1.1

    def test_proportion_is_height_against_the_narrower_ground_axis(self):
        import trimesh

        tall = trimesh.creation.cylinder(radius=5.0, height=40.0, sections=32)
        squat = trimesh.creation.cylinder(radius=20.0, height=10.0, sections=32)

        assert export.shape_descriptors(tall)["heightOverWidth"] > 3.0
        assert export.shape_descriptors(squat)["heightOverWidth"] < 0.4

    def test_a_closed_surface_with_no_tunnels_reports_genus_zero(self):
        import trimesh

        assert export.shape_descriptors(trimesh.creation.icosphere(subdivisions=3))["genus"] == 0

    def test_a_tunnel_is_counted(self):
        """The measurement the rest of the report cannot make. A torus is watertight, manifold and one
        body, so every print-gate criterion passes on it while it plainly has a hole through it."""
        import trimesh

        torus = trimesh.creation.torus(major_radius=10.0, minor_radius=3.0)

        assert torus.is_watertight
        assert export.shape_descriptors(torus)["genus"] == 1

    def test_separate_closed_shells_are_not_mistaken_for_tunnels(self):
        """Euler characteristic is 2C - 2g over C components, so the component count has to come out
        before the genus does. Two spheres are genus 0 twice over, not a tunnel."""
        import trimesh

        first = trimesh.creation.icosphere(subdivisions=3)
        second = trimesh.creation.icosphere(subdivisions=3)
        second.apply_translation((5.0, 0.0, 0.0))
        pair = trimesh.util.concatenate([first, second])

        assert export.surface_genus(pair) == 0

    def test_a_uv_seam_does_not_read_as_an_open_surface(self):
        """The reason this is measured on a welded copy. A textured mesh carries a separate vertex per face
        corner wherever its atlas has a seam, and over the exported indices that reads as open -- a re-baked
        mesh whose genus is 7 reported -891 unwelded. The geometry is identical either way."""
        import numpy as np
        import trimesh
        from PIL import Image

        torus = trimesh.creation.torus(major_radius=10.0, minor_radius=3.0)
        # Split every vertex, exactly as an unwrap does at a chart seam: one vertex per face corner, the
        # same positions, and a uv field over them.
        faces = np.asarray(torus.faces)
        split = trimesh.Trimesh(vertices=np.asarray(torus.vertices)[faces.ravel()],
                                faces=np.arange(len(faces) * 3).reshape(-1, 3), process=False)
        split.visual = trimesh.visual.TextureVisuals(
            uv=np.zeros((len(split.vertices), 2)), image=Image.new("RGB", (4, 4), (10, 20, 30)))

        assert not split.is_watertight, "the split mesh must read as open before welding, or this is moot"
        assert export.surface_genus(split) == 1

    def test_an_open_surface_reports_no_genus_rather_than_a_wrong_one(self):
        """Genus is undefined for a surface with a boundary, so the field is absent rather than guessed."""
        import numpy as np
        import trimesh

        sheet = trimesh.Trimesh(
            vertices=np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]]),
            faces=np.array([[0, 1, 2], [1, 3, 2]]), process=False)

        assert export.surface_genus(sheet) is None
        assert "genus" not in export.shape_descriptors(sheet)

    def test_an_unmeasurable_mesh_reports_nothing_rather_than_raising(self):
        class Broken:
            @property
            def extents(self):
                raise RuntimeError("no extents")

        assert export.shape_descriptors(Broken()) == {}

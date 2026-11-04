# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Print features: hanging loops and stand bases, and the join that decides whether colour survives.

The central risk these cover is silent: a boolean union produces a mesh that passes every print-gate
criterion while having discarded the texture. On a pipeline whose primary requirement is full-colour
output that is a worse outcome than a hard failure, because nothing reports it.
"""

import numpy as np
import pytest
import trimesh
from PIL import Image

import printfeatures


def textured_sphere(radius=10.0):
    """A mesh carrying a real texture, standing in for a colour-capable model's output."""
    mesh = trimesh.creation.icosphere(subdivisions=2, radius=radius)
    mesh.visual = trimesh.visual.TextureVisuals(
        uv=np.random.rand(len(mesh.vertices), 2),
        image=Image.new("RGB", (64, 64), (200, 30, 30)))
    return mesh


def bare_sphere(radius=10.0):
    """A geometry-only mesh, standing in for a model that produces no colour."""
    return trimesh.creation.icosphere(subdivisions=2, radius=radius)


class TestJoinResolution:
    def test_textured_mesh_takes_the_colour_preserving_join(self):
        assert printfeatures.resolve_join(textured_sphere(), "auto") == "concat"

    def test_geometry_only_mesh_takes_the_single_solid_join(self):
        assert printfeatures.resolve_join(bare_sphere(), "auto") == "union"

    def test_a_trimesh_placeholder_material_does_not_count_as_colour(self):
        """trimesh substitutes a 2x2 swatch for a mesh with no material.

        Treating it as a real texture would pick the colour-preserving join -- and its overlapping
        shells -- for a mesh that has no colour to preserve.
        """
        mesh = bare_sphere()
        mesh.visual = trimesh.visual.TextureVisuals(
            uv=np.random.rand(len(mesh.vertices), 2),
            image=trimesh.visual.material.empty_material().image)
        assert printfeatures.resolve_join(mesh, "auto") == "union"

    def test_an_explicit_mode_overrides_what_the_mesh_carries(self):
        assert printfeatures.resolve_join(textured_sphere(), "union") == "union"
        assert printfeatures.resolve_join(bare_sphere(), "concat") == "concat"

    def test_an_unknown_mode_is_rejected(self):
        with pytest.raises(printfeatures.PrintFeatureError, match="PRINT_FEATURE_JOIN"):
            printfeatures.resolve_join(bare_sphere(), "weld")


class TestColourSurvival:
    def test_the_concat_join_preserves_the_texture(self):
        """The requirement this whole module has to not break."""
        mesh, report = printfeatures.apply_features(
            textured_sphere(), {"PRINT_FEATURES": "loop"})
        assert report["printFeatureJoin"] == "concat"
        assert mesh.visual.kind == "texture"
        assert mesh.visual.uv is not None and len(mesh.visual.uv) == len(mesh.vertices)
        assert mesh.visual.material.image is not None

    def test_the_added_geometry_carries_its_own_colour(self):
        """Left bare the feature receives uv coordinates into the model's atlas and prints as a
        smear of the subject's colours. Its own swatch has to reach the packed atlas."""
        mesh, _ = printfeatures.apply_features(
            textured_sphere(), {"PRINT_FEATURES": "loop", "PRINT_FEATURE_COLOR": "#0000FF"})
        colours = {c for _, c in mesh.visual.material.image.convert("RGB").getcolors(1 << 20)}
        assert (0, 0, 255) in colours, f"the feature's colour is absent from the atlas: {colours}"
        assert (200, 30, 30) in colours, "the model's own colour was displaced by the feature's"

    def test_the_union_join_is_only_chosen_where_there_is_no_colour_to_lose(self):
        """Documents the measured trade-off: the union does discard the texture, which is why auto
        never selects it for a textured mesh."""
        fused, _ = printfeatures._join(
            textured_sphere(), printfeatures.loop_geometry(textured_sphere()), "union", (0, 0, 0))
        assert getattr(fused.visual, "uv", None) is None, (
            "the union preserved uvs -- if trimesh gained attribute propagation, auto should prefer "
            "it for textured meshes too, since it yields a single solid")


class TestGeometry:
    def test_a_loop_is_added_at_the_top_and_extends_above_the_model(self):
        original = textured_sphere()
        mesh, report = printfeatures.apply_features(original.copy(), {"PRINT_FEATURES": "loop"})
        assert report["printFeatures"] == "loop"
        assert mesh.bounds[1][2] > original.bounds[1][2], "the loop does not rise above the model"

    def test_a_base_is_added_below_and_widens_the_footprint(self):
        original = bare_sphere()
        mesh, report = printfeatures.apply_features(original.copy(), {"PRINT_FEATURES": "base"})
        assert report["printFeatures"] == "base"
        assert mesh.bounds[0][2] < original.bounds[0][2], "the base does not sit below the model"

    def test_both_features_are_added_together(self):
        original = bare_sphere()
        mesh, report = printfeatures.apply_features(original.copy(), {"PRINT_FEATURES": "loop+base"})
        assert report["printFeatures"] == "loop,base"
        assert mesh.bounds[1][2] > original.bounds[1][2]
        assert mesh.bounds[0][2] < original.bounds[0][2]

    def test_a_loop_overlaps_the_model_rather_than_floating_above_it(self):
        """A feature that only touches leaves a coincident face a boolean resolves unpredictably, and
        one that floats is a detached shell. The union collapsing to a single body is the evidence."""
        mesh = bare_sphere()
        fused, mode = printfeatures._join(
            mesh, printfeatures.loop_geometry(mesh), "union", (0, 0, 0))
        assert mode == "union"
        assert fused.body_count == 1, "the loop did not fuse into the model"

    def test_the_loop_hole_is_clear_so_a_ring_can_pass_through(self):
        """The torus is created hole-up, which would hang edge-on; it is rotated so the hole axis is
        horizontal. A genus-1 result is what confirms the hole survived the sizing."""
        loop = printfeatures.loop_geometry(bare_sphere(), diameter_mm=6.0, thickness_mm=2.0)
        assert loop.is_watertight
        assert loop.euler_number == 0, f"the loop is not a torus (euler {loop.euler_number})"
        # Rotated hole-sideways, the loop is thinner across y than it is tall.
        assert loop.extents[1] < loop.extents[2]

    def test_the_requested_diameter_is_the_clear_hole_a_ring_passes_through(self):
        """The dimension that matters is the opening, not the outer span -- a loop specified as 8 mm
        has to admit an 8 mm ring. The outer span is that plus a tube thickness on each side."""
        loop = printfeatures.loop_geometry(bare_sphere(), diameter_mm=8.0, thickness_mm=2.0)
        assert loop.extents[0] == pytest.approx(8.0 + 2 * 2.0, abs=0.2)
        # The hole itself: the outer radius less a full tube thickness, doubled.
        hole = (loop.extents[0] / 2.0 - 2.0) * 2.0
        assert hole == pytest.approx(8.0, abs=0.2)

    def test_dimensions_left_at_zero_scale_to_the_model(self):
        small = printfeatures.loop_geometry(bare_sphere(radius=5.0))
        large = printfeatures.loop_geometry(bare_sphere(radius=50.0))
        assert large.extents[0] > small.extents[0] * 5


class TestReporting:
    def test_the_body_count_is_reported_because_the_gate_cannot_see_it(self):
        """Two disjoint closed shells satisfy every gate criterion, so "passed" means something
        different for a concat join than a union one. The count is what makes that legible."""
        mesh, report = printfeatures.apply_features(textured_sphere(), {"PRINT_FEATURES": "loop"})
        assert report["printFeatureBodies"] == "2"
        assert mesh.is_watertight, "the concatenated result should still satisfy the gate"

    def test_a_union_join_reports_a_single_body(self):
        _, report = printfeatures.apply_features(bare_sphere(), {"PRINT_FEATURES": "loop"})
        assert report["printFeatureJoin"] == "union"
        assert report["printFeatureBodies"] == "1"

    def test_no_features_requested_leaves_the_mesh_and_report_untouched(self):
        original = textured_sphere()
        mesh, report = printfeatures.apply_features(original, {"PRINT_FEATURES": "none"})
        assert report == {}
        assert mesh is original

    def test_an_absent_setting_defaults_to_no_features(self):
        original = textured_sphere()
        mesh, report = printfeatures.apply_features(original, {})
        assert report == {} and mesh is original

    def test_a_blank_setting_defaults_to_no_features(self):
        """An optional enum submits an empty string when the operator leaves it alone."""
        original = textured_sphere()
        mesh, report = printfeatures.apply_features(original, {"PRINT_FEATURES": ""})
        assert report == {} and mesh is original


class TestValidation:
    def test_an_unknown_feature_is_rejected(self):
        with pytest.raises(printfeatures.PrintFeatureError, match="PRINT_FEATURES"):
            printfeatures.apply_features(bare_sphere(), {"PRINT_FEATURES": "handle"})

    def test_a_loop_thicker_than_its_own_diameter_is_rejected(self):
        """It would close the hole a ring has to pass through, producing a solid stub that looks
        attached and cannot be threaded."""
        with pytest.raises(printfeatures.PrintFeatureError, match="close the hole"):
            printfeatures.loop_geometry(bare_sphere(), diameter_mm=3.0, thickness_mm=4.0)

    def test_a_non_numeric_dimension_is_rejected(self):
        with pytest.raises(printfeatures.PrintFeatureError, match="LOOP_DIAMETER_MM"):
            printfeatures.apply_features(
                bare_sphere(), {"PRINT_FEATURES": "loop", "LOOP_DIAMETER_MM": "large"})

    @pytest.mark.parametrize("raw,expected", [
        ("#3C3C3C", (60, 60, 60)), ("3C3C3C", (60, 60, 60)), ("#ff0000", (255, 0, 0))])
    def test_colours_parse_with_or_without_the_hash(self, raw, expected):
        assert printfeatures.parse_color(raw) == expected

    @pytest.mark.parametrize("raw", ["#FFF", "", "#GGGGGG", "red"])
    def test_a_malformed_colour_is_rejected(self, raw):
        with pytest.raises(printfeatures.PrintFeatureError, match="PRINT_FEATURE_COLOR"):
            printfeatures.parse_color(raw)


class TestGateInteraction:
    def test_the_gate_is_re_measured_against_the_finished_mesh(self):
        """The cleanup's verdict describes pre-rescale, pre-feature geometry. Re-measuring keeps the
        verdict about what ships, while the repair provenance is carried over rather than lost."""
        import meshcleanup

        mesh = bare_sphere()
        before = meshcleanup.evaluate_gate(mesh)
        before.backends_applied = ["trimesh", "manifold3d"]
        before.repairs_rejected = ["pymeshfix"]
        before.volume_retained = 0.91

        with_loop, _ = printfeatures.apply_features(mesh, {"PRINT_FEATURES": "loop"})
        after = meshcleanup.regate(before, with_loop)

        assert after.volume > before.volume, "the re-measured volume should include the loop"
        assert after.backends_applied == ["trimesh", "manifold3d"]
        assert after.repairs_rejected == ["pymeshfix"]
        assert after.volume_retained == 0.91


class TestTheFeatureSharesTheModelsAtlas:
    """Handing the feature its own image makes trimesh pack a combined atlas, and its packer grows the
    result however small the swatch is -- a live run went from a 2048 atlas to 8192, 16x the area and
    94% of it empty, on exactly the output the colour requirement cares about. The feature colour is
    painted into unreferenced texels of the model's own atlas instead.
    """

    @staticmethod
    def _model(atlas_px=512, uv_scale=0.45):
        """A textured mesh whose uv occupies only part of its atlas, as a real unwrap does."""
        mesh = trimesh.creation.icosphere(subdivisions=2, radius=10.0)
        mesh.visual = trimesh.visual.TextureVisuals(
            uv=np.random.rand(len(mesh.vertices), 2) * uv_scale,
            image=Image.new("RGB", (atlas_px, atlas_px), (200, 30, 30)))
        return mesh

    def test_the_atlas_keeps_its_original_resolution(self):
        model = self._model(atlas_px=512)
        joined, mode = printfeatures._join(
            model, printfeatures.loop_geometry(model), "concat", (0, 0, 255))
        assert mode == "concat"
        assert joined.visual.material.image.size == (512, 512), (
            "the atlas grew, so the feature was packed as a second image")

    def test_both_colours_reach_the_shared_atlas(self):
        model = self._model(atlas_px=512)
        joined, _ = printfeatures._join(
            model, printfeatures.loop_geometry(model), "concat", (0, 0, 255))
        colours = {c for _, c in joined.visual.material.image.convert("RGB").getcolors(1 << 22)}
        assert (0, 0, 255) in colours, "the feature colour is absent from the shared atlas"
        assert (200, 30, 30) in colours, "the model's own colour was overwritten"

    def test_the_swatch_lands_on_texels_no_uv_references(self):
        """Painting over a texel the model samples would recolour part of the subject."""
        model = self._model(atlas_px=512, uv_scale=0.45)
        painted = printfeatures._paint_swatch_into_atlas(model, (0, 0, 255))
        assert painted is not None
        _, (u, v) = painted
        # The model's uv never exceeds uv_scale, so a point beyond it cannot be one the model samples.
        assert u > 0.45 or v > 0.45, f"the swatch at ({u}, {v}) sits inside the model's uv range"

    def test_every_feature_vertex_samples_the_swatch(self):
        model = self._model(atlas_px=512)
        loop = printfeatures.loop_geometry(model)
        joined, _ = printfeatures._join(model, loop, "concat", (0, 0, 255))
        feature_uv = joined.visual.uv[len(model.vertices):]
        assert len(feature_uv) == len(loop.vertices)
        assert np.allclose(feature_uv, feature_uv[0]), "the feature's uvs are not all one point"

    def test_a_mesh_with_no_atlas_falls_back_rather_than_failing(self):
        """A concat explicitly requested on geometry-only output has no atlas to paint into."""
        assert printfeatures._paint_swatch_into_atlas(bare_sphere(), (0, 0, 255)) is None
        joined, mode = printfeatures._join(
            bare_sphere(), printfeatures.loop_geometry(bare_sphere()), "concat", (0, 0, 255))
        assert mode == "concat" and len(joined.faces) > 0

    def test_a_placeholder_material_is_not_treated_as_an_atlas(self):
        mesh = bare_sphere()
        mesh.visual = trimesh.visual.TextureVisuals(
            uv=np.random.rand(len(mesh.vertices), 2),
            image=trimesh.visual.material.empty_material().image)
        assert printfeatures._paint_swatch_into_atlas(mesh, (0, 0, 255)) is None


class TestBodyCountIsMeasuredOnGeometry:
    def test_a_textured_concat_reports_the_geometric_shell_count(self):
        """Counted on raw indices a uv-split mesh reports one body per face; a live run said 6663
        where the geometry had 4."""
        mesh = textured_sphere()
        _, report = printfeatures.apply_features(mesh, {"PRINT_FEATURES": "loop+base"})
        assert report["printFeatureBodies"] == "3", (
            f"expected model + loop + base = 3 shells, got {report['printFeatureBodies']}")


def hollow_tube(radius=10.0, height=20.0):
    """An open-topped tube, standing in for a mug or cup.

    Its bounding-box top-centre sits in the opening, where there is no material, which is what made a
    loop placed there fuse onto nothing on a live run.
    """
    tube = trimesh.creation.annulus(r_min=radius * 0.8, r_max=radius, height=height)
    return tube


class TestTheLoopAttachesToMaterial:
    def test_the_loop_is_anchored_over_the_highest_vertex_not_the_bbox_centre(self):
        """On a hollow model the bounding-box top-centre is the opening."""
        mesh = hollow_tube()
        loop = printfeatures.loop_geometry(mesh)
        centre_xy = mesh.bounds.mean(axis=0)[:2]
        loop_xy = loop.bounds.mean(axis=0)[:2]
        assert not np.allclose(loop_xy, centre_xy, atol=1e-6), (
            "the loop is still centred on the bounding box, so it spans the hollow opening")
        # It must sit over the wall, i.e. at roughly the tube's radius from the axis.
        assert np.linalg.norm(loop_xy - centre_xy) > mesh.extents[0] * 0.25

    def test_a_loop_on_a_hollow_model_fuses_into_one_solid(self):
        """The regression: on a mug this left the loop as a separate 2048-face shell that the print
        gate happily passed, because disjoint closed shells are each watertight."""
        mesh = hollow_tube()
        fused, mode = printfeatures._join(
            mesh, printfeatures.loop_geometry(mesh), "union", (0, 0, 0))
        assert mode == "union"
        assert printfeatures._body_count(fused) == printfeatures._body_count(mesh), (
            "the loop did not fuse into the hollow model")

    def test_a_union_that_leaves_the_feature_detached_is_rejected(self):
        """A floating part must fail loudly rather than ship as print-ready."""
        mesh = bare_sphere(radius=10.0)
        stray = trimesh.creation.torus(major_radius=2.0, minor_radius=0.5)
        stray.apply_translation([0, 0, 500.0])  # nowhere near the mesh
        with pytest.raises(printfeatures.PrintFeatureError, match="did not fuse"):
            printfeatures._join(mesh, stray, "union", (0, 0, 0))

    def test_a_loop_still_attaches_on_a_convex_model(self):
        mesh = bare_sphere(radius=10.0)
        fused, _ = printfeatures._join(mesh, printfeatures.loop_geometry(mesh), "union", (0, 0, 0))
        assert printfeatures._body_count(fused) == 1

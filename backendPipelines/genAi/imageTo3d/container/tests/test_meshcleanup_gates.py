#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the imageTo3d print-readiness gate and the repair ladder.

A reconstruction model's raw output is rarely printable: boundary holes leave a surface no slicer can
thicken, and two sheets pinched at one vertex leave a solid with no interior there. The gate measures
exactly the conditions a slicer needs, and the policy decides whether a failure suppresses the mesh
output or merely annotates it. Meshes here are hand-built face lists, so the gate arithmetic is
tested without trimesh installed.
"""

import pytest

import export
import meshcleanup

# A closed tetrahedron: every edge is shared by exactly two faces.
TETRAHEDRON_FACES = [(0, 1, 2), (0, 2, 3), (0, 3, 1), (1, 3, 2)]
# Two triangles sharing one edge: an open sheet with four boundary edges.
OPEN_SHEET_FACES = [(0, 1, 2), (1, 3, 2)]
# Two tetrahedra pinched at vertex 3: every edge is manifold, the shared vertex is not.
PINCHED_FACES = TETRAHEDRON_FACES + [(3, 4, 5), (3, 5, 6), (3, 6, 4), (4, 6, 5)]


class FakeMesh:
    """The slice of the trimesh surface the gate reads: faces, volume, and watertightness."""

    def __init__(self, faces, volume=1.0, is_watertight=None):
        self.faces = list(faces)
        self.volume = volume
        if is_watertight is not None:
            self.is_watertight = is_watertight


@pytest.mark.unit
class TestEvaluateGate:
    def test_a_closed_positive_volume_mesh_passes(self):
        report = meshcleanup.evaluate_gate(FakeMesh(TETRAHEDRON_FACES, volume=0.166))
        assert report.passed
        assert (report.non_manifold_edges, report.non_manifold_vertices) == (0, 0)
        assert report.broken_faces == []
        assert report.failures == []

    def test_an_open_sheet_fails_on_watertightness_and_broken_faces(self):
        report = meshcleanup.evaluate_gate(FakeMesh(OPEN_SHEET_FACES, volume=0.0))
        assert not report.passed
        assert not report.is_watertight
        # Four boundary edges, each touched by one of the two faces
        assert report.non_manifold_edges == 4
        assert report.broken_faces == [0, 1]
        assert any("not watertight" in f for f in report.failures)
        assert any("broken faces" in f for f in report.failures)

    def test_a_pinched_vertex_fails_even_though_every_edge_is_manifold(self):
        """Edge counting alone calls this solid watertight; a slicer cannot print the pinch."""
        report = meshcleanup.evaluate_gate(FakeMesh(PINCHED_FACES, volume=0.33))
        assert report.non_manifold_edges == 0
        assert report.non_manifold_vertices == 1
        assert not report.passed
        assert any("non-manifold vertices" in f for f in report.failures)

    def test_zero_volume_fails_even_on_a_closed_surface(self):
        report = meshcleanup.evaluate_gate(
            FakeMesh(TETRAHEDRON_FACES, volume=0.0, is_watertight=True))
        assert not report.passed
        assert any("no positive volume" in f for f in report.failures)

    def test_trimeshs_own_watertight_verdict_is_authoritative_when_present(self):
        report = meshcleanup.evaluate_gate(
            FakeMesh(TETRAHEDRON_FACES, volume=0.166, is_watertight=False))
        assert not report.is_watertight
        assert not report.passed

    def test_the_report_serialises_for_the_metadata_document(self):
        report = meshcleanup.evaluate_gate(FakeMesh(OPEN_SHEET_FACES, volume=0.0))
        as_dict = report.as_dict()
        assert as_dict["isWatertight"] is False
        assert as_dict["passed"] is False
        assert as_dict["brokenFaceCount"] == 2
        assert as_dict["nonManifoldEdges"] == 4


@pytest.mark.unit
class TestResolveBackends:
    def test_the_default_ladder_is_the_permissive_pair(self):
        for raw in ("", None, "  "):
            assert meshcleanup.resolve_backends(raw) == ["trimesh", "manifold3d"]

    def test_the_ladder_order_is_fixed_regardless_of_how_the_list_is_written(self):
        """A reproducible run needs a fixed order; REPAIR_BACKENDS selects rungs, not their order."""
        assert meshcleanup.resolve_backends("manifold3d,trimesh") == ["trimesh", "manifold3d"]
        assert meshcleanup.resolve_backends("pymeshlab,trimesh,pymeshfix") == [
            "trimesh", "pymeshfix", "pymeshlab"]

    def test_an_unknown_backend_raises_rather_than_being_skipped(self):
        with pytest.raises(meshcleanup.MeshRepairError, match="Unknown mesh repair backend"):
            meshcleanup.resolve_backends("trimesh,meshlab2")

    def test_the_gpl_backends_are_named_separately_from_the_permissive_ladder(self):
        assert meshcleanup.GPL_BACKENDS == ("pymeshfix", "pymeshlab")
        assert all(b in meshcleanup.REPAIR_LADDER for b in meshcleanup.GPL_BACKENDS)


@pytest.mark.unit
class TestGplBackendsAreOptIn:
    def test_an_absent_gpl_backend_raises_a_clear_error(self, monkeypatch):
        """pymeshfix and pymeshlab are GPL-3.0 and present only under ENABLE_GPL_MESH_REPAIR."""
        import builtins

        real_import = builtins.__import__

        def blocked_import(name, *args, **kwargs):
            if name in meshcleanup.GPL_BACKENDS:
                raise ImportError(f"No module named {name!r}")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", blocked_import)
        for backend in meshcleanup.GPL_BACKENDS:
            with pytest.raises(meshcleanup.MeshRepairError, match="ENABLE_GPL_MESH_REPAIR"):
                meshcleanup._REPAIR_FUNCTIONS[backend](FakeMesh(OPEN_SHEET_FACES))


@pytest.mark.unit
class TestRunCleanup:
    def test_a_mesh_that_already_passes_is_left_untouched(self):
        mesh = FakeMesh(TETRAHEDRON_FACES, volume=0.166)
        cleaned, report = meshcleanup.run_cleanup(mesh, {"REPAIR_BACKENDS": "trimesh,manifold3d"})
        assert cleaned is mesh
        assert report.passed
        assert report.backends_applied == []

    def test_the_ladder_stops_at_the_first_rung_that_passes(self, monkeypatch):
        repaired = FakeMesh(TETRAHEDRON_FACES, volume=0.166)
        calls = []

        def fake_trimesh_repair(mesh):
            calls.append("trimesh")
            return repaired

        def fake_manifold_repair(mesh):
            calls.append("manifold3d")
            return repaired

        monkeypatch.setitem(meshcleanup._REPAIR_FUNCTIONS, "trimesh", fake_trimesh_repair)
        monkeypatch.setitem(meshcleanup._REPAIR_FUNCTIONS, "manifold3d", fake_manifold_repair)

        cleaned, report = meshcleanup.run_cleanup(
            FakeMesh(OPEN_SHEET_FACES, volume=0.0), {"REPAIR_BACKENDS": "trimesh,manifold3d"})
        assert calls == ["trimesh"]
        assert cleaned is repaired
        assert report.passed
        assert report.backends_applied == ["trimesh"]

    def test_an_unrepairable_mesh_returns_a_failing_report_with_every_rung_recorded(
            self, monkeypatch):
        monkeypatch.setitem(meshcleanup._REPAIR_FUNCTIONS, "trimesh", lambda mesh: mesh)
        monkeypatch.setitem(meshcleanup._REPAIR_FUNCTIONS, "manifold3d", lambda mesh: mesh)

        _cleaned, report = meshcleanup.run_cleanup(
            FakeMesh(OPEN_SHEET_FACES, volume=0.0), {"REPAIR_BACKENDS": "trimesh,manifold3d"})
        assert not report.passed
        assert report.backends_applied == ["trimesh", "manifold3d"]


@pytest.mark.unit
class TestGatePolicy:
    _OUTPUT_DIR = "pipelines/genai-image-to-3d/genai-image-to-3d/output/E1/files/"

    @staticmethod
    def _outputs(root):
        (root / "photo.stl").write_text("stl", encoding="utf-8")
        (root / "imageTo3d_report.json").write_text("{}", encoding="utf-8")

    def test_a_passing_gate_writes_the_outputs_under_either_policy(self):
        report = meshcleanup.evaluate_gate(FakeMesh(TETRAHEDRON_FACES, volume=0.166))
        for policy in ("failClosed", "bestEffort"):
            assert meshcleanup.resolve_gate_outcome(report, policy) == (True, None)

    def test_fail_closed_writes_no_mesh_output(self, tmp_path):
        """The rejection is only legible through the gate report, so the error is returned rather
        than raised: the caller writes the report first."""
        self._outputs(tmp_path)
        report = meshcleanup.evaluate_gate(FakeMesh(OPEN_SHEET_FACES, volume=0.0))
        write_outputs, gate_error = meshcleanup.resolve_gate_outcome(report, "failClosed")

        assert write_outputs is False
        assert isinstance(gate_error, meshcleanup.PrintGateError)

        uploaded = []
        if write_outputs:
            export.upload_output_dir(
                lambda bucket, key, path: uploaded.append(key), str(tmp_path), "asset-bucket",
                self._OUTPUT_DIR, "", skip_names=("imageTo3d_report.json",))
        assert uploaded == []

    def test_best_effort_writes_the_outputs_and_records_the_failures(self, tmp_path):
        self._outputs(tmp_path)
        report = meshcleanup.evaluate_gate(FakeMesh(OPEN_SHEET_FACES, volume=0.0))
        write_outputs, gate_error = meshcleanup.resolve_gate_outcome(report, "bestEffort")

        assert write_outputs is True
        assert gate_error is None

        uploaded = []
        export.upload_output_dir(
            lambda bucket, key, path: uploaded.append(key), str(tmp_path), "asset-bucket",
            self._OUTPUT_DIR, "", skip_names=("imageTo3d_report.json",))
        assert uploaded == [f"{self._OUTPUT_DIR}photo.stl"]
        assert report.as_dict()["passed"] is False
        assert report.as_dict()["failures"]

    def test_a_blank_policy_defaults_to_fail_closed(self):
        report = meshcleanup.evaluate_gate(FakeMesh(OPEN_SHEET_FACES, volume=0.0))
        for policy in ("", None, "  "):
            write_outputs, gate_error = meshcleanup.resolve_gate_outcome(report, policy)
            assert write_outputs is False
            assert isinstance(gate_error, meshcleanup.PrintGateError)

    def test_an_unknown_policy_raises(self):
        report = meshcleanup.evaluate_gate(FakeMesh(OPEN_SHEET_FACES, volume=0.0))
        with pytest.raises(meshcleanup.PrintGateError, match="Unknown print gate policy"):
            meshcleanup.resolve_gate_outcome(report, "warnOnly")


class TestLadderResilience:
    """A rung that cannot run must not cost the run its already-generated geometry."""

    def _holed_mesh(self):
        import trimesh

        mesh = trimesh.creation.icosphere(subdivisions=2)
        mesh.faces = mesh.faces[:-6]
        return mesh

    def test_a_raising_rung_is_skipped_and_recorded(self, monkeypatch):
        import meshcleanup

        def explode(_mesh):
            raise AttributeError("type object 'manifold3d.Manifold' has no attribute 'from_mesh'")

        monkeypatch.setitem(meshcleanup._REPAIR_FUNCTIONS, "manifold3d", explode)
        mesh, report = meshcleanup.run_cleanup(
            self._holed_mesh(), {"REPAIR_BACKENDS": "trimesh,manifold3d"}
        )
        assert mesh is not None and len(mesh.faces) > 0, "geometry was lost to a failed rung"
        assert "manifold3d" not in report.backends_applied, "a skipped rung claimed to have run"
        assert any("manifold3d" in f for f in report.failures), (
            "the report must name the rung that could not run"
        )

    def test_a_rung_absent_from_the_image_still_raises_before_the_walk(self):
        import meshcleanup
        import pytest as _pytest

        with _pytest.raises(meshcleanup.MeshRepairError):
            meshcleanup.resolve_backends("trimesh,notarealbackend")


@pytest.mark.unit
class TestDestructiveRepairsAreRejected:
    """A repair that removes the model satisfies the gate without producing anything printable.

    Observed live: pymeshfix reduced a fragmented reconstruction to a single small body, leaving a
    watertight, manifold, zero-defect mesh holding 2.5% of the original volume -- a thin sliver of one
    trouser leg. Every gate criterion passed. Only a fidelity check catches that, because printability
    and likeness are independent properties.
    """

    def _ladder(self, monkeypatch, factor, params=None):
        """Run a one-rung ladder whose rung scales the mesh volume by ``factor``."""
        def shrink(mesh):
            return FakeMesh(OPEN_SHEET_FACES if factor < 0.5 else TETRAHEDRON_FACES,
                            volume=1.0 * factor, is_watertight=True)

        monkeypatch.setitem(meshcleanup._REPAIR_FUNCTIONS, "trimesh", shrink)
        start = FakeMesh(OPEN_SHEET_FACES, volume=1.0)
        settings = {"REPAIR_BACKENDS": "trimesh"}
        settings.update(params or {})
        return meshcleanup.run_cleanup(start, settings)

    def test_a_repair_that_destroys_the_model_is_discarded(self, monkeypatch):
        mesh, report = self._ladder(monkeypatch, factor=0.025)
        assert report.repairs_rejected, "the destructive repair was accepted"
        assert "trimesh" not in report.backends_applied
        assert mesh.volume == 1.0, "the pre-repair mesh must be kept when a repair is rejected"

    def test_a_rejection_is_recorded_in_the_failures(self, monkeypatch):
        _, report = self._ladder(monkeypatch, factor=0.025)
        assert any("repair rejected" in f for f in report.failures)
        assert "repairsRejected" in report.as_dict()

    def test_a_repair_that_preserves_the_model_is_accepted(self, monkeypatch):
        """The guard must not reject ordinary hole filling, which preserves or grows volume."""
        mesh, report = self._ladder(monkeypatch, factor=1.02)
        assert report.backends_applied == ["trimesh"]
        assert not report.repairs_rejected
        assert report.passed

    def test_the_floor_is_configurable(self, monkeypatch):
        """An operator who wants aggressive repair can lower the floor."""
        _, report = self._ladder(monkeypatch, factor=0.3, params={"MIN_VOLUME_RETENTION": "0.1"})
        assert report.backends_applied == ["trimesh"], "a lowered floor must accept the repair"

    def test_a_zero_floor_accepts_any_repair(self, monkeypatch):
        _, report = self._ladder(monkeypatch, factor=0.001, params={"MIN_VOLUME_RETENTION": "0"})
        assert report.backends_applied == ["trimesh"]

    @pytest.mark.parametrize("bad", ["abc", "-1", "2", ""])
    def test_an_unusable_floor_falls_back_to_the_default(self, bad):
        assert meshcleanup._resolve_min_volume_retention(
            {"MIN_VOLUME_RETENTION": bad}) == meshcleanup.DEFAULT_MIN_VOLUME_RETENTION

    def test_an_unmeasurable_volume_disables_the_guard_rather_than_blocking_repair(self,
                                                                                  monkeypatch):
        """The guard catches destruction; it must not stop repair when volume cannot be read."""
        monkeypatch.setitem(meshcleanup._REPAIR_FUNCTIONS, "trimesh",
                            lambda mesh: FakeMesh(TETRAHEDRON_FACES, volume=0.01,
                                                  is_watertight=True))
        _, report = meshcleanup.run_cleanup(
            FakeMesh(OPEN_SHEET_FACES, volume=0.0), {"REPAIR_BACKENDS": "trimesh"})
        assert report.backends_applied == ["trimesh"]


@pytest.mark.unit
class TestTexturedMeshesAreMeasuredWelded:
    """A texture's uv seams must not be counted as holes in the surface.

    Wherever an atlas has a seam, one position needs two uv coordinates, so a textured mesh carries a
    separate vertex per face corner there. That changes no geometry -- the enclosed volume is identical
    -- but measured on the vertex indices each seam edge belongs to one face instead of two. Left
    unwelded, the gate rejects every textured mesh, which is the opposite of what a colour print needs.
    """

    @staticmethod
    def _sphere():
        trimesh = pytest.importorskip("trimesh")
        return trimesh.creation.icosphere(subdivisions=2, radius=10.0)

    @staticmethod
    def _uv_split(mesh):
        """The same solid with one vertex per face corner, as a textured export carries it."""
        np = pytest.importorskip("numpy")
        trimesh = pytest.importorskip("trimesh")
        from PIL import Image

        split = trimesh.Trimesh(
            vertices=mesh.vertices[mesh.faces].reshape(-1, 3),
            faces=np.arange(len(mesh.faces) * 3).reshape(-1, 3),
            process=False)
        split.visual = trimesh.visual.TextureVisuals(
            uv=np.random.rand(len(split.vertices), 2),
            image=Image.new("RGB", (32, 32), (200, 30, 30)))
        return split

    def test_a_uv_split_sphere_still_passes_the_gate(self):
        original = self._sphere()
        assert meshcleanup.evaluate_gate(original).passed, "the welded sphere should pass"
        report = meshcleanup.evaluate_gate(self._uv_split(original))
        assert report.passed, f"a textured sphere was rejected: {report.failures}"
        assert report.is_watertight
        assert report.non_manifold_edges == 0
        assert report.broken_faces == []

    def test_welding_preserves_the_volume_the_gate_reports(self):
        """The weld is a measurement device, not a repair -- it must not change what is measured."""
        original = self._sphere()
        split = self._uv_split(original)
        assert meshcleanup.evaluate_gate(split).volume == pytest.approx(
            meshcleanup.evaluate_gate(original).volume, rel=1e-9)

    def test_a_real_hole_is_still_reported_on_a_textured_mesh(self):
        """Welding must not mask genuine defects: it merges coincident positions, it does not fill."""
        original = self._sphere()
        holed = original.copy()
        holed.faces = holed.faces[:-8]
        report = meshcleanup.evaluate_gate(self._uv_split(holed))
        assert not report.passed
        assert report.non_manifold_edges > 0, "an actual hole must survive the weld"

    def test_the_exported_mesh_is_not_welded_by_measuring_it(self):
        """The uv splits have to survive the gate, or measuring would strip the texture."""
        split = self._uv_split(self._sphere())
        before = len(split.vertices)
        meshcleanup.evaluate_gate(split)
        assert len(split.vertices) == before
        assert split.visual.uv is not None and len(split.visual.uv) == before


@pytest.mark.unit
class TestHoleClosingCeiling:
    """pymeshlab's meshing_close_holes defaults to maxholesize=30 EDGES and leaves anything wider open
    without reporting it, so the rung could run to completion and still leave the mesh failing the gate.
    """

    def test_the_default_ceiling_is_far_above_the_library_default(self):
        assert meshcleanup.DEFAULT_CLOSE_HOLE_MAX_EDGES > 30

    @pytest.mark.parametrize("raw,expected", [
        ("", meshcleanup.DEFAULT_CLOSE_HOLE_MAX_EDGES),
        (None, meshcleanup.DEFAULT_CLOSE_HOLE_MAX_EDGES),
        ("1000", 1000),
        ("0", 0),
        ("  250  ", 250),
        ("not-a-number", meshcleanup.DEFAULT_CLOSE_HOLE_MAX_EDGES),
        ("-5", meshcleanup.DEFAULT_CLOSE_HOLE_MAX_EDGES),
    ])
    def test_the_ceiling_resolves_from_the_template_setting(self, raw, expected):
        assert meshcleanup._resolve_close_hole_max_edges(
            {"CLOSE_HOLE_MAX_EDGES": raw}) == expected

    def test_the_ceiling_reaches_the_pymeshlab_rung(self, monkeypatch):
        """The ladder must pass the resolved value through, or the setting would be inert."""
        seen = {}

        def fake_pymeshlab(mesh, max_hole_edges=None):
            seen["max_hole_edges"] = max_hole_edges
            return mesh

        monkeypatch.setitem(meshcleanup._REPAIR_FUNCTIONS, "pymeshlab", fake_pymeshlab)
        meshcleanup.run_cleanup(
            FakeMesh(OPEN_SHEET_FACES, volume=1.0),
            {"REPAIR_BACKENDS": "pymeshlab", "CLOSE_HOLE_MAX_EDGES": "777"})
        assert seen["max_hole_edges"] == 777

    def test_a_large_ceiling_closes_a_hole_the_library_default_leaves_open(self):
        """The behaviour the change exists for, measured rather than asserted from the docs."""
        pytest.importorskip("pymeshlab")
        np = pytest.importorskip("numpy")
        trimesh = pytest.importorskip("trimesh")

        sphere = trimesh.creation.icosphere(subdivisions=3, radius=1.0)
        keep = np.ones(len(sphere.faces), bool)
        keep[:120] = False          # a hole whose boundary is wider than 30 edges
        holed = trimesh.Trimesh(vertices=sphere.vertices, faces=sphere.faces[keep], process=False)
        assert not holed.is_watertight

        at_library_default = meshcleanup._repair_pymeshlab(holed, max_hole_edges=30)
        assert not at_library_default.is_watertight, (
            "a 30-edge ceiling closed this hole, so the test no longer covers the case it was written for")

        generous = meshcleanup._repair_pymeshlab(holed)
        assert generous.is_watertight, "the default ceiling failed to close the hole"
        assert generous.volume == pytest.approx(sphere.volume, rel=0.05), (
            "closing the hole should restore roughly the original volume, not balloon or collapse it")


@pytest.mark.unit
class TestARungCannotMakeThingsWorse:
    """Two faults that let the ladder ship a mesh worse than the one it was given.

    On a TripoSR mug the first rung took 31 non-manifold edges to 14739 and the ladder never recovered,
    even though the raw mesh had been within reach of the gate.
    """

    def test_the_real_trimesh_rung_mutates_in_place_and_so_must_be_given_a_copy(self):
        """The premise behind _copy_for_rung, verified against the actual library.

        trimesh.repair operates on the mesh it is handed and the rung returns that same object, so
        without a copy the ladder's `candidate` and its current mesh are one object and discarding the
        candidate keeps the damage.
        """
        trimesh = pytest.importorskip("trimesh")
        np = pytest.importorskip("numpy")

        sphere = trimesh.creation.icosphere(subdivisions=2, radius=1.0)
        keep = np.ones(len(sphere.faces), bool)
        keep[:20] = False
        holed = trimesh.Trimesh(vertices=sphere.vertices, faces=sphere.faces[keep], process=False)

        returned = meshcleanup._repair_trimesh(holed)
        assert returned is holed, (
            "trimesh's rung no longer returns its input; _copy_for_rung may be unnecessary, but check "
            "the other rungs before removing it")

        # And the copy helper genuinely isolates it.
        fresh = trimesh.Trimesh(vertices=sphere.vertices, faces=sphere.faces[keep], process=False)
        isolated = meshcleanup._copy_for_rung(fresh)
        assert isolated is not fresh
        meshcleanup._repair_trimesh(isolated)
        assert len(fresh.faces) == int(keep.sum()), "the original was modified through the copy"

    def test_the_copy_helper_tolerates_a_mesh_that_cannot_be_copied(self):
        """A minimal mesh-like object must not break the ladder."""
        bare = FakeMesh(TETRAHEDRON_FACES, volume=1.0)
        assert meshcleanup._copy_for_rung(bare) is bare

    def test_a_rung_that_raises_the_non_manifold_count_is_discarded(self, monkeypatch):
        """The guard that would have caught the TripoSR regression automatically."""
        good = FakeMesh(TETRAHEDRON_FACES, volume=1.0, is_watertight=True)
        # An open sheet has boundary edges, so it measures worse than the closed tetrahedron.
        worse = FakeMesh(OPEN_SHEET_FACES, volume=1.0)
        assert meshcleanup.evaluate_gate(worse).non_manifold_edges > 0

        monkeypatch.setitem(meshcleanup._REPAIR_FUNCTIONS, "trimesh", lambda mesh: worse)
        result, report = meshcleanup.run_cleanup(
            FakeMesh(OPEN_SHEET_FACES[:1], volume=1.0), {"REPAIR_BACKENDS": "trimesh"})

        assert "trimesh" not in report.backends_applied
        assert any("raised non-manifold edges" in r for r in report.repairs_rejected), (
            f"expected a topology rejection, got {report.repairs_rejected}")

    def test_a_rung_that_passes_the_gate_is_kept_whatever_the_count_did(self, monkeypatch):
        """The guard must not reject a result that is actually printable."""
        passing = FakeMesh(TETRAHEDRON_FACES, volume=1.0, is_watertight=True)
        assert meshcleanup.evaluate_gate(passing).passed

        monkeypatch.setitem(meshcleanup._REPAIR_FUNCTIONS, "trimesh", lambda mesh: passing)
        result, report = meshcleanup.run_cleanup(
            FakeMesh(OPEN_SHEET_FACES, volume=1.0), {"REPAIR_BACKENDS": "trimesh"})
        assert report.passed
        assert report.backends_applied == ["trimesh"]


@pytest.mark.unit
class TestWindingIsNormalised:
    """An inside-out mesh is the same solid with every normal pointing inward. A slicer fills by
    winding, so it reads the solid as the surrounding space -- a real print defect, and a mechanical
    one. Measured on TripoSR at marching-cubes resolution 384: watertight, zero non-manifold edges,
    zero broken faces, and rejected on a volume of -0.196.
    """

    @staticmethod
    def _inverted_sphere():
        trimesh = pytest.importorskip("trimesh")
        sphere = trimesh.creation.icosphere(subdivisions=2, radius=1.0)
        return trimesh.Trimesh(vertices=sphere.vertices, faces=sphere.faces[:, ::-1], process=False)

    def test_a_negative_volume_mesh_is_flipped(self):
        inverted = self._inverted_sphere()
        assert meshcleanup._volume_of(inverted) < 0
        fixed, flipped = meshcleanup.normalize_winding(inverted)
        assert flipped
        assert meshcleanup._volume_of(fixed) > 0
        assert meshcleanup.evaluate_gate(fixed).passed

    def test_a_correctly_wound_mesh_is_left_alone(self):
        trimesh = pytest.importorskip("trimesh")
        sphere = trimesh.creation.icosphere(subdivisions=2, radius=1.0)
        same, flipped = meshcleanup.normalize_winding(sphere)
        assert not flipped
        assert same is sphere

    def test_flipping_changes_no_geometry(self):
        """The correction must be lossless -- the vertex set is untouched, only face order changes."""
        np = pytest.importorskip("numpy")
        inverted = self._inverted_sphere()
        fixed, _ = meshcleanup.normalize_winding(inverted)
        assert len(fixed.vertices) == len(inverted.vertices)
        assert len(fixed.faces) == len(inverted.faces)
        assert np.allclose(np.sort(fixed.vertices, axis=0), np.sort(inverted.vertices, axis=0))
        assert meshcleanup._volume_of(fixed) == pytest.approx(-meshcleanup._volume_of(inverted))

    def test_the_ladder_returns_a_positive_volume_mesh(self):
        """The end-to-end behaviour: an inverted input must not come out of run_cleanup still failing
        on volume when nothing else is wrong with it."""
        inverted = self._inverted_sphere()
        result, report = meshcleanup.run_cleanup(inverted, {"REPAIR_BACKENDS": "trimesh"})
        assert report.volume > 0, f"the ladder returned a negative volume: {report.failures}"
        assert not any("no positive volume" in f for f in report.failures)

    def test_a_mesh_that_cannot_be_copied_is_reported_not_crashed(self):
        bare = FakeMesh(TETRAHEDRON_FACES, volume=-1.0)
        same, flipped = meshcleanup.normalize_winding(bare)
        assert same is bare and not flipped

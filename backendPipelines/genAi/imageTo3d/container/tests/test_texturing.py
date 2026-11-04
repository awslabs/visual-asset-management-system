#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for carrying the generated texture across the imageTo3d cleanup stage.

Every cleanup operation returns geometry alone -- decimation, the manifold3d boolean pass and the
pymeshfix/pymeshlab reconstructions all hand back a mesh whose visual is vertex colour with no uv
array -- and the GLB is the preview a viewer shows, so an untextured GLB is a product defect rather
than a cosmetic one. These tests pin the capture, the three restore paths and the report value each
one produces, over real trimesh meshes where trimesh is installed.

STL carries no colour whatever the visual holds, so an untextured STL is the format and not a
degradation.
"""

import sys
from unittest.mock import MagicMock

import pytest

import export
import texturing

# The capture/restore logic is exercised against real trimesh geometry. conftest stubs trimesh with
# a MagicMock when it is absent, which cannot answer a vertex count, so those tests skip there.
_TRIMESH_INSTALLED = not isinstance(sys.modules.get("trimesh"), MagicMock)
requires_trimesh = pytest.mark.skipif(
    not _TRIMESH_INSTALLED, reason="trimesh is not installed in this environment")


def _atlas_image(size=32):
    """A square atlas whose colour varies with position, so reading it in the wrong place shows.

    A flat image satisfies every "is there a texture" check while making a misplaced read indistinguishable
    from a correct one, which is the one thing a texture-placement test must not allow.
    """
    import numpy as np
    from PIL import Image

    y, x = np.mgrid[0:size, 0:size]
    return Image.fromarray(np.dstack([
        (x * 255 // (size - 1)).astype(np.uint8),
        (y * 255 // (size - 1)).astype(np.uint8),
        np.full((size, size), 128, np.uint8)]))


def _textured_box(image_size=(4, 4)):
    """A unit box carrying a per-vertex uv field over a SimpleMaterial image, as an OBJ loads."""
    import numpy as np
    import trimesh
    from PIL import Image

    mesh = trimesh.creation.box()
    uv = np.linspace(0.0, 1.0, len(mesh.vertices) * 2).reshape(len(mesh.vertices), 2)
    mesh.visual = trimesh.visual.TextureVisuals(
        uv=uv, image=Image.new("RGB", image_size, (12, 34, 56)))
    return mesh


def _rebuilt_from(vertices, faces):
    """A mesh over an exact vertex set, kept as given rather than merged or reordered."""
    import numpy as np
    import trimesh

    return trimesh.Trimesh(
        vertices=np.asarray(vertices), faces=np.asarray(faces), process=False)


def _rebuilt_without_texture(mesh, drop_faces=0):
    """``mesh``'s geometry alone, the way a repair rung hands it back.

    ``trimesh.Trimesh(..., process=True)`` is what ``_repair_pymeshfix`` and ``_repair_pymeshlab``
    close with, and it is what converts TextureVisuals to ColorVisuals.
    """
    import numpy as np
    import trimesh

    faces = np.asarray(mesh.faces)
    if drop_faces:
        faces = faces[:-drop_faces]
    return trimesh.Trimesh(vertices=np.asarray(mesh.vertices), faces=faces, process=True)


def _fake_xatlas(vmapping, indices, uvs):
    """An xatlas stand-in returning a known ``(vmapping, indices, uvs)`` triple."""
    module = MagicMock()
    module.parametrize.return_value = (vmapping, indices, uvs)
    return module


def _grid_atlas(faces):
    """A valid unwrap of ``faces``, as xatlas's ``(vmapping, indices, uvs)``, laid out on a grid.

    Every corner becomes its own atlas vertex and every face gets its own cell, which is a real
    parameterisation rather than a placeholder: each face has non-zero area in uv space, so a bake
    rasterises into it. An all-zero uv array satisfies the same call signature while giving every face
    zero area, which makes a bake write nothing and any assertion about the result vacuous.
    """
    import numpy as np

    faces = np.asarray(faces)
    across = int(np.ceil(np.sqrt(len(faces))))
    cell = 1.0 / across
    corner = np.array([[0.1, 0.1], [0.9, 0.15], [0.5, 0.9]])
    uvs = np.zeros((len(faces) * 3, 2))
    for number in range(len(faces)):
        origin = np.array([(number % across) * cell, (number // across) * cell])
        uvs[number * 3:number * 3 + 3] = origin + corner * cell
    return faces.ravel(), np.arange(len(faces) * 3).reshape(-1, 3), uvs


@pytest.mark.unit
@requires_trimesh
class TestCleanupDestroysTheTexture:
    """The premise the capture exists for, asserted rather than assumed."""

    def test_decimation_returns_geometry_with_no_uv_array(self):
        mesh = _textured_box()
        assert mesh.visual.uv is not None
        decimated = mesh.simplify_quadric_decimation(face_count=6)
        assert getattr(decimated.visual, "uv", None) is None

    def test_a_reconstruction_pass_returns_geometry_with_no_uv_array(self):
        rebuilt = _rebuilt_without_texture(_textured_box())
        assert getattr(rebuilt.visual, "uv", None) is None

    def test_an_untextured_obj_export_emits_no_mtl_or_map(self, tmp_path):
        rebuilt = _rebuilt_without_texture(_textured_box())
        rebuilt.export(str(tmp_path / "clean.obj"), file_type="obj", include_texture=True)
        assert sorted(p.name for p in tmp_path.iterdir()) == ["clean.obj"]

    def test_the_restored_texture_puts_the_mtl_and_the_map_back(self, tmp_path):
        mesh = _textured_box()
        snapshot = texturing.capture_texture(mesh)
        restored, status = texturing.restore_texture(
            _rebuilt_without_texture(mesh), snapshot, (), str(tmp_path))

        assert status == texturing.TEXTURE_PRESERVED
        restored.export(str(tmp_path / "tex.obj"), file_type="obj", include_texture=True)
        written = sorted(p.name for p in tmp_path.iterdir())
        assert "tex.obj" in written
        assert any(name.endswith(".mtl") for name in written)
        assert any(name.endswith(".png") for name in written)


@pytest.mark.unit
@requires_trimesh
class TestCaptureTexture:
    def test_a_textured_mesh_yields_its_uv_field_material_and_topology(self):
        mesh = _textured_box()
        snapshot = texturing.capture_texture(mesh)
        assert snapshot.has_texture()
        assert len(snapshot.uv) == len(mesh.vertices)
        assert snapshot.vertex_count == len(mesh.vertices)
        assert snapshot.face_count == len(mesh.faces)
        assert texturing.material_image(snapshot.material) is not None

    def test_the_captured_arrays_do_not_track_later_mutation_of_the_mesh(self):
        """The trimesh repairs mutate the mesh they are given, so a reference into its buffers would
        describe the repaired mesh by the time it is read back."""
        mesh = _textured_box()
        snapshot = texturing.capture_texture(mesh)
        first_uv = tuple(snapshot.uv[0])
        first_vertex = tuple(snapshot.vertices[0])

        mesh.visual.uv[0] = (0.99, 0.99)
        mesh.vertices[0] = (9.0, 9.0, 9.0)

        assert tuple(snapshot.uv[0]) == first_uv
        assert tuple(snapshot.vertices[0]) == first_vertex

    def test_an_untextured_mesh_yields_a_snapshot_that_only_records_the_topology(self):
        import trimesh

        mesh = trimesh.creation.box()
        snapshot = texturing.capture_texture(mesh)
        assert not snapshot.has_texture()
        assert (snapshot.vertex_count, snapshot.face_count) == (len(mesh.vertices), len(mesh.faces))

    def test_a_uv_field_with_no_map_of_its_own_is_still_carried(self):
        """trimesh fills a material with no image in with a flat placeholder, so the uv field is
        what marks a texture. Carrying it costs nothing and keeps a flat map's own colour."""
        import numpy as np
        import trimesh

        mesh = trimesh.creation.box()
        mesh.visual = trimesh.visual.TextureVisuals(uv=np.zeros((len(mesh.vertices), 2)))
        assert texturing.capture_texture(mesh).has_texture()

    def test_a_mesh_whose_visual_is_vertex_colour_carries_no_texture(self):
        """What every cleanup operation hands back: a ColorVisuals with no uv array at all."""
        import trimesh

        mesh = trimesh.creation.box()
        mesh.visual = trimesh.visual.ColorVisuals(mesh=mesh)
        assert not texturing.capture_texture(mesh).has_texture()

    def test_a_uv_field_that_is_not_per_vertex_is_not_carried(self):
        """Re-attaching a uv array of the wrong length produces a mesh no viewer can read."""
        import numpy as np
        import trimesh
        from PIL import Image

        mesh = trimesh.creation.box()
        mesh.visual = trimesh.visual.TextureVisuals(
            uv=np.zeros((len(mesh.vertices) - 2, 2)), image=Image.new("RGB", (4, 4)))
        assert not texturing.capture_texture(mesh).has_texture()

    def test_the_snapshot_recognises_the_topology_it_was_measured_against(self):
        mesh = _textured_box()
        snapshot = texturing.capture_texture(mesh)
        assert snapshot.fits(_rebuilt_without_texture(mesh))
        assert not snapshot.fits(_rebuilt_without_texture(mesh, drop_faces=2))

    def test_a_permuted_vertex_set_of_the_same_size_does_not_fit(self):
        """The manifold3d rung hands back the same counts in a different vertex order.

        The uv array is indexed by vertex, so re-attaching it over a permuted set paints each uv
        value onto a different surface point. Matching counts is not evidence the field still fits.
        """
        import numpy as np

        mesh = _textured_box()
        snapshot = texturing.capture_texture(mesh)
        permuted = _rebuilt_without_texture(mesh)
        order = np.roll(np.arange(len(permuted.vertices)), 1)
        inverse = np.argsort(order)
        permuted = _rebuilt_from(permuted.vertices[order], inverse[permuted.faces])

        assert len(permuted.vertices) == snapshot.vertex_count
        assert len(permuted.faces) == snapshot.face_count
        assert not snapshot.fits(permuted)

    def test_a_vertex_set_moved_by_the_cleanup_does_not_fit(self):
        mesh = _textured_box()
        snapshot = texturing.capture_texture(mesh)
        moved = _rebuilt_without_texture(mesh)
        shifted = moved.vertices.copy()
        shifted[0] += 0.25
        assert not snapshot.fits(_rebuilt_from(shifted, moved.faces))


@pytest.mark.unit
@requires_trimesh
class TestMaterialImage:
    def test_an_obj_style_material_keeps_its_map_on_image(self):
        from PIL import Image
        from trimesh.visual.material import SimpleMaterial

        material = SimpleMaterial(image=Image.new("RGB", (4, 4)))
        assert texturing.material_image(material) is material.image

    def test_a_gltf_style_material_keeps_its_map_on_base_colour_texture(self):
        """A GLB is what TRELLIS.2 and Step1X-3D emit, and trimesh loads its material as PBR."""
        from PIL import Image
        from trimesh.visual.material import PBRMaterial

        material = PBRMaterial(baseColorTexture=Image.new("RGB", (4, 4)))
        assert texturing.material_image(material) is material.baseColorTexture

    def test_a_material_with_neither_attribute_set_has_no_map(self):
        from trimesh.visual.material import SimpleMaterial

        assert texturing.material_image(SimpleMaterial()) is None

    def test_an_untextured_gltf_material_carries_no_map_to_re_attach(self):
        import numpy as np
        import trimesh
        from trimesh.visual.material import PBRMaterial

        mesh = trimesh.creation.box()
        mesh.visual = trimesh.visual.TextureVisuals(
            uv=np.zeros((len(mesh.vertices), 2)), material=PBRMaterial())
        assert not texturing.capture_texture(mesh).has_texture()
        assert texturing.generated_texture_status(mesh) == texturing.TEXTURE_NONE


@pytest.mark.unit
@requires_trimesh
class TestRestoreTextureUnchangedTopology:
    def test_a_permuted_vertex_set_is_not_reported_as_preserved(self, tmp_path, monkeypatch):
        """A run whose vertex order changed must not claim the uv field survived untouched.

        ``preserved`` on a permuted set is worse than no status: the report tells the operator the
        preview is textured while every uv value addresses a different surface point.
        """
        import numpy as np

        mesh = _textured_box()
        snapshot = texturing.capture_texture(mesh)
        rebuilt = _rebuilt_without_texture(mesh)
        order = np.roll(np.arange(len(rebuilt.vertices)), 1)
        permuted = _rebuilt_from(rebuilt.vertices[order], np.argsort(order)[rebuilt.faces])

        monkeypatch.setitem(sys.modules, "xatlas",
                            _fake_xatlas(*_grid_atlas(permuted.faces)))
        _restored, status = texturing.restore_texture(permuted, snapshot, (), str(tmp_path))
        assert status == texturing.TEXTURE_REBAKED

    def test_the_captured_uv_field_goes_back_on_as_it_was(self, tmp_path):
        import numpy as np
        import trimesh

        mesh = _textured_box()
        snapshot = texturing.capture_texture(mesh)
        restored, status = texturing.restore_texture(
            _rebuilt_without_texture(mesh), snapshot, (), str(tmp_path))

        assert status == texturing.TEXTURE_PRESERVED
        assert isinstance(restored.visual, trimesh.visual.TextureVisuals)
        assert np.allclose(restored.visual.uv, snapshot.uv)
        assert texturing.material_image(restored.visual.material) is not None

    def test_the_fast_path_needs_no_xatlas(self, tmp_path, monkeypatch):
        """The common case is a cleanup that repaired nothing; it must not need the unwrap."""
        monkeypatch.setitem(sys.modules, "xatlas", None)
        mesh = _textured_box()
        _restored, status = texturing.restore_texture(
            _rebuilt_without_texture(mesh), texturing.capture_texture(mesh), (), str(tmp_path))
        assert status == texturing.TEXTURE_PRESERVED

    def test_a_cleanup_that_kept_the_texture_visual_is_left_alone(self, tmp_path):
        """A ladder that ran no rung at all returns the loaded mesh untouched."""
        mesh = _textured_box()
        restored, status = texturing.restore_texture(
            mesh, texturing.capture_texture(mesh), (), str(tmp_path))
        assert restored is mesh
        assert status == texturing.TEXTURE_PRESERVED

    def test_no_texture_files_are_copied_on_the_fast_path(self, tmp_path):
        mesh = _textured_box()
        source = tmp_path / "material_0.png"
        source.write_bytes(b"png")
        output_dir = tmp_path / "out"
        output_dir.mkdir()

        texturing.restore_texture(
            _rebuilt_without_texture(mesh), texturing.capture_texture(mesh),
            (str(source),), str(output_dir))
        assert list(output_dir.iterdir()) == []


@pytest.mark.unit
@requires_trimesh
class TestRestoreTextureRebakesTheAtlas:
    """A uv atlas that outlives the cleanup is RE-BAKED onto the cleaned topology, not copied onto it.

    Three things could be carried across, and only one of them is both valid and faithful.

    Copying the uv FIELD across by nearest vertex is invalid outright. A uv is a coordinate into a
    discontinuous chart layout, so two adjacent vertices legitimately hold uvs in unrelated islands, and a
    triangle built from nearest-neighbour uvs sweeps across the atlas as it interpolates, sampling whatever
    lies between. Measured on the meshes the pipeline shipped: 51% of TRELLIS.2's faces and 49% of
    TripoSR's had a uv edge spanning more than a tenth of the atlas, and the models rendered as dark
    speckle with a black back and a black face.

    Collapsing the atlas to per-vertex COLOUR is valid but lossy, and severely so here: a 2048x2048 atlas
    over 690,000 vertices decimated to 99,000 keeps a fortieth of its texels, and its nearest-VERTEX
    lookup lands on the wrong body part wherever decimation moved a vertex further than the gap to a
    neighbouring surface. It stays as the fallback for an image without scipy.

    Re-baking asks the question with a correct answer -- which point on the ORIGINAL SURFACE is this
    cleaned vertex, and what was the uv there -- then rasterises the original atlas into a fresh layout,
    keeping detail at the new atlas's resolution rather than at the vertex spacing. Run with the cleanup
    off, both models ship a clean likeness, so everything wrong in a cleaned result was introduced here.
    """

    def test_a_changed_topology_gets_a_re_baked_atlas(self, tmp_path):
        import trimesh

        original = _textured_box()
        snapshot = texturing.capture_texture(original)
        repaired = _rebuilt_without_texture(original, drop_faces=2)

        restored, status = texturing.restore_texture(repaired, snapshot, (), str(tmp_path))

        assert status == texturing.TEXTURE_REBAKED
        assert isinstance(restored.visual, trimesh.visual.TextureVisuals)
        assert len(restored.visual.uv) == len(restored.vertices)
        assert texturing.real_material_image(restored.visual.material) is not None

    def test_the_captured_faces_are_what_makes_the_re_bake_possible(self, tmp_path):
        """The snapshot has to carry the original FACES, not just its vertices. Locating a cleaned vertex
        on the original surface is a question about triangles; without them the only answer available is
        the nearest original vertex, which is the lossy fallback."""
        original = _textured_box()
        snapshot = texturing.capture_texture(original)

        assert snapshot.faces is not None
        assert len(snapshot.faces) == snapshot.face_count

    def test_without_the_captured_faces_it_falls_back_to_colour(self, tmp_path):
        """Degrades rather than fails, and says which path it took."""
        import trimesh

        original = _textured_box()
        snapshot = texturing.capture_texture(original)
        snapshot.faces = None
        repaired = _rebuilt_without_texture(original, drop_faces=2)

        restored, status = texturing.restore_texture(repaired, snapshot, (), str(tmp_path))

        assert status == texturing.TEXTURE_RESAMPLED
        assert isinstance(restored.visual, trimesh.visual.ColorVisuals)

    def test_the_ladder_degrades_one_rung_at_a_time(self, tmp_path, monkeypatch):
        """Each rung is reached only when the one above it cannot run, and every rung reports its own name.

        Asserted as a sequence rather than one rung at a time, because the value of a fallback ladder is the
        ORDER: a rung that quietly runs ahead of a better one costs fidelity on every run and reports a
        status that looks deliberate.
        """
        original = _textured_box()
        repaired = _rebuilt_without_texture(original, drop_faces=2)

        best = texturing.capture_texture(original)
        _mesh, status = texturing.restore_texture(repaired.copy(), best, (), str(tmp_path))
        assert status == texturing.TEXTURE_REBAKED

        # No captured faces: the original surface cannot be searched, so the atlas becomes colour.
        no_faces = texturing.capture_texture(original)
        no_faces.faces = None
        _mesh, status = texturing.restore_texture(repaired.copy(), no_faces, (), str(tmp_path))
        assert status == texturing.TEXTURE_RESAMPLED

        # Nor can the atlas be read at all: only then is the uv field copied across.
        monkeypatch.setattr(texturing, "sample_atlas_colors", lambda uv, material: None)
        monkeypatch.setitem(sys.modules, "xatlas", _fake_xatlas(*_grid_atlas(repaired.faces)))
        _mesh, status = texturing.restore_texture(repaired.copy(), no_faces, (), str(tmp_path))
        assert status == texturing.TEXTURE_REPROJECTED

    def test_a_cleaned_vertex_is_located_on_the_original_surface_not_at_a_vertex(self):
        """The distinction the whole fix rests on, on a case built to separate them.

        A point placed at the centre of a large triangle is nearer to that triangle's interior than to any
        of its corners, so the surface answer is the interpolated uv at the centre while the nearest-vertex
        answer is a corner's uv. They differ, and only the first describes where the point actually is.
        """
        import numpy as np
        import trimesh

        # One big triangle in the z = 0 plane, its uvs spread across the atlas.
        vertices = np.array([[0.0, 0.0, 0.0], [4.0, 0.0, 0.0], [0.0, 4.0, 0.0]])
        mesh = trimesh.Trimesh(vertices=vertices, faces=np.array([[0, 1, 2]]), process=False)
        snapshot = texturing.TextureSnapshot(
            uv=np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]]),
            material=trimesh.visual.material.SimpleMaterial(image=_atlas_image()),
            vertices=vertices, faces=np.array([[0, 1, 2]]), vertex_count=3, face_count=1)

        centre = np.array([[4.0 / 3.0, 4.0 / 3.0, 0.0]])
        surface_uv = texturing.nearest_surface_uv(snapshot, centre)

        assert surface_uv is not None
        assert np.allclose(surface_uv[0], [1.0 / 3.0, 1.0 / 3.0], atol=1e-6)
        nearest_vertex_uv = snapshot.uv[texturing._nearest_source_indices(vertices, centre)][0]
        assert not np.allclose(surface_uv[0], nearest_vertex_uv, atol=0.1), (
            "the test is only meaningful if the two answers differ")

    def test_a_point_beyond_the_surface_lands_just_inside_the_nearest_edge(self):
        """Cleanup moves vertices off the original surface, so the closest point is usually not in the
        interior of any triangle -- decimation keeps the silhouette, so survivors sit on creases and
        borders. Two things then have to hold, and the second is what stops the dark flecks: the uv must
        come from the nearest EDGE rather than from a corner, and it must be nudged strictly INSIDE the
        triangle, because a uv exactly on a chart boundary reads whatever the packer put next door.
        """
        import numpy as np
        import trimesh

        vertices = np.array([[0.0, 0.0, 0.0], [4.0, 0.0, 0.0], [0.0, 4.0, 0.0]])
        atlas_size = 32
        snapshot = texturing.TextureSnapshot(
            uv=np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]]),
            material=trimesh.visual.material.SimpleMaterial(image=_atlas_image(atlas_size)),
            vertices=vertices, faces=np.array([[0, 1, 2]]), vertex_count=3, face_count=1)

        # Well outside the triangle, off the far side of the edge from vertex 0 to vertex 1.
        outside = np.array([[2.0, -5.0, 0.0]])
        surface_uv = texturing.nearest_surface_uv(snapshot, outside)[0]

        # A full texel, not half of one: the read is bilinear, so its 2x2 footprint spans a texel either
        # side of the coordinate and half a texel of inset still reaches past the boundary.
        texel = 1.0 / atlas_size
        edge_midpoint = np.array([0.5, 0.0])
        assert np.linalg.norm(surface_uv - edge_midpoint) <= texel + 1e-9, (
            f"{surface_uv} is further than a texel from the edge it should sit on")
        assert surface_uv[1] > 0.0, f"{surface_uv} is still on the boundary rather than inside it"

    def test_the_atlas_is_read_at_the_pre_cleanup_uvs(self, tmp_path):
        """Sampling has to happen where the parameterisation is still correct. Reading it after the
        cleanup would be reading a uv field the cleanup already invalidated."""
        import numpy as np

        original = _textured_box()
        snapshot = texturing.capture_texture(original)

        colours = texturing.sample_atlas_colors(snapshot.uv, snapshot.material)

        assert colours is not None
        assert len(colours) == snapshot.vertex_count
        assert colours.shape[1] == 4
        # This fixture's atlas is one flat colour, so the check that means something is that every
        # sample IS that colour -- a lookup reading outside the image, or off by a row, would not be.
        atlas = np.asarray(
            texturing.material_image(snapshot.material).convert("RGBA"), dtype=np.uint8)
        assert np.array_equal(np.unique(colours, axis=0), np.unique(atlas.reshape(-1, 4), axis=0))

    def test_sampling_is_nearest_texel_not_filtered(self, tmp_path):
        """Filtering between texels is what a renderer does with a contiguous chart, and these atlases
        are not contiguous. Every sampled colour must therefore be one that is actually IN the atlas."""
        import numpy as np

        original = _textured_box()
        snapshot = texturing.capture_texture(original)
        atlas = np.asarray(
            texturing.material_image(snapshot.material).convert("RGBA"), dtype=np.uint8)

        colours = texturing.sample_atlas_colors(snapshot.uv, snapshot.material)

        present = {tuple(c) for c in atlas.reshape(-1, 4)}
        for colour in colours:
            assert tuple(colour) in present

    def test_a_material_with_no_image_cannot_be_resampled(self, tmp_path):
        """Reported as unsamplable rather than guessed at, so the caller can fall back."""
        original = _textured_box()
        snapshot = texturing.capture_texture(original)

        assert texturing.sample_atlas_colors(snapshot.uv, None) is None


@pytest.mark.unit
@requires_trimesh
class TestRestoreTextureFallsBackToSidecars:
    @staticmethod
    def _changed_topology():
        original = _textured_box()
        return _rebuilt_without_texture(original, drop_faces=2), texturing.capture_texture(original)

    @staticmethod
    def _unsamplable(monkeypatch):
        """An atlas the resampler cannot read, and a uv reprojection that cannot run either.

        Breaking xatlas alone no longer reaches the fallback, because the resampling path does not use
        it -- which is the point of the change. Both have to fail for the sidecars to be the answer.
        """
        monkeypatch.setattr(texturing, "sample_atlas_colors", lambda uv, material: None)
        monkeypatch.setitem(sys.modules, "xatlas", None)

    def test_an_unreadable_atlas_ships_the_raw_texture_files_instead(self, tmp_path, monkeypatch):
        self._unsamplable(monkeypatch)
        source = tmp_path / "material_0.png"
        source.write_bytes(b"png")
        output_dir = tmp_path / "out"
        repaired, snapshot = self._changed_topology()

        restored, status = texturing.restore_texture(
            repaired, snapshot, (str(source),), str(output_dir))

        assert status == texturing.TEXTURE_SIDECARS_ONLY
        assert restored is repaired
        assert (output_dir / export.SIDECAR_SUBDIR / "material_0.png").is_file()

    def test_a_failing_unwrap_ships_the_raw_texture_files_instead(self, tmp_path, monkeypatch):
        def failing_unwrap(vertices, faces):
            raise RuntimeError("the chart packer ran out of space")

        monkeypatch.setattr(texturing, "sample_atlas_colors", lambda uv, material: None)
        monkeypatch.setattr(texturing, "_unwrap", failing_unwrap)
        source = tmp_path / "albedo.jpg"
        source.write_bytes(b"jpg")
        repaired, snapshot = self._changed_topology()

        _restored, status = texturing.restore_texture(
            repaired, snapshot, (str(source),), str(tmp_path / "out"))
        assert status == texturing.TEXTURE_SIDECARS_ONLY

    def test_a_failure_with_no_raw_texture_files_reports_no_texture(self, tmp_path, monkeypatch):
        self._unsamplable(monkeypatch)
        repaired, snapshot = self._changed_topology()

        restored, status = texturing.restore_texture(repaired, snapshot, (), str(tmp_path))
        assert status == texturing.TEXTURE_NONE
        assert restored is repaired

    def test_a_model_that_produced_no_texture_at_all_reports_no_texture(self, tmp_path):
        import trimesh

        mesh = trimesh.creation.box()
        restored, status = texturing.restore_texture(
            mesh, texturing.capture_texture(mesh), (), str(tmp_path))
        assert status == texturing.TEXTURE_NONE
        assert restored is mesh

    def test_a_model_with_raw_files_but_no_texture_visual_ships_the_files(self, tmp_path):
        import trimesh

        mesh = trimesh.creation.box()
        source = tmp_path / "texture.png"
        source.write_bytes(b"png")
        output_dir = tmp_path / "out"

        _restored, status = texturing.restore_texture(
            mesh, texturing.capture_texture(mesh), (str(source),), str(output_dir))
        assert status == texturing.TEXTURE_SIDECARS_ONLY
        assert (output_dir / export.SIDECAR_SUBDIR / "texture.png").is_file()


@pytest.mark.unit
class TestNearestSourceIndices:
    def test_each_target_takes_the_closest_source(self):
        import numpy as np

        source = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [0.0, 10.0, 0.0]])
        target = np.array([[9.5, 0.1, 0.0], [0.2, 0.0, 0.0], [0.0, 9.0, 0.5]])
        assert list(texturing._nearest_source_indices(source, target)) == [1, 0, 2]

    def test_an_exact_scan_past_the_comparison_limit_raises_rather_than_stalling(self, monkeypatch):
        """scipy is what makes the sampling tractable on a production mesh; without it the exact
        scan would allocate the whole distance matrix."""
        import numpy as np

        monkeypatch.setattr(texturing, "BRUTE_FORCE_COMPARISON_LIMIT", 4)
        monkeypatch.setitem(sys.modules, "scipy.spatial", None)
        with pytest.raises(texturing.TextureTransferError, match="scipy is not installed"):
            texturing._nearest_source_indices(np.zeros((3, 3)), np.zeros((3, 3)))


@pytest.mark.unit
@requires_trimesh
class TestGeneratedTextureStatus:
    def test_a_textured_generated_mesh_reports_preserved(self):
        assert texturing.generated_texture_status(_textured_box()) == texturing.TEXTURE_PRESERVED

    def test_an_untextured_generated_mesh_reports_no_texture(self):
        import trimesh

        assert texturing.generated_texture_status(
            trimesh.creation.box()) == texturing.TEXTURE_NONE


@pytest.mark.unit
@requires_trimesh
class TestFloodPreservedAtlas:
    """A preserved atlas the model baked incompletely still ships continuous colour.

    ``bl_bake`` clears its atlas to black and writes only the charts the unwrap reached, so a run cut
    short delivers an atlas that is part surface, part black. A renderer samples the black across chart
    edges as dark speckle over the whole surface -- the exact defect measured on the cat and horse
    refines. The delivery step floods those gaps from the nearest written texel before the mesh goes
    out, so an interrupted run degrades to a filled atlas rather than a holey one.
    """

    def _partial_atlas_box(self, size=32, written=8, colour=(200, 120, 60)):
        import numpy as np
        import trimesh
        from PIL import Image

        array = np.zeros((size, size, 3), dtype=np.uint8)
        array[:written, :written] = colour
        mesh = trimesh.creation.box()
        uv = np.linspace(0.0, 1.0, len(mesh.vertices) * 2).reshape(len(mesh.vertices), 2)
        mesh.visual = trimesh.visual.TextureVisuals(uv=uv, image=Image.fromarray(array))
        return mesh, colour

    def test_the_unwritten_texels_are_filled_from_the_written_colour(self):
        import numpy as np

        mesh, colour = self._partial_atlas_box()
        texturing.flood_preserved_atlas(mesh)

        filled = np.array(texturing.real_material_image(mesh.visual.material))
        # No black texel survives when there is a written colour to flood from, and the one colour the
        # atlas held is what every texel now carries.
        assert (filled.sum(axis=-1) > 0).all()
        assert np.array_equal(np.unique(filled.reshape(-1, 3), axis=0), np.array([colour]))

    def test_the_written_region_keeps_its_own_colour(self):
        import numpy as np

        mesh, colour = self._partial_atlas_box(colour=(10, 180, 40))
        texturing.flood_preserved_atlas(mesh)

        filled = np.array(texturing.real_material_image(mesh.visual.material))
        assert tuple(filled[0, 0]) == colour

    def test_a_fully_written_atlas_is_returned_unchanged(self):
        import numpy as np
        import trimesh
        from PIL import Image

        mesh = trimesh.creation.box()
        uv = np.linspace(0.0, 1.0, len(mesh.vertices) * 2).reshape(len(mesh.vertices), 2)
        mesh.visual = trimesh.visual.TextureVisuals(
            uv=uv, image=Image.new("RGB", (16, 16), (30, 40, 50)))
        before = np.array(texturing.real_material_image(mesh.visual.material))

        texturing.flood_preserved_atlas(mesh)

        after = np.array(texturing.real_material_image(mesh.visual.material))
        assert np.array_equal(before, after)

    def test_a_mesh_with_no_atlas_of_its_own_is_left_untouched(self):
        import trimesh

        mesh = trimesh.creation.box()
        assert texturing.flood_preserved_atlas(mesh) is mesh

    def test_an_opaque_black_rgba_atlas_is_flooded_by_its_colour(self):
        import numpy as np
        import trimesh
        from PIL import Image

        # The model bakes the space between its charts as OPAQUE black -- a full alpha channel with the
        # gaps written as (0, 0, 0, 255), not as transparency. Keyed by alpha the atlas reads as fully
        # written and no gap is found, which shipped one refine with a 61%-black atlas. The gaps must be
        # found by their colour instead.
        size, written, colour = 32, 8, (200, 120, 60)
        array = np.zeros((size, size, 4), dtype=np.uint8)
        array[..., 3] = 255
        array[:written, :written, :3] = colour
        mesh = trimesh.creation.box()
        uv = np.linspace(0.0, 1.0, len(mesh.vertices) * 2).reshape(len(mesh.vertices), 2)
        mesh.visual = trimesh.visual.TextureVisuals(uv=uv, image=Image.fromarray(array))

        texturing.flood_preserved_atlas(mesh)

        filled = np.array(texturing.real_material_image(mesh.visual.material).convert("RGB"))
        assert (filled.sum(axis=-1) > 0).all()
        assert np.array_equal(np.unique(filled.reshape(-1, 3), axis=0), np.array([colour]))

    def test_a_genuinely_transparent_atlas_is_keyed_by_its_alpha(self):
        import numpy as np
        import trimesh
        from PIL import Image

        # An atlas that carries real transparency keeps its alpha mask: the gaps are alpha 0, and their
        # colour is deliberately a non-black white so that dropping the alpha would misread them as
        # written. Keyed by alpha they flood to the one opaque colour; keyed by colour the white gaps
        # would survive untouched.
        size, written, colour = 32, 8, (10, 180, 40)
        array = np.full((size, size, 4), 255, dtype=np.uint8)
        array[..., 3] = 0
        array[:written, :written] = (*colour, 255)
        mesh = trimesh.creation.box()
        uv = np.linspace(0.0, 1.0, len(mesh.vertices) * 2).reshape(len(mesh.vertices), 2)
        mesh.visual = trimesh.visual.TextureVisuals(uv=uv, image=Image.fromarray(array))

        texturing.flood_preserved_atlas(mesh)

        filled = np.array(texturing.real_material_image(mesh.visual.material).convert("RGB"))
        assert np.array_equal(np.unique(filled.reshape(-1, 3), axis=0), np.array([colour]))


@pytest.mark.unit
@requires_trimesh
class TestCollapseAtlasToVertexColors:
    """A chart-packed atlas the model preserved is delivered as per-vertex colour instead of kept.

    The Blender agent bakes its texture over a dense auto-unwrap, so its atlas is thousands of small
    islands with unwritten background between them; a renderer samples across an island edge into that
    background and the surface reads as torn seams. Collapsing the atlas to per-vertex colour removes the
    seam -- glTF carries the colour array natively and a renderer interpolates it over the continuous
    surface. Unlike ``resample_atlas_to_vertex_colors``, which samples a pre-decimation snapshot by
    nearest vertex, this samples the mesh's OWN uv, so every vertex gets exactly the texel it names.
    """

    def _textured_box(self, size=32):
        import numpy as np
        import trimesh

        mesh = trimesh.creation.box()
        # A uv field spread across the atlas so a position-varying image yields many distinct colours,
        # which a flood to one colour or a misread could not produce. It stops short of 1.0, which the
        # sampler wraps to the far edge.
        uv = np.linspace(0.02, 0.98, len(mesh.vertices) * 2).reshape(len(mesh.vertices), 2)
        mesh.visual = trimesh.visual.TextureVisuals(uv=uv, image=_atlas_image(size))
        return mesh, uv

    def test_the_atlas_becomes_per_vertex_colour(self):
        import numpy as np
        import trimesh

        mesh, uv = self._textured_box()
        material = mesh.visual.material

        result = texturing.collapse_atlas_to_vertex_colors(mesh)

        assert result is not None
        out, status = result
        assert status == texturing.TEXTURE_RESAMPLED
        # A per-vertex ColorVisuals with no atlas left, one colour per vertex.
        assert isinstance(out.visual, trimesh.visual.ColorVisuals)
        assert out.visual.kind == "vertex"
        assert texturing.material_image(getattr(out.visual, "material", None)) is None
        assert len(out.visual.vertex_colors) == len(out.vertices)
        # Each vertex carries the atlas read a texel and a half inside each of its faces -- within the
        # gradient's step over that distance of the texel its own uv names -- not one flooded colour.
        expected = np.asarray(texturing.sample_atlas_colors(uv, material)).astype(int)
        got = np.asarray(out.visual.vertex_colors).astype(int)
        assert np.abs(got - expected).max() <= 20, np.abs(got - expected).max()
        assert len(np.unique(got, axis=0)) > 1

    def test_a_mesh_with_no_atlas_returns_none(self):
        import trimesh

        # A per-vertex or plain mesh has nothing to collapse; the caller falls back to the flood.
        assert texturing.collapse_atlas_to_vertex_colors(trimesh.creation.box()) is None


@pytest.mark.unit
class TestAtlasDeliveryIsScopedToTheAgent:
    """Only the agent's chart-packed atlas is collapsed to per-vertex colour; every other model keeps
    its own delivery.

    The collapse targets the one model whose atlas tears. A model that bakes a contiguous atlas -- Step1X-3D's
    texture stage writes its own 2048x2048 output -- would be REGRESSED by a collapse to per-vertex colour,
    and a model that ships per-vertex colour already (SAM3D) never enters the preserved-atlas branch at all.
    The postprocess gate reads ``ATLAS_IS_CHART_PACKED`` off the selected adapter, so this pins which models
    carry it: exactly the agent, so standalone runs of every other model are unaffected.
    """

    def test_only_the_blender_agent_is_chart_packed(self):
        import generate

        packed = {key for key, adapter in generate.MODEL_ADAPTERS.items()
                  if getattr(adapter, "ATLAS_IS_CHART_PACKED", False)}
        assert packed == {"blenderagent"}

    def test_the_contiguous_atlas_model_is_not_collapsed(self):
        import generate

        # Step1X-3D's atlas comes from its own texture stage and must ship intact; a True here would
        # collapse it to per-vertex colour and lose its resolution.
        assert generate.MODEL_ADAPTERS["step1x3d"].ATLAS_IS_CHART_PACKED is False


@pytest.mark.unit
@requires_trimesh
class TestWeldedForGltf:
    """The glTF export merges the uv-seam splits an atlas model leaves behind, but only off an atlas.

    A model that baked a texture split every vertex at a uv seam, so a collapsed-to-per-vertex mesh
    reaches export with several vertices per position and per-face normals. Merging coincident positions
    restores smooth shading and shrinks the GLB. A mesh that still carries an atlas keeps its seams split
    -- merging them would leave its uv array addressing positions that no longer exist -- so it is
    returned untouched.
    """

    def _split_vertex_mesh(self):
        """A mesh whose vertices are all distinct per face, as an atlas export leaves them."""
        import numpy as np
        import trimesh

        box = trimesh.creation.box()
        vertices = box.vertices[box.faces].reshape(-1, 3)
        faces = np.arange(len(vertices)).reshape(-1, 3)
        mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
        mesh.visual = trimesh.visual.ColorVisuals(
            mesh=mesh, vertex_colors=np.tile([200, 120, 60, 255], (len(vertices), 1)))
        return mesh

    def test_coincident_vertices_are_merged_and_geometry_is_preserved(self):
        welded = export.welded_for_gltf(self._split_vertex_mesh())

        assert len(welded.vertices) == 8  # a box has eight corners once its face splits are merged
        assert getattr(welded.visual, "vertex_colors", None) is not None
        assert len(welded.vertex_normals) == len(welded.vertices)

    def test_geometry_is_unchanged_by_the_weld(self):
        mesh = self._split_vertex_mesh()
        welded = export.welded_for_gltf(mesh)
        assert welded.volume == pytest.approx(mesh.volume)

    def test_a_mesh_with_an_atlas_is_returned_untouched(self):
        import numpy as np
        import trimesh

        mesh = trimesh.creation.box()
        uv = np.linspace(0.0, 1.0, len(mesh.vertices) * 2).reshape(len(mesh.vertices), 2)
        mesh.visual = trimesh.visual.TextureVisuals(uv=uv, image=_atlas_image(16))

        assert export.welded_for_gltf(mesh) is mesh


@pytest.mark.unit
class TestCopyTextureSidecars:
    def test_the_files_land_in_their_own_subdirectory(self, tmp_path):
        """Kept out of the output root so the OBJ exporter's own ``material_0.png`` cannot overwrite
        one of them."""
        source = tmp_path / "material_0.png"
        source.write_bytes(b"png")
        output_dir = tmp_path / "out"

        copied = export.copy_texture_sidecars((str(source),), str(output_dir))
        assert copied == [str(output_dir / export.SIDECAR_SUBDIR / "material_0.png")]
        assert (output_dir / export.SIDECAR_SUBDIR / "material_0.png").read_bytes() == b"png"

    def test_material_and_buffer_files_are_left_behind(self, tmp_path):
        """An MTL naming its maps by their pre-cleanup relative names, or a glTF buffer describing
        the pre-cleanup topology, resolves to nothing next to the exported set."""
        for name in ("material.mtl", "scene.bin"):
            (tmp_path / name).write_bytes(b"x")
        output_dir = tmp_path / "out"

        copied = export.copy_texture_sidecars(
            [str(tmp_path / "material.mtl"), str(tmp_path / "scene.bin")], str(output_dir))
        assert copied == []
        assert not output_dir.exists()

    def test_every_image_extension_the_adapters_collect_is_copied(self, tmp_path):
        names = ("a.png", "b.JPG", "c.jpeg", "d.ktx2")
        for name in names:
            (tmp_path / name).write_bytes(b"x")
        output_dir = tmp_path / "out"

        copied = export.copy_texture_sidecars(
            [str(tmp_path / name) for name in names], str(output_dir))
        assert len(copied) == len(names)

    def test_a_file_that_is_no_longer_present_is_skipped_rather_than_raising(self, tmp_path):
        present = tmp_path / "present.png"
        present.write_bytes(b"png")
        output_dir = tmp_path / "out"

        copied = export.copy_texture_sidecars(
            [str(present), str(tmp_path / "gone.png")], str(output_dir))
        assert copied == [str(output_dir / export.SIDECAR_SUBDIR / "present.png")]

    def test_nothing_to_copy_leaves_no_directory_behind(self, tmp_path):
        assert export.copy_texture_sidecars((), str(tmp_path / "out")) == []
        assert export.copy_texture_sidecars(("x.png",), "") == []
        assert not (tmp_path / "out").exists()


@pytest.mark.unit
class TestTextureStatusValues:
    def test_the_four_report_values_are_distinct(self):
        """The operator has to be able to tell a textured result from a silently grey one without
        downloading the mesh."""
        values = (texturing.TEXTURE_PRESERVED, texturing.TEXTURE_REPROJECTED,
                  texturing.TEXTURE_SIDECARS_ONLY, texturing.TEXTURE_NONE)
        assert len(set(values)) == 4
        assert values == ("preserved", "reprojected", "sidecarsOnly", "none")


@pytest.mark.unit
class TestTextureStatusReachesTheReport:
    def test_the_status_is_a_metadata_entry_of_its_own(self):
        document = export.build_metadata_document(
            {"IMAGE_TO_3D_TEXTURE_STATUS": texturing.TEXTURE_REPROJECTED})
        assert document["metadata"] == [{
            "metadataKey": "IMAGE_TO_3D_TEXTURE_STATUS",
            "metadataValue": "reprojected",
            "metadataValueType": "string",
        }]

#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Colour returned per vertex rather than as a uv atlas is carried across the cleanup.

SAM 3D Objects returns colour this way: the only configuration of its upstream pipeline that avoids
nvdiffrast produces a mesh with vertex colours instead of a baked texture. Every cleanup rung discards
a colour visual just as readily as it discards a uv field, so the colours are captured beforehand and
sampled back on afterwards by position.
"""

import numpy as np
import pytest
import trimesh

import texturing


def _colored_box(color_seed=0):
    """A box whose every vertex carries a different colour."""
    mesh = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
    colors = np.zeros((len(mesh.vertices), 4), dtype=np.uint8)
    for i in range(len(mesh.vertices)):
        colors[i] = (10 + i * 7 + color_seed, 40 + i * 5, 200 - i * 3, 255)
    mesh.visual = trimesh.visual.ColorVisuals(mesh=mesh, vertex_colors=colors)
    return mesh, colors


@pytest.mark.unit
class TestPerVertexColourIsRecognised:

    def test_a_mesh_with_distinct_vertex_colours_reports_them(self):
        mesh, colors = _colored_box()

        found = texturing.per_vertex_colors(mesh)

        assert found is not None
        assert np.array_equal(np.asarray(found)[:, :3], colors[:, :3])

    def test_a_uniformly_coloured_mesh_reports_none(self):
        # trimesh gives a mesh with no colour of its own a uniform default. Treating that as colour
        # would have the run report a texture for a grey mesh.
        mesh = trimesh.creation.box(extents=(1.0, 1.0, 1.0))

        assert texturing.per_vertex_colors(mesh) is None

    def test_an_explicitly_uniform_colour_still_reports_none(self):
        mesh = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
        uniform = np.tile(np.array([[120, 120, 120, 255]], dtype=np.uint8), (len(mesh.vertices), 1))
        mesh.visual = trimesh.visual.ColorVisuals(mesh=mesh, vertex_colors=uniform)

        assert texturing.per_vertex_colors(mesh) is None

    def test_a_textured_mesh_is_left_to_the_uv_path(self):
        mesh, _ = _colored_box()
        mesh.visual = mesh.visual.to_texture()

        assert texturing.per_vertex_colors(mesh) is None


@pytest.mark.unit
class TestCaptureFallsBackToVertexColour:

    def test_the_snapshot_carries_the_colours_and_the_positions_they_were_measured_over(self):
        mesh, colors = _colored_box()

        snapshot = texturing.capture_texture(mesh)

        assert not snapshot.has_texture()
        assert snapshot.has_vertex_colors()
        assert len(snapshot.vertex_colors) == len(mesh.vertices)
        assert np.allclose(snapshot.vertices, np.asarray(mesh.vertices, dtype=float))

    def test_a_mesh_with_neither_texture_nor_colour_captures_nothing(self):
        mesh = trimesh.creation.box(extents=(1.0, 1.0, 1.0))

        snapshot = texturing.capture_texture(mesh)

        assert not snapshot.has_texture()
        assert not snapshot.has_vertex_colors()


@pytest.mark.unit
class TestColourSurvivesTheCleanup:

    def test_an_unchanged_mesh_gets_its_own_colours_back(self):
        mesh, colors = _colored_box()
        snapshot = texturing.capture_texture(mesh)

        restored, status = texturing.restore_texture(mesh, snapshot)

        assert status == texturing.TEXTURE_FROM_VERTEX_COLORS
        # The colour stays per vertex: glTF carries a colour array natively and exactly, and a renderer
        # interpolates it across a triangle correctly. Converting it to a texture is the OBJ writer's
        # requirement alone, and happens at export.
        assert np.array_equal(np.asarray(texturing.per_vertex_colors(restored))[:, :3], colors[:, :3])

    def test_a_rebuilt_vertex_standing_on_a_captured_vertex_keeps_that_vertex_s_colour(self):
        # What a repair rung leaves behind: the same surface over a different vertex set, so an
        # index-based restore would paint one surface point with another's colour.
        mesh, colors = _colored_box()
        snapshot = texturing.capture_texture(mesh)

        cleaned = trimesh.Trimesh(vertices=np.asarray(mesh.vertices)[::-1],
                                  faces=trimesh.creation.box().faces, process=False)

        restored, status = texturing.restore_texture(cleaned, snapshot)

        assert status == texturing.TEXTURE_FROM_VERTEX_COLORS
        # Each reversed vertex sits exactly on a captured one, so its colour must be that vertex's.
        sampled = np.asarray(texturing.per_vertex_colors(restored))[:, :3]
        assert np.array_equal(sampled, colors[::-1][:, :3])

    def test_a_failed_transfer_degrades_instead_of_ending_the_run(self, monkeypatch):
        mesh, _ = _colored_box()
        snapshot = texturing.capture_texture(mesh)
        monkeypatch.setattr(texturing, "_restore_vertex_colors",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

        restored, status = texturing.restore_texture(mesh, snapshot)

        assert status == texturing.TEXTURE_NONE
        assert restored is mesh


@pytest.mark.unit
class TestTheStatusIsReportedHonestly:

    def test_an_uncleaned_vertex_coloured_mesh_reports_its_colour(self):
        mesh, _ = _colored_box()

        assert texturing.generated_texture_status(mesh) == texturing.TEXTURE_FROM_VERTEX_COLORS

    def test_an_uncleaned_grey_mesh_reports_none(self):
        mesh = trimesh.creation.box(extents=(1.0, 1.0, 1.0))

        assert texturing.generated_texture_status(mesh) == texturing.TEXTURE_NONE

    def test_vertex_colour_is_not_reported_as_a_preserved_texture(self):
        # "preserved" claims the model's own atlas came through. It did not: there was never one.
        mesh, _ = _colored_box()
        snapshot = texturing.capture_texture(mesh)

        _restored, status = texturing.restore_texture(mesh, snapshot)

        assert status != texturing.TEXTURE_PRESERVED


@pytest.mark.unit
class TestColourStaysPerVertexThroughTheGeometry:
    """Nothing converts the colour to a texture while geometry is still being added to the mesh.

    A colour array survives concatenation, a rescale and a print feature untouched, because it is indexed
    by vertex. Anything that turns it into an image indexed by uv has to be rebuilt by whatever comes
    next, and a rebuild resamples: packing before a print feature was attached left 26.6% of the model's
    vertices carrying a wrong colour, one of them off by 181 of 255.
    """

    def test_restore_leaves_the_colour_per_vertex(self):
        mesh, _colors = _colored_box()
        snapshot = texturing.capture_texture(mesh)

        restored, _status = texturing.restore_texture(mesh, snapshot)

        assert texturing.per_vertex_colors(restored) is not None
        assert getattr(restored.visual, "uv", None) is None

    def test_every_colour_survives_a_print_feature_exactly(self):
        """The defect this ordering fixes, measured end to end."""
        import printfeatures

        mesh, colors = _colored_box()
        vertex_count = len(mesh.vertices)
        snapshot = texturing.capture_texture(mesh)
        carried, _status = texturing.restore_texture(mesh, snapshot)

        featured, report = printfeatures.apply_features(
            carried, {"PRINT_FEATURES": "loop+base", "PRINT_FEATURE_JOIN": "auto",
                      "TARGET_SIZE_MM": 0})

        assert report["printFeatureJoin"] == "concat", "a coloured mesh must not take the union path"
        sampled = np.asarray(texturing.per_vertex_colors(featured))[:vertex_count, :3]
        assert np.array_equal(sampled, colors[:vertex_count, :3]), "every colour must be bit-exact"

    def test_auto_join_keeps_a_vertex_coloured_mesh_out_of_the_union_path(self):
        """A boolean union returns geometry alone, so auto choosing it would discard all the colour."""
        import printfeatures

        mesh, _colors = _colored_box()
        snapshot = texturing.capture_texture(mesh)
        carried, _status = texturing.restore_texture(mesh, snapshot)

        assert printfeatures.carries_colour(carried)
        assert printfeatures.resolve_join(carried, "auto") == "concat"


@pytest.mark.unit
class TestBakingColourIntoAnAtlas:
    """The atlas an OBJ needs is a PARAMETERISATION, not a lookup table.

    trimesh's ``to_texture`` produces the latter: one texel per distinct colour, in deduplication order,
    with no spatial relationship between neighbours. That is not something a renderer can sample, because
    a renderer interpolates uv across each triangle and reads per fragment rather than per vertex.
    Measured on a shipped mesh, 97.5% of triangles had their corners more than eight palette texels apart
    -- a median of 68 of a 184-wide palette -- so nearly every triangle interior read a line of unrelated
    entries: 43% of rendered pixels came out badly wrong, and the model rendered covered in crawling
    speckle. A bake scored 4.2% on the same measurement.
    """

    def test_a_baked_atlas_is_a_real_image_not_a_placeholder(self):
        # printfeatures and the report both decide "does this mesh have a texture" through
        # real_material_image, which rejects trimesh's stand-in image.
        mesh, _colors = _colored_box()

        baked = texturing.bake_vertex_colours(mesh, resolution=128)

        assert baked.visual.uv is not None
        assert len(baked.visual.uv) == len(baked.vertices)
        assert texturing.real_material_image(baked.visual.material) is not None

    def test_a_mesh_with_no_vertex_colour_bakes_nothing(self):
        """Reported as nothing to bake rather than as a grey atlas, so the caller ships what it has."""
        plain = trimesh.creation.box(extents=(1.0, 1.0, 1.0))

        assert texturing.bake_vertex_colours(plain, resolution=128) is None

    def test_a_face_reads_its_own_corner_colours_across_its_whole_interior(self):
        """The property a palette violates, and the reason the packed output rendered as speckle.

        Sampled at the uv centroid of each face, the atlas must return a colour lying between that face's
        three corner colours -- which is what interpolating a surface means. A palette returns whichever
        unrelated colour happens to be deduplicated into the texel the centroid lands on.
        """
        mesh, _colors = _colored_box()
        baked = texturing.bake_vertex_colours(mesh, resolution=256)

        uv = np.asarray(baked.visual.uv, dtype=float)
        atlas = np.asarray(texturing.material_image(baked.visual.material).convert("RGB"),
                           dtype=np.int16)
        corner_colours = texturing.sample_atlas_colors(uv, baked.visual.material)[:, :3].astype(np.int16)

        for face in np.asarray(baked.faces):
            centroid = uv[face].mean(axis=0)
            read = atlas[int(round((1.0 - centroid[1]) * (atlas.shape[0] - 1))),
                         int(round(centroid[0] * (atlas.shape[1] - 1)))]
            low = corner_colours[face].min(axis=0) - 2
            high = corner_colours[face].max(axis=0) + 2
            assert ((read >= low) & (read <= high)).all(), (
                f"the atlas reads {read} at the centroid of a face whose corners are "
                f"{corner_colours[face].tolist()}")

    def test_the_gaps_between_charts_are_flooded(self):
        """An unwritten texel next to a chart is what filtering pulls in at a chart edge, and it reads as
        dark speckle scattered over the model. The fixture's own colours are all far from black, so any
        black texel left in the atlas is an unfilled gap."""
        mesh, _colors = _colored_box()

        baked = texturing.bake_vertex_colours(mesh, resolution=128)

        atlas = np.asarray(texturing.material_image(baked.visual.material).convert("RGB"))
        assert not (atlas.sum(axis=2) == 0).any()


@pytest.mark.unit
class TestEachFormatGetsTheColourFormItCanRead:
    """The two formats want different things from the same colour, and the wiring is what decides.

    glTF carries a per-vertex colour array natively and round-trips it bit-exactly, so handing it a
    texture only loses fidelity. An OBJ carries a colour array only through the non-standard
    ``v x y z r g b`` extension that many readers ignore, so it gets an atlas and an MTL naming it.
    Nothing in either exporter reports the mismatch, which is why it is asserted here.
    """

    def test_the_glb_carries_the_colour_array_itself(self, tmp_path):
        import export

        mesh, colors = _colored_box()

        export.write_formats(mesh, str(tmp_path), "m", ("glb",),
                             textured_variant=lambda: texturing.bake_vertex_colours(mesh, 128))

        loaded = trimesh.load(str(tmp_path / "m.glb"), force="mesh", process=False)
        assert loaded.visual.kind == "vertex"
        assert np.array_equal(
            np.asarray(loaded.visual.vertex_colors)[:, :3], colors[:, :3]), "bit-exact or not at all"

    def test_the_obj_is_written_from_the_baked_atlas(self, tmp_path):
        import export

        mesh, _colors = _colored_box()

        export.write_formats(mesh, str(tmp_path), "m", ("obj",),
                             textured_variant=lambda: texturing.bake_vertex_colours(mesh, 128))

        written = {path.name for path in tmp_path.iterdir()}
        assert "m.obj" in written
        assert any(name.endswith(".mtl") for name in written), f"no MTL among {written}"
        assert any(name.endswith(".png") for name in written), f"no texture among {written}"

    def test_an_uncoloured_mesh_needs_no_variant_and_still_exports(self, tmp_path):
        """The bake returns None for a mesh with no colour, and the export goes ahead regardless."""
        import export

        plain = trimesh.creation.box(extents=(1.0, 1.0, 1.0))

        written = export.write_formats(
            plain, str(tmp_path), "m", ("obj", "glb"),
            textured_variant=lambda: texturing.bake_vertex_colours(plain, 128))

        assert len(written) == 2

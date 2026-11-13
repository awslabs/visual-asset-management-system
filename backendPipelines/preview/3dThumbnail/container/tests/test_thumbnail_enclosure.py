#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A model inside environment geometry authored to be seen from inside (a room or environment box, a
sky sphere or dome).

Rendered whole, the preview showed the outside of the box or the sphere. A loader whose parts carry a
reliable winding (glTF/GLB, OBJ, USD, STEP) now leaves such a layer out before it merges the parts,
when every check holds: the bounds screen, single-sided parts, nothing of it inside the content, at
least ``scene_parts.INWARD_SHARE`` of each of its parts' area facing the content and rays from the
content meeting it in nearly every direction. The model then loads, frames and renders exactly as from
a file holding it alone, texture included. A shell that is part of a product (it faces outward, or has a
wall thickness), a double-sided or open environment, and anything else in doubt render as before.

The screen and decision tests run anywhere; the loader and render tests need the real modules and run
inside the pipeline image (or a venv holding its requirements). The loader tests fail on the code before
this change by behaviour: the enclosure is still loaded and drawn.
"""

import numpy as np
import pytest

from conftest import requires_real
from preview_pipeline import scene_parts


def _box(lo, hi):
    return list(lo) + list(hi)


MODEL = _box((-0.5, 0.0, -0.5), (0.5, 1.0, 0.5))
ROOM = _box((-10.0, 0.0, -10.0), (10.0, 8.0, 10.0))

# A box's corners and its twelve triangles wound counter-clockwise seen from outside (normals outward).
_CORNERS = np.array([[x, y, z] for x in (0, 1) for y in (0, 1) for z in (0, 1)], dtype=np.float64)
_OUTWARD = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1],
                     [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])


def _box_part(bounds, inward=False, one_sided=True):
    lo, hi = np.asarray(bounds[:3], float), np.asarray(bounds[3:], float)
    triangles = _OUTWARD[:, ::-1] if inward else _OUTWARD
    return scene_parts.Part(lo + _CORNERS * (hi - lo), triangles, one_sided)


@pytest.mark.unit
class TestEnclosureScreen:
    def test_a_small_model_inside_a_closed_box_passes(self):
        lo, hi, kept, contact = scene_parts.enclosure_screen([MODEL, ROOM])
        np.testing.assert_allclose(np.concatenate([lo, hi]), MODEL)
        assert kept.tolist() == [True, False]
        assert contact == 1  # the model stands on the floor (ymin)

    def test_a_light_card_on_a_wall_is_environment_and_a_label_beside_the_model_is_kept(self):
        card = _box((-9.0, 2.0, 1.0), (-9.0, 4.0, 3.0))
        label = _box((0.55, 0.2, 0.0), (0.7, 0.4, 0.0))
        _, hi, kept, _ = scene_parts.enclosure_screen([MODEL, ROOM, card, label])
        assert kept.tolist() == [True, False, False, True]
        assert hi[0] == pytest.approx(0.7)

    def test_a_ground_plane_under_a_model_is_no_enclosure(self):
        assert scene_parts.enclosure_screen([MODEL, _box((-25, 0, -25), (25, 0, 25))]) is None

    def test_the_size_rule_holds_on_every_axis(self):
        """A pipe 100 long and 2 across around a 1 m valve is 100 times the valve on one axis only."""
        assert scene_parts.enclosure_screen([MODEL, _box((-50, -0.5, -1), (50, 1.5, 1))]) is None

    def test_content_touching_two_faces_or_spread_over_the_room_is_no_enclosure(self):
        corner = _box((-10.0, 0.0, -10.0), (-9.0, 1.0, -9.0))
        assert scene_parts.enclosure_screen([corner, ROOM]) is None
        spread = [_box((-8, 0, -8), (-6, 1, -6)), _box((6, 0, 6), (8, 2, 8))]
        assert scene_parts.enclosure_screen(spread + [ROOM]) is None


@pytest.mark.unit
class TestEnvironmentKept:
    def test_an_inward_single_sided_box_around_the_model_is_left_out(self, caplog):
        with caplog.at_level("INFO"):
            keep = scene_parts.environment_kept([_box_part(MODEL), _box_part(ROOM, inward=True)])
        assert keep.tolist() == [True, False]
        assert "Enclosure left out of the preview: 1 of 2 parts facing inward" in caplog.text

    def test_an_outward_box_is_part_of_the_product(self):
        """A building exterior, a closed housing or a crate faces outward: nothing is left out."""
        assert scene_parts.environment_kept([_box_part(MODEL), _box_part(ROOM)]) is None

    def test_a_shell_with_a_wall_thickness_is_part_of_the_product(self):
        """Outer faces outward and inner faces inward: half the area faces the content."""
        wall = [_box_part(ROOM), _box_part(_box((-9.8, 0.2, -9.8), (9.8, 7.8, 9.8)), inward=True)]
        assert scene_parts.environment_kept([_box_part(MODEL)] + wall) is None
        joined = scene_parts.Part(np.vstack([wall[0].vertices, wall[1].vertices]),
                                  np.vstack([wall[0].triangles, wall[1].triangles + 8]), True)
        assert scene_parts.environment_kept([_box_part(MODEL), joined]) is None

    def test_a_double_sided_or_unknown_environment_is_kept(self):
        assert scene_parts.environment_kept([_box_part(MODEL), _box_part(ROOM, True, one_sided=False)]) is None

    def test_a_reversed_winding_is_honoured(self):
        """A USD leftHanded prim (or a mirroring transform) whose triangles wind outward faces inward."""
        part = _box_part(ROOM)
        part.reversed_winding = True
        assert scene_parts.environment_kept([_box_part(MODEL), part]).tolist() == [True, False]

    def test_an_inward_backdrop_open_to_the_camera_is_kept(self):
        """Three inward walls of a studio corner: rays from the model leave through the open sides."""
        room = _box_part(ROOM, inward=True)
        lo = np.asarray(ROOM[:3])
        centres = room.vertices[room.triangles].mean(axis=1)
        keep = (centres[:, 0] < lo[0] + 1e-9) | (centres[:, 1] < lo[1] + 1e-9) | (centres[:, 2] < lo[2] + 1e-9)
        corner = scene_parts.Part(room.vertices, room.triangles[keep], True)
        assert scene_parts.environment_kept([_box_part(MODEL), corner]) is None

    def test_environment_reaching_into_the_model_is_kept(self):
        rod = scene_parts.Part(np.array([[-10, 0.5, 0], [10, 0.5, 0], [10, 0.5, 0.01]]), [[0, 1, 2]], True)
        room = _box_part(ROOM, inward=True)
        joined = scene_parts.Part(np.vstack([room.vertices, rod.vertices, [[0.0, 0.5, 0.0]]]),
                                  np.vstack([room.triangles, rod.triangles + 8, [[8, 9, 11]]]), True)
        assert scene_parts.environment_kept([_box_part(MODEL), joined]) is None

    def test_nested_inward_layers_are_both_left_out(self):
        sky = _box_part(_box((-200, -100, -200), (200, 300, 200)), inward=True)
        keep = scene_parts.environment_kept([_box_part(MODEL), _box_part(ROOM, inward=True), sky])
        assert keep.tolist() == [True, False, False]

    def test_an_inward_sky_around_an_outward_room_is_drawn_as_before(self):
        """Leaving the sky out would show the outside of the room: the model is no easier to see."""
        sky = _box_part(_box((-200, -100, -200), (200, 300, 200)), inward=True)
        assert scene_parts.environment_kept([_box_part(MODEL), _box_part(ROOM), sky]) is None

    def test_an_environment_over_the_triangle_cap_is_kept(self, monkeypatch):
        monkeypatch.setattr(scene_parts, "MAX_ENVIRONMENT_TRIANGLES", 11)
        assert scene_parts.environment_kept([_box_part(MODEL), _box_part(ROOM, inward=True)]) is None


MODEL_RGB = (230, 40, 40)


def _model_mesh():
    """A detailed 1 m ball standing on y = 0 and a thin ring around it."""
    import trimesh

    ball = trimesh.creation.icosphere(subdivisions=4, radius=0.4)
    ball.apply_translation((0, 0.5, 0))
    ring = trimesh.creation.torus(major_radius=0.45, minor_radius=0.03)
    ring.apply_translation((0, 0.5, 0))
    return [ball, ring]


def _environment_box(bounds=ROOM, inward=True):
    import trimesh

    box = trimesh.creation.box(bounds=[bounds[:3], bounds[3:]])
    if inward:
        box.invert()
    return box


def _write_glb(path, meshes, double_sided=None):
    import trimesh

    scene = trimesh.Scene()
    for index, mesh in enumerate(meshes):
        if double_sided is not None and index == len(meshes) - 1:
            mesh = mesh.copy()
            mesh.visual = trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(
                name="environment", baseColorFactor=[200, 200, 200, 255], doubleSided=double_sided))
        scene.add_geometry(mesh, geom_name=f"part{index}")
    scene.export(str(path))
    return str(path)


def _load(path):
    from preview_pipeline import core

    ext = "." + str(path).rsplit(".", 1)[1].lower()
    return core._normalize_up_axis(core._load_file(str(path), ext), ext)


def _same(left, right):
    np.testing.assert_array_equal(np.asarray(left.points), np.asarray(right.points))
    np.testing.assert_array_equal(np.asarray(left.faces), np.asarray(right.faces))


@pytest.mark.unit
@requires_real("pyvista", "vtk", "trimesh")
class TestGlbLoad:
    def test_a_model_inside_an_inward_environment_box_loads_as_the_model_alone(self, tmp_path):
        alone = _load(_write_glb(tmp_path / "alone.glb", _model_mesh()))
        inside = _load(_write_glb(tmp_path / "inside.glb", _model_mesh() + [_environment_box()], False))
        _same(inside, alone)

    def test_nested_inward_sky_and_room_load_as_the_model_alone(self, tmp_path):
        sky = _environment_box(_box((-200, -100, -200), (200, 300, 200)))
        alone = _load(_write_glb(tmp_path / "alone.glb", _model_mesh()))
        nested = _load(_write_glb(tmp_path / "nested.glb", _model_mesh() + [_environment_box(), sky]))
        _same(nested, alone)

    def test_a_textured_model_inside_an_environment_box_keeps_its_texture(self, tmp_path):
        import trimesh
        from PIL import Image

        model = trimesh.creation.icosphere(subdivisions=3, radius=0.4)
        model.apply_translation((0, 0.5, 0))
        image = Image.fromarray(np.random.default_rng(0).integers(0, 255, (16, 16, 3), dtype=np.uint8))
        uv = np.random.default_rng(1).random((len(model.vertices), 2))
        model.visual = trimesh.visual.TextureVisuals(uv=uv, image=image)
        alone = _load(_write_glb(tmp_path / "alone.glb", [model]))
        inside = _load(_write_glb(tmp_path / "inside.glb", [model, _environment_box()], False))
        assert getattr(alone, "_preview_texture", None) is not None
        assert getattr(inside, "_preview_texture", None) is not None
        _same(inside, alone)
        np.testing.assert_array_equal(inside.active_texture_coordinates, alone.active_texture_coordinates)

    @pytest.mark.parametrize("case", ["outward", "double-sided", "thick wall"])
    def test_product_shells_and_double_sided_environments_load_whole(self, tmp_path, case):
        if case == "outward":
            meshes, flag = _model_mesh() + [_environment_box(inward=False)], False
        elif case == "double-sided":
            meshes, flag = _model_mesh() + [_environment_box()], True
        else:
            import trimesh

            shell = trimesh.util.concatenate([_environment_box(inward=False), _environment_box(
                _box((-9.8, 0.2, -9.8), (9.8, 7.8, 9.8)))])
            meshes, flag = _model_mesh() + [shell], False
        loaded = _load(_write_glb(tmp_path / "scene.glb", meshes, flag))
        assert loaded.n_points == sum(len(m.vertices) for m in meshes)


@pytest.mark.unit
@requires_real("pyvista", "vtk", "trimesh")
class TestRender:
    def test_every_orbit_frame_matches_the_model_rendered_alone(self, tmp_path):
        from preview_pipeline import renderer

        alone = _load(_write_glb(tmp_path / "alone.glb", _model_mesh()))
        inside = _load(_write_glb(tmp_path / "inside.glb", _model_mesh() + [_environment_box()], False))
        for treated, reference in zip(renderer.generate_rotating_frames(inside, n_frames=12, resolution=(160, 120)),
                                      renderer.generate_rotating_frames(alone, n_frames=12, resolution=(160, 120))):
            assert 0.05 < (reference[..., 3] > 0).mean() < 0.95
            assert np.array_equal(treated, reference)


_USD_MODEL = """
    def Sphere "Model"
    {
        double radius = 0.5
        double3 xformOp:translate = (0, 0.5, 0)
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }
"""


def _usd_stage(tmp_path, name, environment=""):
    path = tmp_path / name
    path.write_text(f'#usda 1.0\n(\n    upAxis = "Y"\n)\ndef Xform "World"\n{{\n{_USD_MODEL}{environment}}}\n')
    return path


def _usd_box(extra=""):
    return ('    def Cube "Environment"\n    {\n        double size = 20\n' + extra
            + '        double3 xformOp:translate = (0, 4, 0)\n'
            '        uniform token[] xformOpOrder = ["xformOp:translate"]\n'
            '    }\n')


@pytest.mark.unit
@requires_real("pyvista", "vtk", "pxr")
class TestUsdLoad:
    def test_a_left_handed_environment_cube_is_left_out(self, tmp_path):
        alone = _load(_usd_stage(tmp_path, "alone.usda"))
        left_handed = _usd_box('        uniform token orientation = "leftHanded"\n')
        inside = _load(_usd_stage(tmp_path, "inside.usda", left_handed))
        _same(inside, alone)

    @pytest.mark.parametrize("extra", ["", '        uniform token orientation = "leftHanded"\n'
                                           '        uniform bool doubleSided = 1\n'])
    def test_an_outward_or_double_sided_cube_is_kept(self, tmp_path, extra):
        alone = _load(_usd_stage(tmp_path, "alone.usda"))
        inside = _load(_usd_stage(tmp_path, "inside.usda", _usd_box(extra)))
        assert inside.n_points > alone.n_points


@pytest.mark.unit
@requires_real("pyvista", "vtk", "cadquery")
class TestStepLoad:
    def test_a_hollow_housing_around_a_bracket_loads_whole(self, tmp_path):
        """A solid's faces point out of the material, so a housing with a wall faces both ways."""
        import cadquery as cq

        assembly = cq.Assembly()
        assembly.add(cq.Workplane().box(150, 150, 150).shell(-3), name="housing")
        assembly.add(cq.Workplane().box(12, 8, 3), name="bracket")
        path = tmp_path / "housing.step"
        assembly.save(str(path))
        assert np.ptp(np.asarray(_load(path).points), axis=0).max() > 100

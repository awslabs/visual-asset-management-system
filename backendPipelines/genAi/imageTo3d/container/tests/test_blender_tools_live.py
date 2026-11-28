# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The Blender-side tools, run under a real Blender on the kind of scene the agent actually builds.

Skipped where no Blender is installed. Where one is, this is the check that would have caught every tool
defect the first live agent runs found, in twenty seconds on a laptop instead of an hour on a GPU:

- ``bl_solidify.py`` referenced a mesh attribute Blender 4.5 no longer has and died on its first line of
  work -- the mandatory step of the agent's method, broken in the image, with nothing static to notice;
- ``bl_export.py`` compared polygon counts across a glTF round trip, and glTF triangulates, so every quad
  mesh -- which is what a voxel remesh produces -- was reported as not the scene it came from;
- ``bl_export.py`` counted zero images in a file that carried one, because a packed image re-imported
  from glTF loads lazily and ``has_data`` is False until touched; the agent reported this one itself;
- ``bl_render.py`` handed Blender a bare relative path, which Blender resolved against its own notion of
  the current directory and wrote elsewhere while printing success.

The scene is a figure built the way the first agent run built one: a dozen closed primitives with
different materials, none joined. The assertions are the print gate's terms -- one body, closed, an image
texture -- and the export checker's own verdict.
"""

import glob
import json
import os
import shutil
import subprocess
import sys
import textwrap

import numpy as np
import pytest

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS_DIR = os.path.join(_CONTAINER_DIR, "blenderagent_tools")

_BUILD_PARTS = textwrap.dedent('''
    """A figure from closed primitives, nothing joined, feet on Z=0, facing -Y."""
    import os
    import bpy

    bpy.ops.wm.read_homefile(use_empty=True)


    def material(name, rgb):
        m = bpy.data.materials.new(name)
        m.use_nodes = True
        bsdf = next(n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
        return m


    def add(op, mat, name, **kw):
        op(**kw)
        obj = bpy.context.active_object
        obj.name = name
        obj.data.materials.append(mat)
        return obj


    skin, hair = material("skin", (0.85, 0.65, 0.5)), material("hair", (0.25, 0.12, 0.05))
    top, jeans, shoe = material("top", (0.02, 0.02, 0.02)), material("jeans", (0.1, 0.2, 0.5)), material("shoe", (0.9, 0.9, 0.9))
    add(bpy.ops.mesh.primitive_uv_sphere_add, skin, "head", radius=0.11, location=(0, 0, 1.58))
    add(bpy.ops.mesh.primitive_uv_sphere_add, hair, "hair", radius=0.115, location=(0, 0.02, 1.61))
    add(bpy.ops.mesh.primitive_cylinder_add, top, "torso", radius=0.17, depth=0.55, location=(0, 0, 1.2))
    add(bpy.ops.mesh.primitive_cylinder_add, skin, "neck", radius=0.05, depth=0.12, location=(0, 0, 1.5))
    for side, x in (("l", -0.23), ("r", 0.23)):
        add(bpy.ops.mesh.primitive_cylinder_add, top, f"sleeve_{side}", radius=0.055, depth=0.25, location=(x, 0, 1.35))
        add(bpy.ops.mesh.primitive_cylinder_add, skin, f"arm_{side}", radius=0.045, depth=0.4, location=(x, 0, 1.03))
        add(bpy.ops.mesh.primitive_cylinder_add, jeans, f"leg_{side}", radius=0.085, depth=0.85, location=(x * 0.4, 0, 0.5))
        add(bpy.ops.mesh.primitive_cube_add, shoe, f"shoe_{side}", size=0.1, location=(x * 0.4, -0.04, 0.05))
        bpy.context.active_object.scale = (1.0, 1.6, 0.6)
    if os.environ.get("BUILD_FLOATING_PART") == "1":
        # A hat hovering a centimetre above the hair: closed, watertight, and touching nothing.
        add(bpy.ops.mesh.primitive_cylinder_add, top, "hat", radius=0.12, depth=0.06, location=(0, 0, 1.79))
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath("scene.blend"))
    print("parts:", len([o for o in bpy.data.objects if o.type == "MESH"]))
''')

_BUILD_ONE_CLOSED_BODY = textwrap.dedent('''
    """One closed, manifold body -- what a reconstruction hands the refine run before it is solidified."""
    import os
    import bpy

    bpy.ops.wm.read_homefile(use_empty=True)
    mat = bpy.data.materials.new("coat")
    mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Base Color"].default_value = (0.85, 0.4, 0.1, 1.0)
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5, segments=32, ring_count=16, location=(0, 0, 0.5))
    body = bpy.context.active_object
    body.name = "reconstruction"
    body.data.materials.append(mat)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath("scene.blend"))
    print("faces:", len(body.data.polygons))
''')


def _find_blender():
    explicit = os.environ.get("BLENDER_EXE")
    if explicit and os.path.isfile(explicit):
        return explicit
    on_path = shutil.which("blender")
    if on_path:
        return on_path
    if sys.platform == "win32":
        candidates = sorted(glob.glob(r"C:\Program Files\Blender Foundation\Blender 4.*\blender.exe"))
        if candidates:
            return candidates[-1]
    return None


_BLENDER = _find_blender()
pytestmark = [pytest.mark.integration,
              pytest.mark.skipif(_BLENDER is None, reason="no Blender installation found")]


@pytest.fixture(scope="module")
def workspace(tmp_path_factory):
    root = tmp_path_factory.mktemp("agent_ws")
    shutil.copytree(_TOOLS_DIR, root / "tools", ignore=shutil.ignore_patterns("__pycache__"))
    for sub in ("scripts", "renders", "output", "textures"):
        (root / sub).mkdir()
    (root / "scripts" / "build_parts.py").write_text(_BUILD_PARTS, encoding="utf-8")
    return root


def _blender(workspace, *args, blend=None, expect_failure=False, extra_env=None):
    """Run Blender the way the agent is told to, inside the workspace, and fail on a non-zero exit."""
    command = [_BLENDER, "-b"] + ([blend] if blend else []) + ["--factory-startup", "--python-exit-code", "1"] + list(args)
    env = dict(os.environ, AGENT_WORKSPACE=str(workspace), PYTHONUTF8="1", AGENT_RENDER_ENGINE="BLENDER_WORKBENCH",
               **(extra_env or {}))
    done = subprocess.run(command, cwd=str(workspace), env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=600)
    if expect_failure:
        assert done.returncode != 0, f"{' '.join(command[-4:])} was expected to fail and exited 0:\n{done.stdout[-2000:]}"
    else:
        assert done.returncode == 0, f"{' '.join(command[-4:])} exited {done.returncode}:\n{done.stdout[-3000:]}\n{done.stderr[-2000:]}"
    return done.stdout + done.stderr


def test_the_agents_tool_chain_turns_many_parts_into_one_printable_textured_body(workspace):
    out = _blender(workspace, "-P", "scripts/build_parts.py")
    assert "parts: 12" in out

    out = _blender(workspace, "-P", "tools/bl_solidify.py", "--", "--save", "scene.blend", blend="scene.blend")
    assert "boundary edges 0, non-manifold edges 0" in out, out[-1500:]
    assert (workspace / "textures" / "solid_basecolor.png").stat().st_size > 10_000

    _blender(workspace, "-P", "tools/bl_inspect.py", "--", "--json", "renders/inspect.json", blend="scene.blend")
    inspect = json.loads((workspace / "renders" / "inspect.json").read_text(encoding="utf-8"))
    assert inspect["meshCount"] == 1, "solidify must leave exactly one mesh"
    (only,) = inspect["objects"]
    assert only["watertight"] is True
    assert only["bodies"] == 1 and only["oneBody"] is True
    assert only["hasImageTexture"] is True
    assert inspect["printable"] is True

    _blender(workspace, "-P", "tools/bl_export.py", "--", "--out", "output/model.glb", blend="scene.blend")
    verdict = json.loads((workspace / "output" / "model_export_check.json").read_text(encoding="utf-8"))
    assert verdict["facesMatch"] is True, "a quad mesh must survive the glTF round trip by triangle count"
    assert verdict["hasImages"] is True, "the re-imported file carries a packed image and must say so"
    assert verdict["hasMaterials"] is True
    assert (workspace / "output" / "model.glb").stat().st_size > 100_000

    _blender(workspace, "-P", "tools/bl_render.py", "--", "--out", "renders/final", "--views", "front,threequarter",
             "--size", "256", blend="scene.blend")
    for view in ("front", "threequarter"):
        assert (workspace / "renders" / f"final_{view}.png").is_file(), f"{view} render did not land in the workspace"
    report = json.loads((workspace / "renders" / "final_report.json").read_text(encoding="utf-8"))
    assert report["meshCount"] == 1
    # The render report names what was rendered, in a word the agent has to copy into its notes.
    for view in ("front", "threequarter"):
        summary = report["colours"][view]
        assert summary["meanHex"].startswith("#") and len(summary["meanHex"]) == 7
        assert summary["colour"] in ("brown", "grey", "orange", "black", "blue", "cyan", "red", "yellow", "white")
        assert 0.05 < summary["coverage"] < 0.9
    assert report["referenceColours"] == {}, "the fixture has no refs/ so nothing to compare against"


def test_solidify_keeps_an_already_closed_body_instead_of_remeshing_it(tmp_path):
    """A reconstruction handed to the refine run is already one closed body. A voxel remesh would only
    coarsen it and can bridge nearby features into tunnels, so solidify keeps the topology as it is,
    still bakes the colour onto it, and leaves one printable body."""
    shutil.copytree(_TOOLS_DIR, tmp_path / "tools", ignore=shutil.ignore_patterns("__pycache__"))
    for sub in ("scripts", "renders", "output", "textures"):
        (tmp_path / sub).mkdir()
    (tmp_path / "scripts" / "build_one.py").write_text(_BUILD_ONE_CLOSED_BODY, encoding="utf-8")
    out = _blender(tmp_path, "-P", "scripts/build_one.py")
    faces_before = int([line for line in out.splitlines() if line.startswith("faces:")][0].split()[1])

    out = _blender(tmp_path, "-P", "tools/bl_solidify.py", "--", "--save", "scene.blend", blend="scene.blend")
    assert "not remeshed" in out, out[-1500:]
    kept = [line for line in out.splitlines() if line.startswith("already one closed body")][0]
    assert int(kept.split("(")[1].split(" faces")[0]) == faces_before, "the kept body must keep its own faces"
    assert "boundary edges 0, non-manifold edges 0" in out, out[-1500:]
    assert (tmp_path / "textures" / "solid_basecolor.png").stat().st_size > 10_000

    _blender(tmp_path, "-P", "tools/bl_inspect.py", "--", "--json", "renders/inspect.json", blend="scene.blend")
    inspect = json.loads((tmp_path / "renders" / "inspect.json").read_text(encoding="utf-8"))
    assert inspect["meshCount"] == 1 and inspect["printable"] is True
    (only,) = inspect["objects"]
    assert only["watertight"] is True and only["oneBody"] is True and only["hasImageTexture"] is True


def test_a_hidden_side_is_filled_from_a_picture_of_it_and_only_where_that_camera_saw(workspace):
    """The atlas-space inpaint came back grey-brown: an image model completes pictures, not uv islands. The
    view tool renders the current texture from a side, masks what the photograph never covered, and bakes a
    completed picture back onto exactly the faces that view saw -- recorded on the mesh, so a second pass
    from the same side finds nothing left."""
    import numpy as np
    from PIL import Image

    out = _blender(workspace, "-P", "tools/bl_view_fill.py", "--", "prepare", "--view", "back", "--size", "256",
                   blend="scene.blend")
    assert "faces to fill" in out, out[-1500:]
    render = Image.open(workspace / "textures" / "view_back.png").convert("RGB")
    assert render.size == (256, 256)
    mask = np.asarray(Image.open(workspace / "textures" / "view_back_mask.png").convert("L")) > 127
    assert 0.02 < mask.mean() < 0.9, f"the mask covers the figure's back, not the frame: {mask.mean():.2f}"
    record = json.loads((workspace / "textures" / "view_back.json").read_text(encoding="utf-8"))
    assert record["view"] == "back" and record["facesToFill"] > 0

    # Stand in for the image model: paint the masked pixels magenta.
    filled = np.asarray(render).copy()
    filled[mask] = (255, 0, 255)
    Image.fromarray(filled).save(workspace / "textures" / "view_back_filled.png")
    before = np.asarray(Image.open(workspace / "textures" / "solid_basecolor.png").convert("RGB")).astype(int)

    out = _blender(workspace, "-P", "tools/bl_view_fill.py", "--", "apply", "--view", "back",
                   "--image", "textures/view_back_filled.png", "--save", "scene.blend",
                   "--atlas", "textures/view_fill_atlas.png", blend="scene.blend")
    assert "filled" in out and "remain unfilled" in out, out[-1500:]
    after = np.asarray(Image.open(workspace / "textures" / "view_fill_atlas.png").convert("RGB")).astype(int)
    assert after.shape == before.shape
    magenta = (after[..., 0] > 200) & (after[..., 1] < 60) & (after[..., 2] > 200)
    assert magenta.mean() > 0.01, f"the back's texels took the picture: {magenta.mean():.3f}"
    untouched = (np.abs(after - before) <= 8).all(axis=2)
    assert (untouched | magenta).mean() > 0.97, "texels that camera did not see were left as they were"

    out = _blender(workspace, "-P", "tools/bl_view_fill.py", "--", "prepare", "--view", "back", "--size", "256",
                   blend="scene.blend")
    assert "Nothing to fill from this view" in out, out[-1500:]
    _blender(workspace, "-P", "tools/bl_export.py", "--", "--out", "output/model.glb", blend="scene.blend")
    verdict = json.loads((workspace / "output" / "model_export_check.json").read_text(encoding="utf-8"))
    assert verdict["hasImages"] is True, "the filled atlas travels with the export"


def test_colour_words_are_what_a_person_would_say(workspace):
    """The refine run described a blue cat as ginger for three iterations. The word the tool prints for a
    render is the check the agent is told to copy, so the words have to be right for the plain cases."""
    out = _blender(workspace, "--python-expr",
                   "import sys; sys.path.insert(0, 'tools'); import bl_common as c; "
                   "print('WORDS', c.name_colour((0.9, 0.45, 0.1)), c.name_colour((0.2, 0.5, 0.9)), "
                   "c.name_colour((0.02, 0.02, 0.02)), c.name_colour((0.95, 0.95, 0.95)), "
                   "c.name_colour((0.5, 0.5, 0.5)), c.name_colour((0.35, 0.18, 0.06)), c.name_colour((0.1, 0.6, 0.2)))")
    words = [line for line in out.splitlines() if line.startswith("WORDS")][0].split()[1:]
    assert words == ["orange", "blue", "black", "white", "grey", "brown", "green"], words


def test_solidify_refuses_a_scene_whose_parts_do_not_touch(tmp_path):
    """The refine run built a cat whose ears, paws and tail segments floated near the body: every part
    closed, so the inspection said watertight, and the voxel fuse left them as ten pieces, which is what
    the print gate saw. Fusing cannot bridge a gap. The tool now counts bodies, refuses to save a result
    that is not one piece, and names the detached parts so the agent can sink them into their neighbours."""
    shutil.copytree(_TOOLS_DIR, tmp_path / "tools", ignore=shutil.ignore_patterns("__pycache__"))
    for sub in ("scripts", "renders", "output", "textures"):
        (tmp_path / sub).mkdir()
    (tmp_path / "scripts" / "build_parts.py").write_text(_BUILD_PARTS, encoding="utf-8")
    out = _blender(tmp_path, "-P", "scripts/build_parts.py", extra_env={"BUILD_FLOATING_PART": "1"})
    assert "parts: 13" in out
    before = (tmp_path / "scene.blend").stat().st_mtime_ns

    out = _blender(tmp_path, "-P", "tools/bl_solidify.py", "--", "--save", "scene.blend", blend="scene.blend",
                   expect_failure=True)
    assert "2 SEPARATE BODIES" in out, out[-2000:]
    assert "OVERLAP" in out
    assert (tmp_path / "scene.blend").stat().st_mtime_ns == before, "a refused fuse must leave the scene untouched"
    assert not (tmp_path / "textures" / "solid_basecolor.png").exists(), "no bake before the body check passes"

    # And the inspection of the untouched parts scene says so in the print gate's own terms.
    _blender(tmp_path, "-P", "tools/bl_inspect.py", "--", "--json", "renders/inspect.json", blend="scene.blend")
    inspect = json.loads((tmp_path / "renders" / "inspect.json").read_text(encoding="utf-8"))
    assert inspect["meshCount"] == 13 and inspect["bodies"] == 13
    assert inspect["printable"] is False
    assert all(entry["watertight"] for entry in inspect["objects"]), "closed parts are exactly the trap"


def _synthetic_cutout(path):
    """A person-shaped RGBA cutout: round head, black top, blue trousers, white shoes, transparent elsewhere."""
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (400, 900), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((150, 40, 250, 150), fill=(220, 170, 130, 255))          # head
    draw.rectangle((190, 140, 210, 175), fill=(220, 170, 130, 255))       # neck
    draw.rectangle((120, 170, 280, 420), fill=(20, 20, 20, 255))          # top
    draw.rectangle((70, 180, 118, 460), fill=(220, 170, 130, 255))        # left arm
    draw.rectangle((282, 180, 330, 460), fill=(220, 170, 130, 255))       # right arm
    draw.rectangle((125, 415, 195, 820), fill=(60, 90, 150, 255))         # left leg
    draw.rectangle((205, 415, 275, 820), fill=(60, 90, 150, 255))         # right leg
    draw.rectangle((110, 815, 200, 870), fill=(235, 235, 235, 255))       # left shoe
    draw.rectangle((200, 815, 290, 870), fill=(235, 235, 235, 255))       # right shoe
    image.save(path)


def test_the_silhouette_base_reads_the_outline_off_the_photograph_and_the_gate_tool_agrees(tmp_path):
    """Six builds from primitives scored 3 of 10 for silhouette. The base body takes the outline and the
    colours from the cutout itself, and the gate tool -- the pipeline's own gate and silhouette code -- is
    what tells the agent so in numbers. Both are exercised here end to end: build the base, inspect,
    reshape a band, export, and judge it with the pipeline's code, run as the agent runs it."""
    shutil.copytree(_TOOLS_DIR, tmp_path / "tools", ignore=shutil.ignore_patterns("__pycache__"))
    for sub in ("refs", "renders", "output", "textures"):
        (tmp_path / sub).mkdir()
    _synthetic_cutout(tmp_path / "refs" / "person_cutout.png")

    out = _blender(tmp_path, "-P", "tools/bl_silhouette_base.py", "--", "--reference", "refs/person_cutout.png",
                   "--height", "1.8", "--save", "scene.blend")
    assert "front outline from person_cutout.png" in out
    assert (tmp_path / "textures" / "silhouette_atlas.png").is_file()

    _blender(tmp_path, "-P", "tools/bl_inspect.py", "--", "--json", "renders/inspect.json", blend="scene.blend")
    inspect = json.loads((tmp_path / "renders" / "inspect.json").read_text(encoding="utf-8"))
    assert inspect["printable"] is True and inspect["bodies"] == 1
    depth_before = inspect["sceneDimensions"][1]

    out = _blender(tmp_path, "-P", "tools/bl_reshape.py", "--", "--band", "0.0:1.0", "--depth", "1.5",
                   "--save", "scene.blend", blend="scene.blend")
    assert "reshaped" in out
    _blender(tmp_path, "-P", "tools/bl_inspect.py", "--", "--json", "renders/inspect2.json", blend="scene.blend")
    inspect2 = json.loads((tmp_path / "renders" / "inspect2.json").read_text(encoding="utf-8"))
    assert inspect2["sceneDimensions"][1] == pytest.approx(depth_before * 1.5, rel=0.05), "depth should scale 1.5x"
    assert inspect2["sceneDimensions"][0] == pytest.approx(inspect["sceneDimensions"][0], rel=0.01), "width untouched"
    assert inspect2["printable"] is True

    # Solidify as the agent must, then check the baked layout is a layout: an unwrap that corrected for the
    # aspect ratio of a 4-pixel-wide image once squashed every island into two percent of the atlas.
    out = _blender(tmp_path, "-P", "tools/bl_solidify.py", "--", "--save", "scene.blend", blend="scene.blend")
    assert "ONE body, closed" in out, out[-1500:]
    # Then the photograph goes onto the fused body the way a refine run puts it onto a reconstruction.
    out = _blender(tmp_path, "-P", "tools/bl_project_photo.py", "--", "--reference", "refs/person_cutout.png",
                   "--save", "scene.blend", blend="scene.blend")
    assert "face the camera" in out and "turning 0 deg" in out or "turning 180 deg" in out, out[-1500:]
    assert (tmp_path / "textures" / "projected_atlas.png").is_file()
    # The mask of what the camera did not see, over that atlas: the input to an inpainting request.
    out = _blender(tmp_path, "-P", "tools/bl_uv_mask.py", "--", "--out", "textures/inferred_mask.png", blend="scene.blend")
    assert "marked for filling" in out, out[-1500:]
    from PIL import Image as _Image

    mask = np.asarray(_Image.open(tmp_path / "textures" / "inferred_mask.png").convert("L"))
    assert 0.1 < (mask > 127).mean() < 0.9, "roughly the back half of the figure should be marked"
    _blender(tmp_path, "-P", "tools/bl_export.py", "--", "--out", "output/model.glb", blend="scene.blend")
    import trimesh

    exported = trimesh.load(str(tmp_path / "output" / "model.glb"), force="scene").to_geometry()
    uv = np.asarray(exported.visual.uv)
    span = uv.max(axis=0) - uv.min(axis=0)
    assert span.min() > 0.5, f"the baked uv layout is squashed: spans {span}"

    # The gate tool runs with the pipeline's Python, exactly as the agent is told to run it.
    env = dict(os.environ, PIPELINE_CODE_DIR=_CONTAINER_DIR, PYTHONUTF8="1")
    done = subprocess.run([sys.executable, "tools/agent_gate.py", "--mesh", "output/model.glb",
                           "--reference", "refs/person_cutout.png", "--json", "renders/gate.json"],
                          cwd=str(tmp_path), env=env, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=600)
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    gate = json.loads((tmp_path / "renders" / "gate.json").read_text(encoding="utf-8"))
    assert gate["gate"]["passed"] is True and gate["gate"]["bodies"] == 1
    assert gate["silhouette"]["frontIoU"] >= 0.85, gate["silhouette"]
    assert gate["silhouette"]["facingFront"] is True, gate["silhouette"]
    assert gate["texture"] == "preserved"
    assert gate["silhouette"]["matchIoU"] >= gate["silhouette"]["frontIoU"]
    colours = {row["band"]: row for row in gate["colours"]}
    # The top band is skin and the legs are blue, in the model and in the photograph alike.
    assert colours["50-60% from top"]["model"]["colour"] == colours["50-60% from top"]["reference"]["colour"] == "blue"
    assert "GATE: PASS" in done.stdout and "SILHOUETTE: match IoU" in done.stdout


# The two browns a reconstruction might capture, as the display-encoded bytes its glTF carries, and the
# brighter brown of the photograph it was made from.
_BROWN_LEFT, _BROWN_RIGHT, _PHOTO_BROWN = (95, 68, 38), (160, 118, 69), (200, 140, 80)


def _reconstruction_glb(path):
    """A reconstruction's output as it reaches the refine run: one closed body, its own colour on the vertices
    and nothing else -- two browns split down the middle, and black on the cap at the back it never saw.
    Written with trimesh, the way the pipeline writes one, so the bytes are display colours."""
    import trimesh

    sphere = trimesh.creation.uv_sphere(radius=0.5, count=[48, 48])
    sphere.apply_translation([0.0, 0.5, 0.0])  # glTF is y-up: the body stands on y=0 and faces +z
    xyz = np.asarray(sphere.vertices)
    colours = np.where((xyz[:, 0] > 0)[:, None], _BROWN_RIGHT, _BROWN_LEFT).astype(np.uint8)
    colours[xyz[:, 2] < -0.42] = 0
    sphere.visual = trimesh.visual.ColorVisuals(mesh=sphere, vertex_colors=np.column_stack(
        [colours, np.full(len(colours), 255, dtype=np.uint8)]))
    path.write_bytes(sphere.export(file_type="glb"))
    return sphere


def _disc_cutout(path, rgb, size=256, radius=110):
    """A photograph of the sphere: one flat disc of colour on a transparent background."""
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse([size // 2 - radius, size // 2 - radius, size // 2 + radius, size // 2 + radius],
                                  fill=(*rgb, 255))
    image.save(path)


def _refine_workspace(tmp_path):
    shutil.copytree(_TOOLS_DIR, tmp_path / "tools", ignore=shutil.ignore_patterns("__pycache__"))
    for sub in ("refs", "start", "renders", "output", "textures"):
        (tmp_path / sub).mkdir()
    _reconstruction_glb(tmp_path / "start" / "recon.glb")
    _disc_cutout(tmp_path / "refs" / "disc_cutout.png", _PHOTO_BROWN)
    return tmp_path


def _gate(workspace, mesh, reference=None, report="renders/gate.json"):
    env = dict(os.environ, PIPELINE_CODE_DIR=_CONTAINER_DIR, PYTHONUTF8="1")
    command = [sys.executable, "tools/agent_gate.py", "--mesh", mesh, "--json", report]
    if reference:
        command += ["--reference", reference]
    done = subprocess.run(command, cwd=str(workspace), env=env, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=600)
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    return done.stdout, json.loads((workspace / report).read_text(encoding="utf-8"))


def _exported_colours(path):
    """Per-vertex display colour and unit normals of an exported model, in the agent's Z-up frame."""
    import trimesh

    import export
    import texturing

    mesh = export.normalize_up_axis(trimesh.load(str(path), force="scene").to_geometry(), "y")
    colours = np.asarray(texturing.sample_atlas_colors(np.asarray(mesh.visual.uv), mesh.visual.material))[:, :3]
    return colours.astype(float), np.asarray(mesh.vertex_normals)


def _luminance(rgb):
    return np.asarray(rgb, dtype=float) @ np.array([0.299, 0.587, 0.114])


def test_the_gate_reads_the_colour_a_reconstruction_arrives_with(tmp_path):
    """Every refine run's first gate said its starting mesh carried no colour, and the agent painted over a
    surface it had never measured. The import wires the vertex colour to a material with no image, the export
    keeps it that way, and the gate reads it through -- bands, words and the noise number."""
    workspace = _refine_workspace(tmp_path)
    _blender(workspace, "-P", "tools/bl_import.py", "--", "--mesh", "start/recon.glb", "--save", "scene.blend")
    _blender(workspace, "-P", "tools/bl_export.py", "--", "--out", "output/start.glb", blend="scene.blend")
    out, gate = _gate(workspace, "output/start.glb", "refs/disc_cutout.png")
    assert "carries no texture or vertex colour" not in out, out[-1500:]
    assert "COLOUR NOISE:" in out and gate["colourNoise"]["word"] == "smooth", out[-1500:]
    assert gate["colourNoise"]["nearBlackShare"] > 0.02, "the black cap is reported"
    bands = {row["band"]: row for row in gate["colours"]}
    assert bands["50-60% from top"]["model"]["colour"] == "brown", bands["50-60% from top"]


def test_the_projector_keeps_the_captured_colour_where_the_photograph_did_not_see(tmp_path):
    """A blind judge marked every refine that replaced a reconstruction's smooth colour with one projected
    photograph and a filled-in rest down as speckled or patchy. The photograph goes where it was taken -- the
    faces that squarely face the camera -- and the surfaces it never saw keep the colour the reconstruction
    captured, brought to the photograph's tone and with its black holes filled from their neighbours."""
    workspace = _refine_workspace(tmp_path)
    _blender(workspace, "-P", "tools/bl_import.py", "--", "--mesh", "start/recon.glb", "--save", "scene.blend")
    out = _blender(workspace, "-P", "tools/bl_project_photo.py", "--", "--reference", "refs/disc_cutout.png",
                   "--save", "scene.blend", "--azimuth", "0", blend="scene.blend")
    assert "kept the reconstruction's own colour" in out, out[-1500:]
    assert "flooded" in out, out[-1500:]
    _blender(workspace, "-P", "tools/bl_bake.py", "--", "--save", "scene.blend", "--size", "512", "--samples", "4",
             blend="scene.blend")
    _blender(workspace, "-P", "tools/bl_export.py", "--", "--out", "output/model.glb", blend="scene.blend")

    colours, normals = _exported_colours(workspace / "output" / "model.glb")
    front, back = normals[:, 1] < -0.6, normals[:, 1] > 0.6
    photo = np.array(_PHOTO_BROWN, dtype=float)
    assert np.abs(colours[front].mean(axis=0) - photo).max() < 25, f"the front wears the photograph: {colours[front].mean(axis=0)}"
    assert np.abs(colours[back] - photo).mean() > 25, "the back is not the photograph's colour continued as a stripe"
    spread = lambda rows: np.percentile(_luminance(rows), 90) - np.percentile(_luminance(rows), 10)  # noqa: E731
    assert spread(colours[back]) > 20, f"the two captured browns still meet down the middle of the back: {spread(colours[back]):.1f}"
    assert spread(colours[front]) < 12, f"the photographed front is one flat colour: {spread(colours[front]):.1f}"
    captured = _luminance(np.array([_BROWN_LEFT, _BROWN_RIGHT])).mean()
    assert _luminance(colours[back]).mean() > captured * 1.12, "the kept colour is lifted towards the photograph's tone"
    assert (_luminance(colours[back]) < 20).mean() < 0.01, "the black cap was flooded from the coat beside it"

    out, gate = _gate(workspace, "output/model.glb", "refs/disc_cutout.png")
    assert gate["colourNoise"]["word"] in ("smooth", "grainy"), gate["colourNoise"]
    assert gate["texture"] == "preserved" and gate["gate"]["passed"] is True

    # Run again on the same scene, as an agent does when it retries the alignment: the captured colour was
    # decoded from display bytes the first time and must not be decoded twice. Two agents that projected twice
    # shipped crushed, noisier coats. The second run leaves the back's colour where the first put it.
    first_back = colours[back].mean(axis=0)
    out = _blender(workspace, "-P", "tools/bl_project_photo.py", "--", "--reference", "refs/disc_cutout.png",
                   "--save", "scene.blend", "--azimuth", "0", blend="scene.blend")
    assert "already linear" in out, out[-1500:]
    _blender(workspace, "-P", "tools/bl_bake.py", "--", "--save", "scene.blend", "--size", "512", "--samples", "4",
             blend="scene.blend")
    _blender(workspace, "-P", "tools/bl_export.py", "--", "--out", "output/model.glb", blend="scene.blend")
    colours2, normals2 = _exported_colours(workspace / "output" / "model.glb")
    second_back = colours2[normals2[:, 1] > 0.6].mean(axis=0)
    assert np.abs(second_back - first_back).max() < 12, f"a second projection changed the kept colour: {first_back} -> {second_back}"

    # The baked image IS the colour. A vertex colour exported beside it is multiplied into it by a glTF
    # viewer, which two agent runs found and stripped by hand.
    import struct

    with open(workspace / "output" / "model.glb", "rb") as handle:
        handle.read(12)
        length = struct.unpack("<I", handle.read(4))[0]
        handle.read(4)
        header = json.loads(handle.read(length))
    attributes = header["meshes"][0]["primitives"][0]["attributes"]
    assert not any(name.startswith("COLOR_") for name in attributes), attributes


def _refit_workspace(tmp_path, squash=0.8):
    """`_reconstruction_glb`'s sphere squashed across to a body narrower than the disc it was photographed as."""
    shutil.copytree(_TOOLS_DIR, tmp_path / "tools", ignore=shutil.ignore_patterns("__pycache__"))
    for sub in ("refs", "start", "refit", "renders", "output", "textures"):
        (tmp_path / sub).mkdir()
    sphere = _reconstruction_glb(tmp_path / "start" / "recon.glb")
    sphere.apply_scale([squash, 1.0, squash])
    (tmp_path / "start" / "recon.glb").write_bytes(sphere.export(file_type="glb"))
    _disc_cutout(tmp_path / "refs" / "disc_cutout.png", _PHOTO_BROWN)
    return tmp_path


def _refit(workspace, *args):
    return _blender(workspace, "-P", "tools/bl_refit.py", "--", *args, blend="scene.blend")


def _refit_steps(workspace):
    return json.loads((workspace / "refit" / "refit_report.json").read_text(encoding="utf-8"))["steps"]


def _bump_depth(cutout, out):
    """A depth estimate of the disc as the refit's relief step reads one: level, with one bump nearer the
    camera at its middle, and the subject's width in the estimate's units."""
    from PIL import Image

    alpha = np.asarray(Image.open(cutout).convert("RGBA"))[:, :, 3] > 127
    rows, cols = np.mgrid[0:alpha.shape[0], 0:alpha.shape[1]]
    middle_r, middle_c = alpha.shape[0] / 2, alpha.shape[1] / 2
    depth = 2.0 - 0.02 * np.exp(-((rows - middle_r) ** 2 + (cols - middle_c) ** 2) / (2 * 6.0 ** 2))
    covered = np.nonzero(alpha.any(axis=0))[0]
    width = (covered.max() - covered.min() + 1) / alpha.shape[1]
    np.savez_compressed(out, depth=np.where(alpha, depth, 0.0).astype(np.float32), mask=alpha,
                        metricWidth=np.float64(width))
    return {"validPixels": int(alpha.sum()), "metricWidth": round(float(width), 4)}


_MESH_COUNT = ("import bpy\n"
               "meshes = [o for o in bpy.data.objects if o.type == 'MESH']\n"
               "for o in meshes: o.data.calc_loop_triangles()\n"
               "print('MESHES', len(meshes), sum(len(o.data.loop_triangles) for o in meshes),"
               " ','.join(sorted(o.name for o in meshes)))")
_FRONT = "import bpy; print('FRONT', min(v.co.y for v in bpy.data.objects['Subject'].data.vertices))"


def _printed(out, tag):
    return next(line for line in out.splitlines() if line.startswith(tag + " ")).split()[1:]


@pytest.mark.parametrize("method", ["voxel", "metaball"])
def test_a_rebuilt_body_is_one_closed_piece_of_the_volume_it_replaced_wearing_the_colour_it_had(tmp_path, method):
    workspace = _refit_workspace(tmp_path)
    _blender(workspace, "-P", "tools/bl_import.py", "--", "--mesh", "start/recon.glb", "--save", "scene.blend")
    _refit(workspace, "register", "--reference", "refs/disc_cutout.png")
    _refit(workspace, "rebuild", "--method", method, "--resolution", "0.02")
    _refit(workspace, "check", "--resolution", "0.02")
    _refit(workspace, "colour")
    steps = _refit_steps(workspace)
    assert "reverted" not in steps["rebuild"], steps["rebuild"]
    assert abs(steps["rebuild"]["volumeRatio"] - 1.0) < 0.1, steps["rebuild"]
    assert steps["rebuild"]["drift"]["mean"] < 0.02, steps["rebuild"]
    assert steps["check"]["after"]["pieces"] == 1 and steps["check"]["leaks"] is False, steps["check"]
    # Blender's voxel remesh keeps the colour attribute itself; a metaball body has none until it is carried.
    worn = ("carried the reconstruction's colour",) if method == "metaball" else ("kept", "carried")
    assert steps["colour"]["colour"].startswith(worn), steps["colour"]


def test_register_joins_a_scene_of_parts_into_the_one_subject_the_other_steps_work_on(tmp_path):
    workspace = _refit_workspace(tmp_path)
    (workspace / "scripts").mkdir()
    (workspace / "scripts" / "build_parts.py").write_text(_BUILD_PARTS, encoding="utf-8")
    _blender(workspace, "-P", "scripts/build_parts.py")
    count, triangles, _ = _printed(_blender(workspace, "--python-expr", _MESH_COUNT, blend="scene.blend"), "MESHES")
    assert int(count) == 12
    _refit(workspace, "register", "--reference", "refs/disc_cutout.png")
    after = _printed(_blender(workspace, "--python-expr", _MESH_COUNT, blend="scene.blend"), "MESHES")
    assert after == ["1", triangles, "Subject"], after
    assert _refit_steps(workspace)["register"]["triangles"] == int(triangles)


_SOURCE_KEPT = "import bpy; print('SOURCE', 'RefitSource' in bpy.data.objects)"
_COLOUR_LAYERS = "import bpy; print('COLOURS', len(bpy.data.objects['Subject'].data.color_attributes))"


def test_quadriflow_lands_near_the_face_budget_wearing_the_colour_or_records_that_it_decimated_instead(tmp_path):
    """The default rebuild, none, then QuadriFlow: the body keeps the reconstruction's colour through the
    remesh, and `colour` finds it rather than leaving the projection to colour the unseen side by rows."""
    workspace = _refit_workspace(tmp_path)
    _blender(workspace, "-P", "tools/bl_import.py", "--", "--mesh", "start/recon.glb", "--save", "scene.blend")
    _refit(workspace, "register", "--reference", "refs/disc_cutout.png")
    _refit(workspace, "rebuild", "--method", "none")
    (kept,) = _printed(_blender(workspace, "--python-expr", _SOURCE_KEPT, blend="scene.blend"), "SOURCE")
    assert kept == "True", "rebuild keeps the body it started from for `colour`, whatever the method"
    _refit(workspace, "retopo", "--method", "quadriflow", "--faces", "2000")
    (layers,) = _printed(_blender(workspace, "--python-expr", _COLOUR_LAYERS, blend="scene.blend"), "COLOURS")
    _refit(workspace, "check")
    _refit(workspace, "colour")
    steps = _refit_steps(workspace)
    retopo = steps["retopo"]
    assert retopo["used"] == ("decimate" if "fallback" in retopo else "quadriflow"), retopo
    assert 1000 <= retopo["trianglesAfter"] <= 3000, retopo
    assert steps["check"]["leaks"] is False, steps["check"]
    assert int(layers) >= 1, f"the remeshed body lost its colour attribute: {retopo}"
    assert not steps["colour"]["colour"].startswith("no colour"), steps["colour"]


def test_the_relief_raises_what_the_depth_estimate_puts_nearer_the_camera_and_refuses_another_grid(tmp_path):
    workspace = _refit_workspace(tmp_path, squash=1.0)
    _blender(workspace, "-P", "tools/bl_import.py", "--", "--mesh", "start/recon.glb", "--save", "scene.blend")
    _refit(workspace, "register", "--reference", "refs/disc_cutout.png")
    _bump_depth(workspace / "refs" / "disc_cutout.png", workspace / "refs" / "front_depth.npz")
    (before,) = _printed(_blender(workspace, "--python-expr", _FRONT, blend="scene.blend"), "FRONT")
    _refit(workspace, "relief", "--reference", "refs/disc_cutout.png", "--depth", "refs/front_depth.npz")
    (after,) = _printed(_blender(workspace, "--python-expr", _FRONT, blend="scene.blend"), "FRONT")
    relief = _refit_steps(workspace)["relief"]
    assert relief["vertices"] > 0 and "reverted" not in relief, relief
    assert float(after) < float(before) - 0.003, f"the middle came towards the camera on -Y: {before} -> {after}"

    np.savez_compressed(workspace / "refs" / "small_depth.npz", depth=np.zeros((10, 10), dtype=np.float32),
                        mask=np.ones((10, 10), dtype=bool), metricWidth=np.float64(1.0))
    out = _blender(workspace, "-P", "tools/bl_refit.py", "--", "relief", "--reference", "refs/disc_cutout.png",
                   "--depth", "refs/small_depth.npz", blend="scene.blend", expect_failure=True)
    assert "not the same grid" in out, out[-1500:]


def test_the_refit_chain_brings_a_reconstruction_to_its_photographs_outline_as_one_closed_textured_body(tmp_path):
    """The pipeline's own chain, one Blender per step, on a body narrower than its photograph: the outline
    comes closer, the relief lands, the body stays one closed piece, and the photograph is on the side the
    registration turned to the camera."""
    from models import blenderagent_refit

    workspace = _refit_workspace(tmp_path)
    settings = {"rebuild": "none", "resolution": 0.004, "outline_fit": True, "relief": True,
                "relief_strength": 0.01, "retopo": "decimate", "faces": 2000, "min_volume_retention": 0.5}
    refit = blenderagent_refit.run_refit(workspace, "refs/disc_cutout.png", "start/recon.glb", settings,
                                         blenderagent_refit.blender_runner(_BLENDER), estimate_depth=_bump_depth)
    summary = refit["summary"]
    assert summary["skipped"] == [], summary["skipped"]
    assert summary["finalIou"] > summary["registrationIou"] + 0.05, summary
    assert summary["leaks"] is False and summary["retopo"]["trianglesAfter"] <= 2000, summary
    assert summary["relief"]["vertices"] > 0 and "reverted" not in summary["relief"], summary["relief"]
    verdict = json.loads((workspace / "refit" / "refit_export_check.json").read_text(encoding="utf-8"))
    assert verdict["hasImages"] is True and verdict["facesMatch"] is True, verdict
    # `unturn` turned the body back, so the photographed side is wherever the registration's view was.
    rotation = np.array(json.loads((workspace / "refit" / "refit_frame.json").read_text(encoding="utf-8"))["rotation"])
    camera_side = rotation.T @ np.array([0.0, -1.0, 0.0])
    colours, normals = _exported_colours(workspace / "refit" / "refit.glb")
    photo = np.array(_PHOTO_BROWN, dtype=float)
    seen = normals @ camera_side > 0.8
    assert np.abs(colours[seen].mean(axis=0) - photo).max() < 25, f"the photographed side wears it: {colours[seen].mean(axis=0)}"
    assert np.abs(colours[normals @ camera_side < -0.8].mean(axis=0) - photo).max() > 25, "the far side does not"


_SIDE_COLOURS = textwrap.dedent('''
    """The colour the imported mesh wears on each side of the body, read as the projection reads it."""
    import bpy
    import numpy as np

    (mesh,) = [o.data for o in bpy.data.objects if o.type == "MESH"]
    if not len(mesh.color_attributes):
        print("SIDES none")
        raise SystemExit(0)
    attribute = mesh.color_attributes.active_color or mesh.color_attributes[0]
    values = np.empty(len(attribute.data) * 4, dtype=np.float32)
    attribute.data.foreach_get("color", values)
    rgb = values.reshape(-1, 4)[:, :3].astype(float)
    if attribute.domain == "CORNER":
        index = np.empty(len(mesh.loops), dtype=np.int64)
        mesh.loops.foreach_get("vertex_index", index)
        total = np.zeros((len(mesh.vertices), 3))
        np.add.at(total, index, rgb)
        rgb = total / np.maximum(np.bincount(index, minlength=len(mesh.vertices)), 1)[:, None]
    co = np.empty(len(mesh.vertices) * 3)
    mesh.vertices.foreach_get("co", co)
    x = co.reshape(-1, 3)[:, 0]
    lit = rgb.max(axis=1) > 0.05
    right, left = np.median(rgb[lit & (x > 0.1)], axis=0), np.median(rgb[lit & (x < -0.1)], axis=0)
    print("SIDES", *np.round(np.concatenate([right, left]), 4))
''')


def _glb_attributes(path):
    """The vertex attributes a glTF binary's first primitive carries."""
    import struct

    with open(path, "rb") as handle:
        handle.read(12)
        length = struct.unpack("<I", handle.read(4))[0]
        handle.read(4)
        header = json.loads(handle.read(length))
    return set(header["meshes"][0]["primitives"][0]["attributes"])


def test_an_agent_after_the_refit_starts_from_a_mesh_whose_colour_its_projection_keeps(tmp_path):
    """refit.glb's colour is its baked image, which the import does not turn into vertex colour, so an agent
    started from it found no captured colour and its projection coloured the unseen side by rows. The mesh it
    is handed carries the reconstruction's colour on its vertices, as the reconstruction's own glb does, and
    refit.glb stays a baked image with no vertex colour to darken it."""
    from models import blenderagent_refit

    workspace = _refit_workspace(tmp_path / "refit")
    settings = {"rebuild": "none", "resolution": 0.004, "outline_fit": True, "relief": False,
                "relief_strength": 0.01, "retopo": "decimate", "faces": 2000, "min_volume_retention": 0.5}
    refit = blenderagent_refit.run_refit(workspace, "refs/disc_cutout.png", "start/recon.glb", settings,
                                         blenderagent_refit.blender_runner(_BLENDER))
    assert not any(name.startswith("COLOR_") for name in _glb_attributes(refit["mesh"]))
    handed = refit["handover"]
    assert "COLOR_0" in _glb_attributes(handed)

    agent = tmp_path / "agent"
    shutil.copytree(_TOOLS_DIR, agent / "tools", ignore=shutil.ignore_patterns("__pycache__"))
    for sub in ("refs", "start", "scripts", "renders", "output", "textures"):
        (agent / sub).mkdir()
    shutil.copy2(workspace / "refs" / "disc_cutout.png", agent / "refs" / "disc_cutout.png")
    shutil.copy2(handed, agent / "start" / os.path.basename(handed))
    (agent / "scripts" / "sides.py").write_text(_SIDE_COLOURS, encoding="utf-8")
    _blender(agent, "-P", "tools/bl_import.py", "--", "--mesh", f"start/{os.path.basename(handed)}",
             "--save", "scene.blend")
    sides = _printed(_blender(agent, "-P", "scripts/sides.py", blend="scene.blend"), "SIDES")
    assert sides != ["none"], "the mesh the agent starts from carries no colour attribute"
    # The two browns the reconstruction captured, as its glTF's display bytes read back.
    expected = np.array(_BROWN_RIGHT + _BROWN_LEFT, dtype=float) / 255.0
    assert np.abs(np.array(sides, dtype=float) - expected).max() < 0.03, f"{sides} against {expected.round(4)}"
    out = _blender(agent, "-P", "tools/bl_project_photo.py", "--", "--reference", "refs/disc_cutout.png",
                   "--save", "scene.blend", blend="scene.blend")
    assert "kept the reconstruction's own colour" in out, out[-1500:]


_REAL_MESH, _REAL_CUTOUT = os.environ.get("REFIT_LIVE_MESH"), os.environ.get("REFIT_LIVE_CUTOUT")


@pytest.mark.skipif(not (_REAL_MESH and _REAL_CUTOUT),
                    reason="set REFIT_LIVE_MESH and REFIT_LIVE_CUTOUT to a SAM 3D mesh and its cutout")
def test_a_real_reconstruction_comes_out_closer_to_its_photograph_and_closed(tmp_path):
    """The chain on a real SAM 3D mesh, relief off since a laptop carries no depth model. REFIT_LIVE_REBUILD
    picks the rebuild, none by default."""
    from models import blenderagent_refit

    shutil.copytree(_TOOLS_DIR, tmp_path / "tools", ignore=shutil.ignore_patterns("__pycache__"))
    for sub in ("refs", "start", "refit", "textures"):
        (tmp_path / sub).mkdir()
    start = "start/start" + os.path.splitext(_REAL_MESH)[1].lower()
    shutil.copy2(_REAL_MESH, tmp_path / start)
    shutil.copy2(_REAL_CUTOUT, tmp_path / "refs" / "front_cutout.png")
    settings = {"rebuild": os.environ.get("REFIT_LIVE_REBUILD", "none"), "resolution": 0.004, "outline_fit": True,
                "relief": False, "relief_strength": 0.01, "retopo": "decimate", "faces": 200000,
                "min_volume_retention": 0.5}
    refit = blenderagent_refit.run_refit(tmp_path, "refs/front_cutout.png", start, settings,
                                         blenderagent_refit.blender_runner(_BLENDER))
    summary = refit["summary"]
    print(json.dumps(summary, indent=1))
    assert summary["finalIou"] >= summary["registrationIou"], summary
    assert summary["leaks"] is False, summary

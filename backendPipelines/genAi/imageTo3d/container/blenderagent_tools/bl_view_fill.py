# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Fill the surfaces the photograph never saw from a VIEW: the image model completes a picture, not an atlas.

    blender -b scene.blend --factory-startup --python-exit-code 1 -P tools/bl_view_fill.py -- \\
        prepare --view back [--size 1024] [--out textures/view_back]
    python tools/agent_image_edit.py inpaint --image textures/view_back.png --mask textures/view_back_mask.png \\
        --prompt "<the subject seen from behind: hair, clothing, colours>" --out textures/view_back_filled.png
    blender -b scene.blend --factory-startup --python-exit-code 1 -P tools/bl_view_fill.py -- \\
        apply --view back --image textures/view_back_filled.png --save scene.blend [--atlas textures/view_fill_atlas.png]

For the ONE mesh in the scene, after `bl_solidify.py` or `bl_project_photo.py`. ``prepare`` renders the
model's current texture, unlit, from a named view -- the same fixed orthographic views as `bl_render.py` --
and writes beside it a mask of the pixels whose surface the photograph did not cover and no earlier view has
filled. Inpaint that pair: the model sees the subject from that side, with the photographed parts as
context, and completes it. ``apply`` carries the completed picture back onto the surface: every atlas texel
of a face that squarely faces this view and is visible in it takes the picture's colour there, with a depth
test so a texel behind an arm is left alone. The faces filled are marked on the mesh, so the next
``prepare`` masks only what remains, and ``apply`` names the view that sees the most of it.

Why views and not the atlas: an inpainting model continues a picture. A packed uv layout is islands with no
surroundings, and the first live request on one came back a uniform grey-brown. Two or three views -- the
back, then a side or two -- cover a standing figure.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402
import numpy as np  # noqa: E402
from bpy_extras.object_utils import world_to_camera_view  # noqa: E402
from mathutils import Vector  # noqa: E402

import bl_common  # noqa: E402
import view_fill  # noqa: E402

# Two face attributes on the mesh, kept in scene.blend and not carried by the glTF exporter: whether a view
# has painted the face, and a bitmask of the views tried on it -- a face still unfilled after a view was
# applied was hidden in that view, and is not counted for it again.
FILLED_ATTRIBUTE = "view_filled"
TRIED_ATTRIBUTE = "view_tried"


def the_mesh():
    objects = bl_common.mesh_objects()
    if len(objects) != 1:
        raise SystemExit(f"The scene has {len(objects)} mesh objects; run bl_solidify.py first so there is one")
    obj = objects[0]
    if not obj.data.uv_layers.active:
        raise SystemExit("The mesh has no UV layout; run bl_solidify.py or bl_project_photo.py first")
    return obj


def atlas_node(obj):
    """The material and image texture node carrying the mesh's atlas."""
    for material in obj.data.materials:
        if material and material.use_nodes:
            for node in material.node_tree.nodes:
                if node.type == "TEX_IMAGE" and node.image and node.image.size[0] and node.image.size[1]:
                    return material, node
    raise SystemExit("The mesh has no image texture; run bl_solidify.py or bl_project_photo.py first")


def triangulated(obj):
    """World vertices, triangles, their uv corners, and the polygon each triangle came from."""
    mesh = obj.data
    matrix = obj.matrix_world
    vertices = np.array([matrix @ v.co for v in mesh.vertices], dtype=float)
    uv_data = mesh.uv_layers.active.data
    triangles, uvs, owner = [], [], []
    for polygon in mesh.polygons:
        idx = list(polygon.vertices)
        loops = list(polygon.loop_indices)
        for k in range(1, len(idx) - 1):
            triangles.append((idx[0], idx[k], idx[k + 1]))
            uvs.append((uv_data[loops[0]].uv[:], uv_data[loops[k]].uv[:], uv_data[loops[k + 1]].uv[:]))
            owner.append(polygon.index)
    return (vertices, np.array(triangles, dtype=np.int64).reshape(-1, 3),
            np.array(uvs, dtype=float).reshape(-1, 3, 2), np.array(owner, dtype=np.int64))


def filled_flags(mesh):
    attribute = mesh.attributes.get(FILLED_ATTRIBUTE)
    if attribute is None:
        return np.zeros(len(mesh.polygons), dtype=bool)
    return np.array([d.value for d in attribute.data], dtype=bool)


def tried_bits(mesh):
    attribute = mesh.attributes.get(TRIED_ATTRIBUTE)
    if attribute is None:
        return np.zeros(len(mesh.polygons), dtype=np.int64)
    return np.array([d.value for d in attribute.data], dtype=np.int64)


def store_face_attribute(mesh, name, kind, values):
    attribute = mesh.attributes.get(name)
    if attribute is None:
        attribute = mesh.attributes.new(name, kind, "FACE")
    cast = bool if kind == "BOOLEAN" else int
    for datum, value in zip(attribute.data, values):
        datum.value = cast(value)


def camera_frame(view, size):
    if view not in view_fill.VIEWS:
        raise SystemExit(f"Unknown view '{view}'; choose from {', '.join(view_fill.VIEWS)}")
    az, el = view_fill.VIEWS[view]
    scene = bpy.context.scene
    scene.render.resolution_x = scene.render.resolution_y = size
    scene.render.resolution_percentage = 100
    camera = bl_common.ensure_camera_and_light()
    bl_common.frame_camera(camera, az, el)
    # The camera was moved by assignment; its world matrix is only current after an update.
    bpy.context.view_layer.update()
    return camera, az, el


def camera_coordinates(camera, vertices):
    scene = bpy.context.scene
    coords = np.array([world_to_camera_view(scene, camera, Vector(v))[:] for v in vertices], dtype=float)
    return coords[:, :2], coords[:, 2]


def scene_fingerprint(vertices):
    lo, hi = vertices.min(axis=0), vertices.max(axis=0)
    return {"vertices": int(len(vertices)), "bounds": [round(float(v), 5) for v in (*lo, *hi)]}


def eligible_triangles(normals, owner, filled, az, el, photo_threshold, facing_threshold):
    """Triangles to fill from this view: never photographed, not yet filled, squarely facing its camera."""
    photographed = view_fill.facing(normals, *view_fill.VIEWS["front"]) > photo_threshold
    remaining = ~photographed & ~filled[owner]
    return remaining & (view_fill.facing(normals, az, el) > facing_threshold), remaining


def polygons_of(mesh, triangle_flags, owner):
    flags = np.zeros(len(mesh.polygons), dtype=bool)
    flags[owner[triangle_flags]] = True
    return flags


def write_image(path, array, name):
    """An (H, W) or (H, W, 3) float array in 0..1, rows top-down, saved as a PNG through Blender's image API."""
    if array.ndim == 2:
        array = np.repeat(array[..., None], 3, axis=2)
    height, width = array.shape[:2]
    image = bpy.data.images.new(name, width, height, alpha=False)
    pixels = np.ones((height, width, 4), dtype=np.float32)
    pixels[..., :3] = array
    image.pixels.foreach_set(pixels[::-1].reshape(-1))
    image.filepath_raw = os.path.abspath(path)
    image.file_format = "PNG"
    image.save()
    bpy.data.images.remove(image)


def read_image(image):
    """An image datablock's pixels as an (H, W, C) float array, rows top-down."""
    width, height = image.size
    buffer = np.empty(width * height * image.channels, dtype=np.float32)
    image.pixels.foreach_get(buffer)
    return buffer.reshape(height, width, image.channels)[::-1]


def prepare(args):
    obj = the_mesh()
    material, node = atlas_node(obj)
    vertices, triangles, tri_uv, owner = triangulated(obj)
    normals = view_fill.face_normals(vertices, triangles)
    camera, az, el = camera_frame(args.view, args.size)
    eligible, remaining = eligible_triangles(normals, owner, filled_flags(obj.data), az, el,
                                             args.photo_threshold, args.facing)

    # The render is the texture itself -- unlit, untransformed -- so what the model completes is what
    # ``apply`` bakes back, and the photographed parts appear as the photograph.
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.film_transparent = False
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.display.render_aa = "OFF"
    shading = scene.display.shading
    shading.light = "FLAT"
    shading.color_type = "TEXTURE"
    shading.background_type = "VIEWPORT"
    shading.background_color = (0.5, 0.5, 0.5)
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    material.node_tree.nodes.active = node
    render_path = os.path.abspath(f"{args.out}.png")
    scene.render.filepath = render_path
    bpy.ops.render.render(write_still=True)
    if not os.path.isfile(render_path):
        raise SystemExit(f"Render reported success but {render_path} was not written")

    cam_xy, depth = camera_coordinates(camera, vertices)
    _, ids = view_fill.rasterise_view(cam_xy, depth, triangles, view_fill.facing(normals, az, el) > 0.0,
                                      args.size, args.size)
    raw = view_fill.view_mask(ids, eligible, 0)
    mask = view_fill.view_mask(ids, eligible, args.dilate, args.edge_margin)
    write_image(f"{args.out}_mask.png", mask.astype(np.float32), "ViewFillMask")
    visible = np.zeros(len(triangles), dtype=bool)
    visible[np.unique(ids[raw])] = True
    to_fill = int(polygons_of(obj.data, eligible & visible, owner).sum())
    remaining_polygons = int(polygons_of(obj.data, remaining, owner).sum())
    bl_common.write_json(f"{args.out}.json", {
        "view": args.view, "azimuth": az, "elevation": el, "size": args.size, "scene": scene_fingerprint(vertices),
        "facesToFill": to_fill, "maskCoverage": round(float(mask.mean()), 4), "facesRemaining": remaining_polygons})
    print(f"render -> {args.out}.png, mask -> {args.out}_mask.png")
    if to_fill:
        print(f"view {args.view}: {to_fill} faces to fill, {mask.mean():.1%} of the frame masked; "
              f"{remaining_polygons} unphotographed faces remain unfilled in all")
        print(f"Next: python tools/agent_image_edit.py inpaint --image {args.out}.png --mask {args.out}_mask.png "
              f"--prompt \"<the subject seen from the {args.view}: hair, clothing, colours>\" "
              f"--out {args.out}_filled.png; LOOK at it; then bl_view_fill.py apply --view {args.view} "
              f"--image {args.out}_filled.png --save scene.blend")
    else:
        name, count = view_fill.suggest_view(normals, remaining, args.facing, tried=tried_bits(obj.data)[owner])
        print(f"Nothing to fill from this view; {remaining_polygons} unphotographed faces remain unfilled in all."
              + (f" The {name} view sees the most of them ({count}): prepare --view {name}." if name
                 else " Every unphotographed face is filled."))


def apply(args):
    obj = the_mesh()
    material, node = atlas_node(obj)
    vertices, triangles, tri_uv, owner = triangulated(obj)
    record_path = f"{args.out}.json"
    if not os.path.isfile(record_path):
        raise SystemExit(f"No {record_path}: run prepare --view {args.view} first")
    with open(record_path, encoding="utf-8") as handle:
        record = json.load(handle)
    if record.get("scene") != scene_fingerprint(vertices):
        raise SystemExit("The mesh has changed since prepare (vertices moved or their count differs); run prepare "
                         "--view again and redo the inpaint on the new render")

    picture = bpy.data.images.load(os.path.abspath(args.image), check_existing=False)
    try:
        image = read_image(picture)[..., :3].astype(float)
    finally:
        bpy.data.images.remove(picture)
    img_h, img_w = image.shape[:2]
    if img_w != img_h:
        raise SystemExit(f"{args.image} is {img_w}x{img_h}; the picture must be the square render from prepare")

    normals = view_fill.face_normals(vertices, triangles)
    camera, az, el = camera_frame(args.view, img_w)
    filled = filled_flags(obj.data)
    eligible, remaining = eligible_triangles(normals, owner, filled, az, el, args.photo_threshold, args.facing)
    if not eligible.any():
        raise SystemExit("Nothing to fill from this view; nothing saved")
    cam_xy, depth = camera_coordinates(camera, vertices)
    zbuf, ids = view_fill.rasterise_view(cam_xy, depth, triangles, view_fill.facing(normals, az, el) > 0.0,
                                         img_w, img_h)
    lo, hi = bl_common.scene_bounds()
    epsilon = max((hi - lo).length / 2, 1e-4) * args.depth_tolerance

    atlas_image = node.image
    atlas = read_image(atlas_image)
    rgb = np.ascontiguousarray(atlas[..., :3].astype(float))
    written = view_fill.bake_view(rgb, tri_uv, triangles, eligible, cam_xy, depth, zbuf, image, epsilon)
    if not written.any():
        raise SystemExit("No texel of the faces to fill was visible in the picture; nothing saved. Is the picture "
                         "the render from prepare for this view?")

    # The filled atlas is a NEW file, which the material's image now reads from; the one before is kept.
    atlas_path = os.path.abspath(args.atlas)
    source = os.path.abspath(bpy.path.abspath(atlas_image.filepath_raw)) if atlas_image.filepath_raw else ""
    if source == atlas_path:
        raise SystemExit(f"--atlas {args.atlas} is the file the material already reads; write the filled atlas to a new one")
    atlas[..., :3] = rgb
    atlas_image.pixels.foreach_set(np.ascontiguousarray(atlas[::-1]).reshape(-1).astype(np.float32))
    atlas_image.filepath_raw = atlas_path
    atlas_image.file_format = "PNG"
    atlas_image.save()

    # A face this view saw is done, whether or not it owned a whole texel; a face that faced this view and
    # was not seen was hidden behind another surface, and this view is not suggested for it again.
    seen = np.zeros(len(triangles), dtype=bool)
    seen[np.unique(ids[ids >= 0])] = True
    mesh = obj.data
    now_filled = filled | polygons_of(mesh, eligible & (seen | (written > 0)), owner)
    store_face_attribute(mesh, FILLED_ATTRIBUTE, "BOOLEAN", now_filled)
    tried = tried_bits(mesh)
    tried[owner[eligible]] |= view_fill.view_bit(args.view)
    store_face_attribute(mesh, TRIED_ATTRIBUTE, "INT", tried)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.save))

    remaining_after = remaining & ~now_filled[owner]
    hidden = int(polygons_of(mesh, eligible & ~(seen | (written > 0)), owner).sum())
    name, count = view_fill.suggest_view(normals, remaining_after, args.facing, tried=tried[owner])
    faces_filled = int(polygons_of(mesh, eligible & (written > 0), owner).sum())
    print(f"filled {faces_filled} faces from the {args.view} view with {os.path.basename(args.image)}: "
          f"{int(written.sum())} texels, {written.sum() / rgb.shape[0] / rgb.shape[1]:.1%} of the atlas; "
          f"atlas -> {args.atlas}; saved -> {args.save}")
    remaining_polygons = int(polygons_of(mesh, remaining_after, owner).sum())
    if hidden:
        print(f"{hidden} faces face this view but were hidden behind other surfaces; another view may reach them")
    print(f"{remaining_polygons} unphotographed faces remain unfilled"
          + (f"; the {name} view sees the most of them ({count}): prepare --view {name} next" if name
             else "; every unphotographed face is filled"))
    print("Then: bl_render.py to look, bl_export.py, agent_gate.py; read the SEAM lines.")


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "apply"):
        sub = commands.add_parser(name)
        sub.add_argument("--view", required=True, help=", ".join(view_fill.VIEWS))
        sub.add_argument("--out", default="", help="prefix for the render, mask and record; default textures/view_<view>")
        sub.add_argument("--photo-threshold", type=float, default=0.25,
                         help="a face was photographed when its normal's -Y component exceeds this")
        sub.add_argument("--facing", type=float, default=0.3,
                         help="a face is filled from a view when it faces that camera by at least this")
    commands.choices["prepare"].add_argument("--size", type=int, default=1024)
    commands.choices["prepare"].add_argument("--dilate", type=int, default=2,
                                              help="grow the mask this many pixels into the photographed surface")
    commands.choices["prepare"].add_argument("--edge-margin", type=int, default=3,
                                              help="keep the mask this many pixels clear of the background, so the outline is fixed")
    commands.choices["apply"].add_argument("--image", required=True, help="the completed picture")
    commands.choices["apply"].add_argument("--save", required=True)
    commands.choices["apply"].add_argument("--atlas", default="textures/view_fill_atlas.png")
    commands.choices["apply"].add_argument("--depth-tolerance", type=float, default=0.01,
                                            help="depth slack as a fraction of the scene's radius")
    args = parser.parse_args(bl_common.args_after_double_dash())
    args.out = args.out or f"textures/view_{args.view}"
    # Every file the command writes, not only the prefix: a prefix naming the workspace itself would put
    # "<workspace>.png" beside it, outside.
    for path in (args.out, f"{args.out}.png", f"{args.out}_mask.png", f"{args.out}.json"):
        bl_common.require_inside_workspace(path, "write")
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    if args.command == "prepare":
        prepare(args)
    else:
        bl_common.require_inside_workspace(args.image, "read")
        bl_common.require_inside_workspace(args.save, "save")
        bl_common.require_inside_workspace(args.atlas, "write")
        apply(args)


main()

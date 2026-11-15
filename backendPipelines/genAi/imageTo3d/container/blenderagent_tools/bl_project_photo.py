# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Project the photograph onto the model: faces that face the camera take the photograph; the rest keep the
colour the model arrived with, or take each row's colour when it arrived with none.

    blender -b scene.blend --factory-startup --python-exit-code 1 -P tools/bl_project_photo.py -- \\
        --reference refs/<front>_cutout.png --save scene.blend [--azimuth auto|0|90|180|270] \\
        [--atlas textures/projected_atlas.png] [--captured-colour srgb|linear] [--keep-tone]

For ONE mesh object -- run `bl_solidify.py` first on a build. The mesh is turned so the side whose outline
best matches the photograph faces -Y (or the azimuth you give), and the photograph is mapped onto the
faces that face the camera by orthographic projection, with the mesh's front-view bounding box fitted to
the photograph's subject.

A mesh that carries its own colour -- a reconstruction's vertex colour, imported on a refine -- keeps it
wherever the photograph was not taken. The photograph goes onto the vertices that squarely face the
camera, pass a depth test against the front view and land inside the cutout, and it fades into the
captured colour over a band of facing angle instead of ending at a seam. The captured colour is prepared
to sit beside it: near-black holes, the surfaces the reconstruction never saw, are flooded from their
neighbours; the surfaces the camera did not see are lifted to the photographed side's brightness, since
the capture carries the photograph's lighting and its shadow side would otherwise ship darker than the
front it meets; and the whole is brought to the photograph's tone band by band, a reconstruction's colour
being usually darker than the photograph it came from. That preparation happens once per mesh: a later
run on the same scene, retrying the alignment, rebuilds the weights and the material and leaves the
prepared colour as it is. The result is one material mixing the two over a fresh non-overlapping uv
layout, `SurfaceAtlas`, for `bl_bake.py` to bake into one image. Measured on a
long-haired cat and a feathered horse: replacing the captured colour outright with the projected
photograph and an inferred rest tripled the colour step between neighbouring vertices, and a blind judge
marked both surfaces down; the one refine of a furry subject that won kept the captured colour where the
photograph did not reach.

A mesh without colour of its own -- a build's solidified body -- takes the photograph on its camera-facing
faces and, on every other face, a band carrying the colour of the photograph's row at that height. One
image, one material. Anything the camera did not see is a flat colour there; say so in `notes.md` and
fill it from views.

`--captured-colour` says how the mesh's colour bytes were encoded. A glTF written by trimesh or by a
reconstruction model carries display (sRGB) bytes, which Blender's importer takes as linear -- the colour
then renders washed out and would bake washed out -- so the default decodes them. Give `linear` for a mesh
whose colour Blender itself wrote.
"""

import argparse
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402
import numpy as np  # noqa: E402

import bl_common  # noqa: E402
import photo_projection  # noqa: E402
import view_fill  # noqa: E402

BAND_ROWS = 256
GRID = 256
ATLAS_LAYER = "SurfaceAtlas"
PHOTO_LAYER = "PhotoProjection"
WEIGHT_ATTRIBUTE = "photo_weight"
# Set on the mesh once its captured colour has been decoded, flooded and tone-matched, so a later run rebuilds
# only the weights and the material.
PREPARED_FLAG = "captured_colour_prepared"
# Below this linear luminance (about 20 of 255) a captured colour is a hole the reconstruction never saw.
DARK_LUMINANCE = 0.007


def normalised_mask(mask, size=GRID):
    """The mask cropped to its content and scaled uniformly into a square canvas, like the pipeline does."""
    rows, cols = np.where(mask)
    if not len(rows):
        return np.zeros((size, size), dtype=bool)
    crop = mask[rows.min():rows.max() + 1, cols.min():cols.max() + 1]
    h, w = crop.shape
    scale = (size - 2) / max(h, w)
    th, tw = max(int(round(h * scale)), 1), max(int(round(w * scale)), 1)
    ys = np.clip((np.arange(th) / scale).astype(int), 0, h - 1)
    xs = np.clip((np.arange(tw) / scale).astype(int), 0, w - 1)
    resized = crop[ys][:, xs]
    canvas = np.zeros((size, size), dtype=bool)
    top, left = (size - th) // 2, (size - tw) // 2
    canvas[top:top + th, left:left + tw] = resized
    return canvas


def silhouette_from_front(points, size=GRID):
    """The outline of a point set seen from -Y, as a filled mask on a square canvas."""
    x, z = points[:, 0], points[:, 2]
    span = max(x.max() - x.min(), z.max() - z.min(), 1e-9)
    px = ((x - (x.min() + x.max()) / 2) / span + 0.5) * (size - 1)
    py = (0.5 - (z - (z.min() + z.max()) / 2) / span) * (size - 1)
    mask = np.zeros((size, size), dtype=bool)
    mask[np.clip(py.astype(int), 0, size - 1), np.clip(px.astype(int), 0, size - 1)] = True
    padded = np.pad(mask, 2)
    grown = padded.copy()
    for di in (-2, -1, 0, 1, 2):
        for dj in (-2, -1, 0, 1, 2):
            grown |= np.roll(np.roll(padded, di, axis=0), dj, axis=1)
    return grown[2:-2, 2:-2]


def rotate_z(points, degrees):
    a = math.radians(degrees)
    c, s = math.cos(a), math.sin(a)
    out = points.copy()
    out[:, 0] = c * points[:, 0] - s * points[:, 1]
    out[:, 1] = s * points[:, 0] + c * points[:, 1]
    return out


def captured_colour_attribute(mesh):
    """The colour attribute the mesh arrived with, or None."""
    attributes = mesh.color_attributes
    if not len(attributes):
        return None
    return attributes.active_color or attributes[0]


def read_vertex_colours(mesh, attribute, encoding):
    """One linear RGB row per vertex from a POINT or CORNER colour attribute."""
    values = np.empty(len(attribute.data) * 4, dtype=np.float32)
    attribute.data.foreach_get("color", values)
    rgb = values.reshape(-1, 4)[:, :3].astype(float)
    if attribute.domain == "CORNER":
        index = np.empty(len(mesh.loops), dtype=np.int64)
        mesh.loops.foreach_get("vertex_index", index)
        total = np.zeros((len(mesh.vertices), 3))
        count = np.zeros(len(mesh.vertices))
        np.add.at(total, index, rgb)
        np.add.at(count, index, 1.0)
        rgb = total / np.maximum(count, 1.0)[:, None]
    return photo_projection.srgb_to_linear(rgb) if encoding == "srgb" else rgb


def write_vertex_colours(mesh, attribute, colours):
    """Linear RGB rows per vertex back onto the attribute, whatever its domain."""
    if attribute.domain == "CORNER":
        index = np.empty(len(mesh.loops), dtype=np.int64)
        mesh.loops.foreach_get("vertex_index", index)
        colours = colours[index]
    rgba = np.concatenate([np.clip(colours, 0.0, 1.0), np.ones((len(colours), 1))], axis=1).astype(np.float32)
    attribute.data.foreach_set("color", rgba.reshape(-1))



def photo_pixels(vertices, frame):
    """Where each vertex lands on the photograph, (column, row), with the model's front box fitted onto
    the subject's box; and its fractions along that box."""
    r0, r1, c0, c1 = frame
    x_min, x_max = vertices[:, 0].min(), vertices[:, 0].max()
    z_min, z_max = vertices[:, 2].min(), vertices[:, 2].max()
    fx = (vertices[:, 0] - x_min) / max(x_max - x_min, 1e-9)
    fz = (z_max - vertices[:, 2]) / max(z_max - z_min, 1e-9)
    return fx, fz, c0 + fx * (c1 - c0), r0 + fz * (r1 - r0)


def unique_atlas_layer(obj, mesh):
    """A fresh non-overlapping uv layout for the bake to land in, made as `bl_bake.py` makes one."""
    mesh.uv_layers.active = mesh.uv_layers.new(name=ATLAS_LAYER)
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=1.15, island_margin=0.002)
    bpy.ops.object.mode_set(mode="OBJECT")
    return mesh.uv_layers[ATLAS_LAYER]


def project_over_row_colours(mesh, vertices, pixels, alpha, frame, args):
    """A mesh without colour of its own: the photograph on the camera-facing faces, a band carrying the
    photograph's row colour on every other face. Returns the summary line."""
    height, width = alpha.shape
    r0, r1, c0, c1 = frame

    # The atlas: the photograph, then BAND_ROWS rows of the subject's row-mean colours.
    total_height = height + BAND_ROWS
    combined = np.zeros((total_height, width, 4), dtype=np.float32)
    combined[:height] = pixels
    combined[:height, :, 3] = 1.0
    # Each band row runs from the colour just inside the photograph's LEFT outline to the colour just inside
    # its RIGHT outline. A face on the back or side then meets the front at the outline in the same colour,
    # so there is no seam; a row MEAN put a mixed colour behind a dark hairline and the join showed.
    crop_rows = np.clip(r0 + (np.arange(BAND_ROWS) + 0.5) / BAND_ROWS * (r1 - r0), 0, height - 1).astype(int)
    ramp = np.linspace(0.0, 1.0, width)[:, None]
    for band_row, src_row in enumerate(crop_rows):
        inside = np.where(alpha[src_row])[0]
        if len(inside) == 0:
            combined[height + band_row, :, :3] = (0.5, 0.5, 0.5)
        else:
            k = max(2, len(inside) // 20)
            left = pixels[src_row][inside[:k]][:, :3].mean(axis=0)
            right = pixels[src_row][inside[-k:]][:, :3].mean(axis=0)
            combined[height + band_row, :, :3] = left * (1 - ramp) + right * ramp
        combined[height + band_row, :, 3] = 1.0
    atlas = bpy.data.images.new("ProjectedAtlas", width, total_height, alpha=True)
    atlas.pixels.foreach_set(combined[::-1].reshape(-1))
    atlas.filepath_raw = os.path.abspath(args.atlas)
    atlas.file_format = "PNG"
    atlas.save()

    # Per-corner uv: the model's front-view box fitted onto the photograph's subject box.
    fx, fz, col, row = photo_pixels(vertices, frame)
    front_faces = 0
    uv_layer = mesh.uv_layers.active or mesh.uv_layers.new(name="UVMap")
    loop_uv = np.zeros((len(mesh.loops), 2), dtype=np.float32)
    for polygon in mesh.polygons:
        idx = list(polygon.vertices)
        pts = vertices[idx]
        normal = np.cross(pts[1] - pts[0], pts[2] - pts[0])
        length = np.linalg.norm(normal)
        facing = (-normal[1] / length) if length > 1e-12 else 0.0
        front = facing > args.front_threshold
        if front:
            front_faces += 1
        for loop_index, vertex_index in zip(polygon.loop_indices, idx):
            if front:
                loop_uv[loop_index] = (col[vertex_index] / width, 1.0 - row[vertex_index] / total_height)
            else:
                # u follows the model's own X, so a back face at the model's right edge reads the photograph's
                # right-edge colour and the two sides of the outline agree.
                loop_uv[loop_index] = (fx[vertex_index],
                                       1.0 - (height + fz[vertex_index] * (BAND_ROWS - 1) + 0.5) / total_height)
    uv_layer.data.foreach_set("uv", loop_uv.reshape(-1))

    material = bpy.data.materials.new("ProjectedPhoto")
    material.use_nodes = True
    nodes, links = material.node_tree.nodes, material.node_tree.links
    bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Roughness"].default_value = 0.8
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = atlas
    links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    mesh.materials.clear()
    mesh.materials.append(material)
    for polygon in mesh.polygons:
        polygon.material_index = 0
    return (f"projected {os.path.basename(args.reference)} onto {front_faces} of {len(mesh.polygons)} faces "
            f"({front_faces / max(len(mesh.polygons), 1):.0%} face the camera); the rest carry row colours; "
            f"atlas -> {args.atlas}; saved -> {args.save}",
            "Next: bl_render.py front and threequarter to see it, bl_export.py, agent_gate.py.")


def project_over_captured_colour(obj, mesh, vertices, pixels, alpha, frame, attribute_name, args):
    """A mesh with its own colour: the photograph where the camera took it, the captured colour -- holes
    flooded, tone matched -- everywhere else, mixed in one material. Returns the summary lines."""
    height, width = alpha.shape
    triangles = bl_common.triangles_of(mesh)
    attribute = mesh.color_attributes[attribute_name]
    # The captured colour is prepared once -- decoded from display bytes, holes flooded, shading and tone
    # matched -- because those are facts about the colour against the photograph, not about the alignment.
    # A second run on the same scene, an agent retrying the alignment, finds it prepared and rebuilds only
    # the weights and the material: decoding again crushed the colour dark and drove the tone match to its
    # clamp, and a clamped gain re-applied compounds, which two runs shipped as a noisier, darker coat.
    prepared = bool(mesh.get(PREPARED_FLAG))
    colours = read_vertex_colours(mesh, attribute, "linear" if prepared else args.captured_colour)
    flooded = 0
    if not prepared:
        edges = np.empty(len(mesh.edges) * 2, dtype=np.int64)
        mesh.edges.foreach_get("vertices", edges)
        colours, flooded = photo_projection.flood_dark(colours, edges.reshape(-1, 2), DARK_LUMINANCE)

    # What the camera saw: a depth test of the front view, in the frame the projection uses.
    fx, fz, col, row = photo_pixels(vertices, frame)
    cam_xy = np.column_stack([fx, 1.0 - fz])
    depth = vertices[:, 1]
    face_normals = view_fill.face_normals(vertices, triangles)
    zbuf, _ = view_fill.rasterise_view(cam_xy, depth, triangles, view_fill.facing(face_normals, 0, 0) > 0.0,
                                        args.raster, args.raster)
    radius = max(float(np.linalg.norm(vertices.max(axis=0) - vertices.min(axis=0))) / 2, 1e-4)
    visible = photo_projection.visible_vertices(cam_xy, depth, zbuf, radius * 0.01)
    icol = np.clip(np.rint(col), 0, width - 1).astype(int)
    irow = np.clip(np.rint(row), 0, height - 1).astype(int)
    # Within two pixels of the cutout's edge, or beyond it, is background, not the subject.
    on_photo = (~view_fill.dilate(~alpha, 2))[irow, icol]
    facing = -photo_projection.vertex_normals(vertices, triangles)[:, 1]
    weight = photo_projection.photo_weights(facing, visible, on_photo, args.front_threshold, args.blend_band)

    tone = "tone kept as captured"
    if prepared:
        tone = "captured colour already linear and tone-matched by an earlier run"
    elif not args.keep_tone:
        seen = weight > 0.5
        # The captured colour carries the photograph's lighting, so the surfaces the camera did not see are
        # in its shadow; they are lifted to the photographed side's brightness before the photograph's tone
        # is matched, so the kept coat meets the front at one brightness.
        shading = photo_projection.shading_gains(colours, seen, vertices[:, 2])
        colours = np.clip(colours * shading[:, None], 0.0, 1.0)
        lifted = f", unseen side x{float(shading[~seen].mean()):.2f}" if (~seen).any() else ""
        photo_linear = photo_projection.srgb_to_linear(pixels[irow, icol, :3])
        gains = photo_projection.band_gains(colours[seen], photo_linear[seen], vertices[seen, 2], vertices[:, 2])
        before = float((colours @ photo_projection.LUMA).mean())
        colours = np.clip(colours * gains, 0.0, 1.0)
        tone = (f"brought to the photograph's tone (brightness x"
                f"{float((colours @ photo_projection.LUMA).mean()) / max(before, 1e-6):.2f}{lifted})")

    # The photograph as the material's image, its edge colour carried outward so a face at the outline
    # never samples the cutout's background.
    extended = photo_projection.extend_edge_colours(pixels[..., :3], alpha, rounds=8)
    photo = bpy.data.images.new("ProjectedPhoto", width, height, alpha=True)
    rgba = np.concatenate([extended, np.ones((height, width, 1))], axis=2).astype(np.float32)
    photo.pixels.foreach_set(rgba[::-1].reshape(-1))
    photo.filepath_raw = os.path.abspath(args.atlas)
    photo.file_format = "PNG"
    photo.save()

    # Two uv layers: the bake's own layout first, so it is the one a glTF reader takes, then the projection.
    for layer in list(mesh.uv_layers):
        mesh.uv_layers.remove(layer)
    unique_atlas_layer(obj, mesh)
    loop_vertex = np.empty(len(mesh.loops), dtype=np.int64)
    mesh.loops.foreach_get("vertex_index", loop_vertex)
    photo_layer = mesh.uv_layers.new(name=PHOTO_LAYER)
    loop_uv = np.column_stack([col[loop_vertex] / width, 1.0 - row[loop_vertex] / height]).astype(np.float32)
    photo_layer.data.foreach_set("uv", loop_uv.reshape(-1))
    mesh.uv_layers.active = mesh.uv_layers[ATLAS_LAYER]
    mesh.uv_layers[ATLAS_LAYER].active_render = True

    # Attributes are fetched again: edit mode may have moved them.
    write_vertex_colours(mesh, mesh.color_attributes[attribute_name], colours)
    mesh[PREPARED_FLAG] = True
    weights = mesh.attributes.get(WEIGHT_ATTRIBUTE) or mesh.attributes.new(WEIGHT_ATTRIBUTE, "FLOAT", "POINT")
    weights.data.foreach_set("value", weight.astype(np.float32))

    material = bpy.data.materials.new("ProjectedPhoto")
    material.use_nodes = True
    nodes, links = material.node_tree.nodes, material.node_tree.links
    bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Roughness"].default_value = 0.8
    captured = nodes.new("ShaderNodeVertexColor")
    captured.layer_name = attribute_name
    uv_node = nodes.new("ShaderNodeUVMap")
    uv_node.uv_map = PHOTO_LAYER
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = photo
    links.new(uv_node.outputs["UV"], tex.inputs["Vector"])
    weight_node = nodes.new("ShaderNodeAttribute")
    weight_node.attribute_name = WEIGHT_ATTRIBUTE
    mix = nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"

    def socket(sockets, identifier):
        return next(s for s in sockets if s.identifier == identifier)

    links.new(captured.outputs["Color"], socket(mix.inputs, "A_Color"))
    links.new(tex.outputs["Color"], socket(mix.inputs, "B_Color"))
    links.new(weight_node.outputs["Fac"], socket(mix.inputs, "Factor_Float"))
    links.new(socket(mix.outputs, "Result_Color"), bsdf.inputs["Base Color"])
    mesh.materials.clear()
    mesh.materials.append(material)
    for polygon in mesh.polygons:
        polygon.material_index = 0

    wears = weight[triangles].mean(axis=1) > 0.5
    return (f"projected {os.path.basename(args.reference)} onto {int(wears.sum())} of {len(triangles)} faces "
            f"({wears.mean():.0%} squarely faced the camera, passed the depth test and lie inside the cutout); "
            f"kept the reconstruction's own colour on the rest, {tone}; flooded {flooded} near-black vertices "
            f"from their neighbours; photo -> {args.atlas}; saved -> {args.save}",
            f"Next: bl_bake.py bakes the mix into the {ATLAS_LAYER} layout as one image; then bl_render.py, "
            "bl_export.py, agent_gate.py -- read COLOUR NOISE, which a texture step must not raise.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", required=True, help="the FRONT reference cutout (RGBA)")
    parser.add_argument("--save", required=True)
    parser.add_argument("--azimuth", default="auto", help="auto, or the degrees to turn the model about Z first")
    parser.add_argument("--atlas", default="textures/projected_atlas.png")
    parser.add_argument("--front-threshold", type=float, default=0.25,
                        help="a face takes the photograph when its normal's -Y component exceeds this")
    parser.add_argument("--blend-band", type=float, default=0.35,
                        help="on a mesh with its own colour, the photograph fades in over this much facing past the threshold")
    parser.add_argument("--captured-colour", choices=("srgb", "linear"), default="srgb",
                        help="how the mesh's own colour bytes are encoded; a glTF from trimesh or a reconstruction model is srgb")
    parser.add_argument("--keep-tone", action="store_true",
                        help="leave the captured colour's tone as it is instead of matching the photograph band by band")
    parser.add_argument("--raster", type=int, default=512, help="pixels across the depth test of the front view")
    args = parser.parse_args(bl_common.args_after_double_dash())
    bl_common.require_inside_workspace(args.save, "save")
    bl_common.require_inside_workspace(args.atlas, "write")
    os.makedirs(os.path.dirname(args.atlas) or ".", exist_ok=True)

    objects = bl_common.mesh_objects()
    if len(objects) != 1:
        raise SystemExit(f"The scene has {len(objects)} mesh objects; run bl_solidify.py first so there is one")
    obj = objects[0]
    mesh = obj.data
    matrix = obj.matrix_world
    vertices = np.array([matrix @ v.co for v in mesh.vertices], dtype=float)

    pixels, width, height = bl_common.load_rgba(args.reference)
    alpha = pixels[:, :, 3] > 0.5
    rows, cols = np.where(alpha)
    if not len(rows):
        raise SystemExit("The reference has no opaque pixels; is it the cutout?")
    frame = (rows.min(), rows.max() + 1, cols.min(), cols.max() + 1)
    reference = normalised_mask(alpha)

    # Which way is the front: the side whose outline matches the photograph best, unless told.
    if args.azimuth == "auto":
        scores = {}
        for degrees in (0, 90, 180, 270):
            turned = rotate_z(vertices, degrees)
            model = normalised_mask(silhouette_from_front(turned))
            union = np.logical_or(model, reference).sum()
            scores[degrees] = float(np.logical_and(model, reference).sum() / union) if union else 0.0
        chosen = max(scores, key=scores.get)
        print("outline match by turn: " + ", ".join(f"{k} deg {v:.3f}" for k, v in scores.items())
              + f" -> turning {chosen} deg", flush=True)
    else:
        chosen = float(args.azimuth)
    if chosen:
        vertices = rotate_z(vertices, chosen)
    # Recentre on X and Y, feet stay where they are.
    vertices[:, 0] -= (vertices[:, 0].min() + vertices[:, 0].max()) / 2
    vertices[:, 1] -= (vertices[:, 1].min() + vertices[:, 1].max()) / 2
    inverse = matrix.inverted()
    for vertex, point in zip(mesh.vertices, vertices):
        vertex.co = inverse @ bl_common.Vector(point)
    mesh.update()

    attribute = captured_colour_attribute(mesh)
    if attribute is None:
        summary = project_over_row_colours(mesh, vertices, pixels, alpha, frame, args)
    else:
        summary = project_over_captured_colour(obj, mesh, vertices, pixels, alpha, frame, attribute.name, args)

    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.save))
    for line in summary:
        print(line)


main()

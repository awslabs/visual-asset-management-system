# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A starting body whose FRONT outline is the photograph's, inflated to a depth, with the photograph on it.

    blender -b --factory-startup --python-exit-code 1 -P tools/bl_silhouette_base.py -- \\
        --reference refs/<front>_cutout.png --height 1.7 --save scene.blend \\
        [--depth-ratio 0.8] [--resolution 200] [--name Subject]

Six builds from primitives scored 3 for silhouette and 2 for structure against a photograph, every time.
The outline and the proportions are the part of the job the photograph already answers exactly, so this
reads them off it: the cutout's alpha becomes a pixel grid, the grid becomes a closed pillow -- a front
sheet and a back sheet joined along the outline -- and the pillow is inflated so each horizontal run of it -- a
head, a torso, an arm -- has a cross-section ``depth-ratio`` times as deep as it is wide, rounded like a body's. The photograph itself is projected onto the front as the material; the back gets each row's mean
colour, so the hair is hair-coloured from behind and the shirt is shirt-coloured. It sits with its feet on
Z=0, centred on X, facing -Y, at the height you give it in metres.

What it gets right by construction: the front silhouette, every proportion visible in the photograph, the
colour of every visible part. What it gets wrong, which is the work: depth. A pillow is not a person from
the side -- the head wants to be rounder than the torso, the arms rounder than the hands. Use
`bl_reshape.py` to scale the depth of horizontal bands, add the parts that project (a nose, ears, a tail)
as primitives sunk into the body, and check every change with `agent_gate.py`. Solidify at the end as usual.
"""

import argparse
import math
import os
import sys
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bmesh  # noqa: E402
import bpy  # noqa: E402
import numpy as np  # noqa: E402

import bl_common  # noqa: E402



def largest_component(mask):
    """The largest 4-connected region of a boolean grid."""
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    best = None
    for start in zip(*np.where(mask)):
        if seen[start]:
            continue
        queue = deque([start])
        seen[start] = True
        members = []
        while queue:
            i, j = queue.popleft()
            members.append((i, j))
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                a, b = i + di, j + dj
                if 0 <= a < h and 0 <= b < w and mask[a, b] and not seen[a, b]:
                    seen[a, b] = True
                    queue.append((a, b))
        if best is None or len(members) > len(best):
            best = members
    out = np.zeros_like(mask, dtype=bool)
    if best:
        rows, cols = zip(*best)
        out[list(rows), list(cols)] = True
    return out


def tidy(mask):
    """Fill one-pixel holes and drop pixels that touch the body by a single side, which would leave a
    non-manifold seam in the pillow."""
    mask = mask.copy()
    for _ in range(3):
        padded = np.pad(mask, 1)
        neighbours = (padded[:-2, 1:-1].astype(int) + padded[2:, 1:-1] + padded[1:-1, :-2] + padded[1:-1, 2:])
        mask = mask | ((~mask) & (neighbours >= 3))
        mask = mask & (neighbours >= 2)
    return largest_component(mask)


def distance_to_outline(mask):
    """Steps from each inside pixel to the nearest outside pixel, four-connected."""
    h, w = mask.shape
    dist = np.full(mask.shape, -1, dtype=int)
    queue = deque()
    padded = np.pad(mask, 1, constant_values=False)
    for i, j in zip(*np.where(mask)):
        if not (padded[i, j + 1] and padded[i + 2, j + 1] and padded[i + 1, j] and padded[i + 1, j + 2]):
            dist[i, j] = 1
            queue.append((i, j))
    while queue:
        i, j = queue.popleft()
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            a, b = i + di, j + dj
            if 0 <= a < h and 0 <= b < w and mask[a, b] and dist[a, b] < 0:
                dist[a, b] = dist[i, j] + 1
                queue.append((a, b))
    return dist


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", required=True, help="the FRONT reference cutout (RGBA)")
    parser.add_argument("--height", type=float, required=True, help="real-world height in metres")
    parser.add_argument("--save", required=True)
    parser.add_argument("--depth-ratio", type=float, default=0.8,
                        help="depth of each part as a fraction of its own width (a round limb is 1.0)")
    parser.add_argument("--resolution", type=int, default=200, help="grid pixels along the longer side")
    parser.add_argument("--name", default="Subject")
    parser.add_argument("--back-texture", default="textures/silhouette_atlas.png",
                        help="where the combined photograph-plus-row-colours image is written")
    args = parser.parse_args(bl_common.args_after_double_dash())
    bl_common.require_inside_workspace(args.save, "save")
    bl_common.require_inside_workspace(args.back_texture, "write")
    os.makedirs(os.path.dirname(args.back_texture) or ".", exist_ok=True)

    pixels, width, height = bl_common.load_rgba(args.reference)
    alpha = pixels[:, :, 3] > 0.5
    rows, cols = np.where(alpha)
    if not len(rows):
        raise SystemExit("The reference has no opaque pixels; is it the cutout?")
    r0, r1, c0, c1 = rows.min(), rows.max() + 1, cols.min(), cols.max() + 1
    crop_h, crop_w = r1 - r0, c1 - c0
    scale = args.resolution / max(crop_h, crop_w)
    h = max(int(round(crop_h * scale)), 2)
    w = max(int(round(crop_w * scale)), 2)
    src_rows = np.clip((np.arange(h) + 0.5) / scale + r0, 0, height - 1).astype(int)
    src_cols = np.clip((np.arange(w) + 0.5) / scale + c0, 0, width - 1).astype(int)
    small = pixels[src_rows][:, src_cols]
    mask = tidy(small[:, :, 3] > 0.5)
    if mask.sum() < 16:
        raise SystemExit("The reference outline is too small at this resolution; raise --resolution")

    dist = distance_to_outline(mask)
    dmax = max(int(dist.max()), 1)
    unit = args.height / h  # metres per grid pixel
    # Each horizontal run of the outline -- a head, a torso, an arm hanging clear of the body -- is inflated
    # against its OWN width, so its cross-section is an ellipse of depth ``depth_ratio`` times its width. An
    # inflation against the whole figure's widest point left the head and the limbs nearly flat, which is
    # what a judge saw first in the result.
    thickness = np.zeros(mask.shape, dtype=float)
    for i in range(h):
        row = mask[i]
        j = 0
        while j < w:
            if not row[j]:
                j += 1
                continue
            k = j
            while k < w and row[k]:
                k += 1
            run = slice(j, k)
            run_dmax = max(int(dist[i, run].max()), 1)
            half = 0.5 * args.depth_ratio * (k - j) * unit
            thickness[i, run] = half * np.sqrt(np.clip(dist[i, run], 0, None) / run_dmax)
            j = k
    half_depth = float(thickness.max())
    print(f"silhouette base: grid {w}x{h}, {int(mask.sum())} filled pixels, {unit * 1000:.1f} mm per pixel, "
          f"depth at the thickest point {2 * half_depth:.3f} m", flush=True)

    # Corner grid (h+1)x(w+1): a corner is used when any adjacent pixel is inside.
    padded = np.pad(mask, 1, constant_values=False)
    corner_used = padded[:-1, :-1] | padded[:-1, 1:] | padded[1:, :-1] | padded[1:, 1:]
    padded_t = np.pad(thickness, 1)
    padded_n = padded.astype(float)
    corner_t = ((padded_t[:-1, :-1] + padded_t[:-1, 1:] + padded_t[1:, :-1] + padded_t[1:, 1:])
                / np.maximum(padded_n[:-1, :-1] + padded_n[:-1, 1:] + padded_n[1:, :-1] + padded_n[1:, 1:], 1))
    corner_t = np.maximum(corner_t, 0.35 * half_depth / math.sqrt(dmax))

    bm = bmesh.new()
    uv_layer = bm.loops.layers.uv.new("UVMap")
    front = {}
    back = {}
    for i, j in zip(*np.where(corner_used)):
        x = (j - w / 2.0) * unit
        z = (h - i) * unit
        front[(i, j)] = bm.verts.new((x, -corner_t[i, j], z))
        back[(i, j)] = bm.verts.new((x, corner_t[i, j], z))
    bm.verts.ensure_lookup_table()

    # One image holds everything: the photograph as it is, and below it a band of ``h`` rows carrying each
    # grid row's mean colour for the back and sides. One image, one material: two materials on one body
    # once made the pipeline's loader pack two images into one atlas and mangle the coordinates, and a
    # 4-pixel-wide strip once made Blender's unwrap squash every island to its aspect ratio.
    total_height = height + h

    def uv_front(i, j):
        return ((c0 + j / scale) / width, 1.0 - (r0 + i / scale) / total_height)

    def uv_back(i, j):
        # u follows the grid column, so the back and sides meet the front at the outline in the same colour.
        return (j / max(w, 1), 1.0 - (height + i + 0.5) / total_height)

    faces = 0
    for i, j in zip(*np.where(mask)):
        corners = [(i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)]
        f = bm.faces.new([front[c] for c in corners])
        f.material_index = 0
        for loop, c in zip(f.loops, corners):
            loop[uv_layer].uv = uv_front(*c)
        b = bm.faces.new([back[c] for c in reversed(corners)])
        b.material_index = 0
        for loop, c in zip(b.loops, reversed(corners)):
            loop[uv_layer].uv = uv_back(*c)
        faces += 2
        # Side walls where the outline runs.
        for (di, dj), (ca, cb) in (((-1, 0), ((i, j), (i, j + 1))), ((1, 0), ((i + 1, j + 1), (i + 1, j))),
                                   ((0, -1), ((i + 1, j), (i, j))), ((0, 1), ((i, j + 1), (i + 1, j + 1)))):
            a, b_ = i + di, j + dj
            if 0 <= a < h and 0 <= b_ < w and mask[a, b_]:
                continue
            # Side walls take the row colour, like the back: the front's edge pixel is often a dark
            # outline, and stretching it across a wall paints a black band around the whole figure.
            side = bm.faces.new([front[ca], front[cb], back[cb], back[ca]])
            side.material_index = 0
            for loop, c in zip(side.loops, (ca, cb, cb, ca)):
                loop[uv_layer].uv = uv_back(*c)
            faces += 1
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    # Two gentle smoothing passes round the pixel staircase of the outline and the pillow's edge without
    # moving the silhouette by more than a pixel.
    for _ in range(2):
        bmesh.ops.smooth_vert(bm, verts=bm.verts, factor=0.5, use_axis_x=True, use_axis_y=True, use_axis_z=True)

    # Scene: a fresh one holding only this body, its camera and light added by the tools later.
    bpy.ops.wm.read_homefile(use_empty=True)
    mesh = bpy.data.meshes.new(args.name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(args.name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    for polygon in mesh.polygons:
        polygon.use_smooth = True

    # The combined image: the photograph, then a band with each grid row's mean colour.
    combined = np.zeros((total_height, width, 4), dtype=np.float32)
    combined[:height] = pixels
    combined[:height, :, 3] = 1.0
    # Each band row runs from the colour just inside the left outline to the colour just inside the right
    # one, so the back and the sides join the front without a seam; a row mean put a mixed colour behind a
    # dark hairline and the join showed at the crown.
    rgb = small[:, :, :3]
    ramp = np.linspace(0.0, 1.0, width)[:, None]
    for i in range(h):
        inside = np.where(mask[i])[0]
        if len(inside) == 0:
            combined[height + i, :, :3] = (0.5, 0.5, 0.5)
        else:
            k = max(1, len(inside) // 12)
            left = rgb[i][inside[:k]].mean(axis=0)
            right = rgb[i][inside[-k:]].mean(axis=0)
            combined[height + i, :, :3] = left * (1 - ramp) + right * ramp
        combined[height + i, :, 3] = 1.0
    atlas = bpy.data.images.new(f"{args.name}_atlas", width, total_height, alpha=True)
    atlas.pixels.foreach_set(combined[::-1].reshape(-1))
    atlas.filepath_raw = os.path.abspath(args.back_texture)
    atlas.file_format = "PNG"
    atlas.save()
    material = bpy.data.materials.new(f"{args.name}_photo")
    material.use_nodes = True
    nodes, links = material.node_tree.nodes, material.node_tree.links
    bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Roughness"].default_value = 0.8
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = atlas
    links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    mesh.materials.append(material)

    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.save))
    report = bl_common.mesh_report()
    print(f"saved -> {args.save}: {faces} faces, dimensions {report.get('sceneDimensions')} m, "
          f"front outline from {os.path.basename(args.reference)}, atlas -> {args.back_texture}")
    print("Next: bl_render.py and agent_gate.py to see where it stands, then bl_reshape.py for the depth of "
          "each band, primitives sunk into it for what projects, bl_solidify.py, bl_export.py.")


main()

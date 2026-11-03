# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A mask over the atlas of the texels that do NOT face the camera: the surface the photograph never saw.

    blender -b scene.blend --factory-startup --python-exit-code 1 -P tools/bl_uv_mask.py -- \\
        --out textures/inferred_mask.png [--threshold 0.25] [--dilate 4]

For the ONE mesh in the scene, every face whose normal does not point towards -Y by at least ``threshold``
is rasterised into a white region over the mesh's active UV layout, at the size of the material's image;
everything the camera saw stays black. Dilated a few texels so the edge of the photographed region is
included, which is where an inpainting model needs to read the material it continues. Feed it to
`agent_image_edit.py inpaint` together with the atlas.

Run it AFTER `bl_solidify.py` (or `bl_project_photo.py`), when the atlas has a real layout: before that the
inferred surfaces live in a thin colour band that no image model can do anything useful with.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402
import numpy as np  # noqa: E402

import bl_common  # noqa: E402


def rasterise(uv, faces, size):
    """A boolean image with each uv triangle filled (v flipped to image rows)."""
    painted = np.zeros((size, size), dtype=bool)
    x = np.clip(uv[:, 0], 0, 1) * (size - 1)
    y = (1.0 - np.clip(uv[:, 1], 0, 1)) * (size - 1)
    for face in faces:
        fx, fy = x[face], y[face]
        if not (np.isfinite(fx).all() and np.isfinite(fy).all()):
            continue
        area = (fx[1] - fx[0]) * (fy[2] - fy[0]) - (fx[2] - fx[0]) * (fy[1] - fy[0])
        if abs(area) < 1e-9:
            continue
        x0, x1 = int(max(np.floor(fx.min()), 0)), int(min(np.ceil(fx.max()) + 1, size))
        y0, y1 = int(max(np.floor(fy.min()), 0)), int(min(np.ceil(fy.max()) + 1, size))
        if x0 >= x1 or y0 >= y1:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1, dtype=float), np.arange(y0, y1, dtype=float))
        w1 = ((gx - fx[0]) * (fy[2] - fy[0]) - (gy - fy[0]) * (fx[2] - fx[0])) / area
        w2 = ((gy - fy[0]) * (fx[1] - fx[0]) - (gx - fx[0]) * (fy[1] - fy[0])) / area
        w0 = 1.0 - w1 - w2
        slack = 0.6 / max(abs(area) ** 0.5, 1.0)
        painted[y0:y1, x0:x1] |= (w0 >= -slack) & (w1 >= -slack) & (w2 >= -slack)
    return painted


def dilate(mask, steps):
    for _ in range(steps):
        padded = np.pad(mask, 1)
        mask = (padded[1:-1, 1:-1] | padded[:-2, 1:-1] | padded[2:, 1:-1] | padded[1:-1, :-2] | padded[1:-1, 2:])
    return mask


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="textures/inferred_mask.png")
    parser.add_argument("--threshold", type=float, default=0.25, help="a face faces the camera when -normal.y exceeds this")
    parser.add_argument("--dilate", type=int, default=4)
    parser.add_argument("--size", type=int, default=0, help="mask size; 0 takes the material image's size")
    args = parser.parse_args(bl_common.args_after_double_dash())
    bl_common.require_inside_workspace(args.out, "write")
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)

    objects = bl_common.mesh_objects()
    if len(objects) != 1:
        raise SystemExit(f"The scene has {len(objects)} mesh objects; run bl_solidify.py first so there is one")
    obj = objects[0]
    mesh = obj.data
    if not mesh.uv_layers.active:
        raise SystemExit("The mesh has no UV layout; run bl_solidify.py or bl_project_photo.py first")

    size = args.size
    if not size:
        for material in mesh.materials:
            if material and material.use_nodes:
                for node in material.node_tree.nodes:
                    if node.type == "TEX_IMAGE" and node.image:
                        size = max(node.image.size)
        size = size or 2048

    matrix = obj.matrix_world
    world = np.array([matrix @ v.co for v in mesh.vertices], dtype=float)
    uv_layer = mesh.uv_layers.active.data
    triangles, inferred_faces, camera_faces = [], 0, 0
    for polygon in mesh.polygons:
        idx = list(polygon.vertices)
        pts = world[idx]
        normal = np.cross(pts[1] - pts[0], pts[2] - pts[0])
        length = np.linalg.norm(normal)
        facing = (-normal[1] / length) if length > 1e-12 else 0.0
        if facing > args.threshold:
            camera_faces += 1
            continue
        inferred_faces += 1
        loops = list(polygon.loop_indices)
        corners = [uv_layer[l].uv[:] for l in loops]
        for k in range(1, len(corners) - 1):  # fan-triangulate an n-gon
            triangles.append((corners[0], corners[k], corners[k + 1]))
    flat = np.array([c for tri in triangles for c in tri], dtype=float).reshape(-1, 2) if triangles else np.zeros((0, 2))
    faces = np.arange(len(flat)).reshape(-1, 3)
    mask = dilate(rasterise(flat, faces, size), args.dilate) if len(faces) else np.zeros((size, size), dtype=bool)

    image = bpy.data.images.new("InferredMask", size, size, alpha=False)
    pixels = np.zeros((size, size, 4), dtype=np.float32)
    pixels[..., :3] = mask[..., None].astype(np.float32)
    pixels[..., 3] = 1.0
    image.pixels.foreach_set(pixels[::-1].reshape(-1))
    image.filepath_raw = os.path.abspath(args.out)
    image.file_format = "PNG"
    image.save()
    coverage = float(mask.mean())
    print(f"mask -> {args.out}: {inferred_faces} faces the camera did not see ({inferred_faces / max(inferred_faces + camera_faces, 1):.0%}), "
          f"{coverage:.1%} of the {size}x{size} atlas marked for filling")


main()

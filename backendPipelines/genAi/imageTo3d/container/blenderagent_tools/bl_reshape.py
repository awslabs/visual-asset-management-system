# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Scale the depth or width of a horizontal band of the model, with a soft edge.

    blender -b scene.blend --factory-startup --python-exit-code 1 -P tools/bl_reshape.py -- \\
        --band 0.82:1.0 --depth 1.4 [--width 1.0] [--object Subject] [--feather 0.04] --save scene.blend

``--band`` is a range of the model's height, 0 at the feet and 1 at the top. ``--depth`` multiplies each
vertex's distance from the model's Y centre-line in that band (1.4 makes the head rounder from the side),
``--width`` does the same for X. The scaling fades over ``--feather`` of the height either side of the
band so the surface does not step. Run `agent_gate.py` after it: the silhouette bands tell you which range
to reshape and the height/width figure tells you by how much.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402

import bl_common  # noqa: E402


def parse_band(text):
    lo, hi = (float(part) for part in text.split(":"))
    if not (0.0 <= lo < hi <= 1.0):
        raise SystemExit("--band wants two fractions of the height, low:high, between 0 and 1")
    return lo, hi


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--band", required=True)
    parser.add_argument("--depth", type=float, default=1.0)
    parser.add_argument("--width", type=float, default=1.0)
    parser.add_argument("--feather", type=float, default=0.04)
    parser.add_argument("--object", default="")
    parser.add_argument("--save", required=True)
    args = parser.parse_args(bl_common.args_after_double_dash())
    bl_common.require_inside_workspace(args.save, "save")
    lo, hi = parse_band(args.band)

    objects = [o for o in bl_common.mesh_objects() if not args.object or o.name == args.object]
    if not objects:
        raise SystemExit("No mesh object to reshape" + (f" named {args.object}" if args.object else ""))
    bounds = bl_common.scene_bounds()
    z_min, z_max = bounds[0].z, bounds[1].z
    span = max(z_max - z_min, 1e-9)
    moved = 0
    for obj in objects:
        matrix = obj.matrix_world
        inverse = matrix.inverted()
        world = [matrix @ v.co for v in obj.data.vertices]
        ys = [p.y for p in world]
        xs = [p.x for p in world]
        y_centre = (min(ys) + max(ys)) / 2.0
        x_centre = (min(xs) + max(xs)) / 2.0
        for vertex, point in zip(obj.data.vertices, world):
            frac = (point.z - z_min) / span
            if frac < lo - args.feather or frac > hi + args.feather:
                continue
            weight = 1.0
            if frac < lo:
                weight = (frac - (lo - args.feather)) / args.feather
            elif frac > hi:
                weight = ((hi + args.feather) - frac) / args.feather
            depth = 1.0 + (args.depth - 1.0) * weight
            width = 1.0 + (args.width - 1.0) * weight
            point.y = y_centre + (point.y - y_centre) * depth
            point.x = x_centre + (point.x - x_centre) * width
            vertex.co = inverse @ point
            moved += 1
        obj.data.update()
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.save))
    report = bl_common.mesh_report()
    print(f"reshaped {moved} vertices in band {lo:.2f}-{hi:.2f}: depth x{args.depth}, width x{args.width}; "
          f"dimensions now {report.get('sceneDimensions')}; saved -> {args.save}")


main()

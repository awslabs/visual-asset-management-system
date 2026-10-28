# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Start a scene from an existing mesh, for a run that REFINES rather than builds.

    blender -b --factory-startup --python-exit-code 1 -P tools/bl_import.py -- \
        --mesh start/model.glb --save scene.blend

Imports GLB/glTF, OBJ, STL or PLY into an empty scene, drops any cameras and lights the file carried,
recentres the result on the origin with its lowest point on Z=0, and saves the .blend the agent then
works in. Textures travel with a GLB and are kept.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

import bl_common  # noqa: E402


def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for block in (bpy.data.meshes, bpy.data.materials, bpy.data.images, bpy.data.cameras, bpy.data.lights):
        for datum in list(block):
            if datum.users == 0:
                block.remove(datum)


def import_mesh(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".obj":
        bpy.ops.wm.obj_import(filepath=path)
    elif ext == ".stl":
        bpy.ops.wm.stl_import(filepath=path)
    elif ext == ".ply":
        bpy.ops.wm.ply_import(filepath=path)
    else:
        raise SystemExit(f"Unsupported starting mesh format: {ext}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mesh", required=True)
    parser.add_argument("--save", required=True)
    args = parser.parse_args(bl_common.args_after_double_dash())
    bl_common.require_inside_workspace(args.save, "save")

    clear_scene()
    import_mesh(args.mesh)
    # Anything that is not geometry came along for the ride and would end up in the export.
    for obj in list(bpy.context.scene.objects):
        if obj.type in ("CAMERA", "LIGHT", "EMPTY") and not obj.children:
            bpy.data.objects.remove(obj, do_unlink=True)

    bounds = bl_common.scene_bounds()
    if bounds is None:
        raise SystemExit(f"{args.mesh} imported no mesh")
    lo, hi = bounds
    offset = Vector((-(lo.x + hi.x) / 2, -(lo.y + hi.y) / 2, -lo.z))
    for obj in bl_common.mesh_objects():
        obj.location += offset
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.save))
    report = bl_common.mesh_report()
    print(f"imported {args.mesh}: {report['meshCount']} meshes, dimensions {report.get('sceneDimensions')}")
    print(f"saved -> {args.save}")


main()

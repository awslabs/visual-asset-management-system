# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Export the scene's meshes as one glTF binary with textures embedded, and prove it by re-importing.

    blender -b scene.blend --factory-startup --python-exit-code 1 -P tools/bl_export.py -- \
        --out output/model.glb

Applies modifiers, exports only mesh objects (no cameras, lights or empties), embeds images, and then
opens the written file in a fresh scene and compares mesh and face counts against what was exported. A
"done" that has not been re-read is not done -- an export that silently dropped a modifier, a material or
half the objects looks identical from the outside.

When every material's Base Color reads an image -- the colour has been baked -- no vertex colour goes into
the file: a glTF viewer multiplies a vertex colour into the base colour, so a captured colour left beside
the baked image darkens it. A mesh whose colour IS its vertex colour keeps it.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402

import bl_common  # noqa: E402


def counts():
    objects = bl_common.mesh_objects()
    # An image re-imported from a GLB is packed and loads lazily, so ``has_data`` is False until it is
    # touched and the count comes out zero for a file that carries its textures. Asking for the size forces
    # the load; a packed image or one with a real size is an image the file carries. The first agent run
    # reported this as a defect in the tool rather than in its model, and it was right.
    def carried(image):
        if image.name in ("Render Result", "Viewer Node"):
            return False
        if image.packed_file is not None:
            return True
        try:
            return image.size[0] > 0 and image.size[1] > 0
        except Exception:
            return False

    # glTF stores triangles, so a quad mesh comes back with twice the polygon count and an n-gon with
    # more; compare the surface in triangles, which is the one count an export cannot change.
    return {"meshes": len(objects), "faces": sum(len(o.data.polygons) for o in objects),
            "triangles": sum(max(len(p.vertices) - 2, 0) for o in objects for p in o.data.polygons),
            "materials": len({m.name for o in objects for m in o.data.materials if m}),
            "images": len([i for i in bpy.data.images if carried(i)])}


def colour_is_baked(objects):
    """Whether every material's Base Color reads an image, so the vertex colour is no longer the colour."""
    materials = [m for o in objects for m in o.data.materials if m]
    if not materials:
        return False
    for material in materials:
        if not material.use_nodes:
            return False
        bsdf = next((n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        links = bsdf.inputs["Base Color"].links if bsdf is not None else ()
        if not links or links[0].from_node.type != "TEX_IMAGE":
            return False
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args(bl_common.args_after_double_dash())
    bl_common.require_inside_workspace(args.out, "export")
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)

    objects = bl_common.mesh_objects()
    if not objects:
        raise SystemExit("Nothing to export: the scene has no mesh objects")

    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    before = counts()

    bpy.ops.export_scene.gltf(
        filepath=os.path.abspath(args.out),
        export_format="GLB",
        use_selection=True,
        export_apply=True,
        export_cameras=False,
        export_lights=False,
        export_image_format="AUTO",
        export_texcoords=True,
        export_normals=True,
        export_materials="EXPORT",
        export_vertex_color="NONE" if colour_is_baked(objects) else "MATERIAL",
        export_yup=True,
    )
    size = os.path.getsize(args.out)
    print(f"exported -> {args.out} ({size} bytes); scene had {json.dumps(before)}")

    # Verify by re-import into a clean scene.
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=os.path.abspath(args.out))
    after = counts()
    verdict = {"exported": before, "reimported": after, "sizeBytes": size,
               "facesMatch": before["triangles"] == after["triangles"],
               "hasImages": after["images"] > 0,
               "hasMaterials": after["materials"] > 0}
    bl_common.write_json(os.path.splitext(args.out)[0] + "_export_check.json", verdict)
    print(json.dumps(verdict, indent=2))
    if not verdict["facesMatch"]:
        raise SystemExit("Re-import face count differs from the export; the file is not what the scene was")


main()

# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bake every mesh's material to an image texture, so the colour survives the glTF export.

    blender -b scene.blend --factory-startup --python-exit-code 1 -P tools/bl_bake.py -- \
        --save scene.blend [--size 2048] [--image textures/baked.png]

glTF carries image textures and nothing else: a procedural node tree -- noise, gradients, colour ramps,
anything computed -- is dropped on export and the model arrives grey. This bakes whatever each material
currently produces into one image over a fresh UV layout, then rewires every material to read that image
as its Base Color. Run it once the materials look right in a render and before exporting.

Headless baking has three requirements this handles: Cycles (the only engine that bakes), a UV layer
(a smart projection is made if none exists), and an image texture node that is ACTIVE in each material so
Blender knows where to bake to.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy  # noqa: E402

import bl_common  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--save", required=True)
    parser.add_argument("--size", type=int, default=2048)
    parser.add_argument("--image", default="textures/baked_basecolor.png")
    parser.add_argument("--samples", type=int, default=16)
    args = parser.parse_args(bl_common.args_after_double_dash())
    bl_common.require_inside_workspace(args.save, "save")
    bl_common.require_inside_workspace(args.image, "write")
    os.makedirs(os.path.dirname(args.image) or ".", exist_ok=True)

    objects = bl_common.mesh_objects()
    if not objects:
        raise SystemExit("Nothing to bake: the scene has no mesh objects")

    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = args.samples
    scene.cycles.device = "CPU"

    image = bpy.data.images.new("AgentBakedBaseColor", args.size, args.size, alpha=False)
    image.filepath_raw = os.path.abspath(args.image)
    image.file_format = "PNG"

    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        if not obj.data.uv_layers:
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.uv.smart_project(angle_limit=1.15, island_margin=0.01)
            bpy.ops.object.mode_set(mode="OBJECT")
        if not obj.data.materials:
            obj.data.materials.append(bpy.data.materials.new(f"{obj.name}_material"))
        for material in obj.data.materials:
            if material is None:
                continue
            material.use_nodes = True
            nodes = material.node_tree.nodes
            node = nodes.get("AgentBakeTarget") or nodes.new("ShaderNodeTexImage")
            node.name = "AgentBakeTarget"
            node.image = image
            node.select = True
            nodes.active = node

    bpy.ops.object.bake(type="DIFFUSE", pass_filter={"COLOR"}, margin=8, use_clear=True,
                        save_mode="INTERNAL")
    image.save()
    print(f"baked {len(objects)} mesh(es) -> {args.image}")

    # Rewire so the exporter finds an image on Base Color: the bake target becomes the material's colour.
    for obj in objects:
        for material in obj.data.materials:
            if material is None:
                continue
            nodes = material.node_tree.nodes
            links = material.node_tree.links
            bsdf = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
            if bsdf is None:
                bsdf = nodes.new("ShaderNodeBsdfPrincipled")
                output = next((n for n in nodes if n.type == "OUTPUT_MATERIAL"), None) or nodes.new(
                    "ShaderNodeOutputMaterial")
                links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
            target = nodes["AgentBakeTarget"]
            for link in list(bsdf.inputs["Base Color"].links):
                links.remove(link)
            links.new(target.outputs["Color"], bsdf.inputs["Base Color"])

    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.save))
    print(f"saved -> {args.save}")


main()

# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Fuse every mesh in the scene into ONE closed body and carry the colour across onto it.

    blender -b scene.blend --factory-startup --python-exit-code 1 -P tools/bl_solidify.py -- \\
        --save scene.blend [--voxel-ratio 0.004] [--size 2048] [--image textures/solid_basecolor.png]

The natural way to build a figure is from parts -- a head, a torso, limbs, hair -- each a closed primitive.
That is fine to model with and wrong to ship: the print gate downstream expects ONE body, and 34 closed
shells that happen to overlap read as 33 detached pieces. The first agent run produced exactly that and
failed the gate with a model that looked right.

This does the fusing once, correctly, as the last modelling step:

1. Duplicates every mesh as a colour SOURCE and hides the copies from render.
2. Joins the originals and voxel-remeshes the result, which turns any set of overlapping closed shells into
   a single watertight surface. The voxel size is a fraction of the subject's longest dimension, so a part
   thinner than that fraction is lost -- keep thin parts thicker than about half a percent of the height.
   A join that is already one closed body has nothing to fuse, so its topology is kept and the remesh skipped.
3. Unwraps the new body, bakes the sources' colour onto it (selected-to-active) into one image, and wires
   that image to the body's Base Color. Everything the parts looked like survives; their topology does not.
4. Deletes the sources and saves.

Run it after the materials look right in a render and before exporting. bl_export.py then has one closed
textured object to write. A scene that is already one closed object is kept as it is; the remesh runs only
when overlapping parts must be fused.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bmesh  # noqa: E402
import bpy  # noqa: E402
import numpy as np  # noqa: E402

import bl_common  # noqa: E402


def closedness(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    boundary = sum(1 for e in bm.edges if e.is_boundary)
    non_manifold = sum(1 for e in bm.edges if not e.is_manifold)
    bm.free()
    return boundary, non_manifold


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--save", required=True)
    parser.add_argument("--voxel-ratio", type=float, default=0.004,
                        help="voxel size as a fraction of the longest scene dimension")
    parser.add_argument("--size", type=int, default=2048)
    parser.add_argument("--image", default="textures/solid_basecolor.png")
    parser.add_argument("--samples", type=int, default=16)
    args = parser.parse_args(bl_common.args_after_double_dash())
    bl_common.require_inside_workspace(args.save, "save")
    bl_common.require_inside_workspace(args.image, "write")
    os.makedirs(os.path.dirname(args.image) or ".", exist_ok=True)

    parts = bl_common.mesh_objects()
    if not parts:
        raise SystemExit("Nothing to solidify: the scene has no mesh objects")
    bounds = bl_common.scene_bounds()
    longest = max((bounds[1] - bounds[0])) if bounds else 1.0
    voxel = max(longest * args.voxel_ratio, 1e-5)
    print(f"solidify: {len(parts)} part(s), longest dimension {longest:.4f}, voxel {voxel:.5f}", flush=True)

    scene = bpy.context.scene
    bpy.ops.object.select_all(action="DESELECT")

    # 1. Colour sources: duplicates of every part, kept only for the bake.
    for obj in parts:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    bpy.ops.object.duplicate(linked=False)
    sources = [o for o in bpy.context.selected_objects if o.type == "MESH"]
    for src in sources:
        src.name = f"ColourSource_{src.name}"
        src.hide_render = True

    # 2. One body: join the originals, apply modifiers, voxel-remesh.
    bpy.ops.object.select_all(action="DESELECT")
    for obj in parts:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    for obj in parts:
        bpy.context.view_layer.objects.active = obj
        for modifier in list(obj.modifiers):
            try:
                bpy.ops.object.modifier_apply(modifier=modifier.name)
            except RuntimeError:
                obj.modifiers.remove(modifier)
    bpy.context.view_layer.objects.active = parts[0]
    if len(parts) > 1:
        bpy.ops.object.join()
    body = bpy.context.view_layer.objects.active
    body.name = "Subject"
    body.data.name = "Subject"

    # The voxel remesh is the tool for turning overlapping shells into one surface, but it is destructive:
    # it discards the input topology and rebuilds it at a uniform voxel density, which coarsens a clean
    # surface and can bridge close-but-separate features into tunnels. When the joined result is ALREADY
    # one closed, manifold body -- the case when the scene is a single reconstructed mesh -- there is
    # nothing to fuse, so the topology is kept as it is and only the smooth shading and bake below run.
    pre_boundary, pre_non_manifold = closedness(body)
    already_one_closed_body = (pre_boundary == 0 and pre_non_manifold == 0
                               and len(bl_common.connected_bodies(body)) == 1)
    if already_one_closed_body:
        print(f"already one closed body ({len(body.data.polygons)} faces): kept as is, not remeshed",
              flush=True)
    else:
        body.data.remesh_voxel_size = voxel
        body.data.remesh_voxel_adaptivity = 0.0
        body.data.use_remesh_fix_poles = True
        bpy.ops.object.voxel_remesh()
    for polygon in body.data.polygons:
        polygon.use_smooth = True
    boundary, non_manifold = closedness(body)
    print(f"body: {len(body.data.polygons)} faces, boundary edges {boundary}, "
          f"non-manifold edges {non_manifold}", flush=True)

    # A voxel remesh fuses what OVERLAPS. Parts that merely sit near each other come out as separate
    # pieces, each perfectly closed, and the print gate counts pieces. Remesh crumbs -- specks of a few
    # faces where a thin part thinned to nothing -- are removed; anything larger is a part the scene left
    # floating, and the scene is left as it was so the parts can be moved and the step run again.
    bodies = bl_common.connected_bodies(body)
    total_faces = sum(b["faces"] for b in bodies)
    crumbs = [b for b in bodies[1:] if b["faces"] < max(12, total_faces * 0.002)]
    if crumbs:
        crumb_roots = {tuple(b["centre"]) for b in crumbs}
        bm = bmesh.new()
        bm.from_mesh(body.data)
        for crumb in crumbs:
            lo = [crumb["centre"][k] - crumb["extents"][k] / 2 - 1e-6 for k in range(3)]
            hi = [crumb["centre"][k] + crumb["extents"][k] / 2 + 1e-6 for k in range(3)]
            doomed = [f for f in bm.faces if all(
                lo[k] <= (body.matrix_world @ f.calc_center_median())[k] <= hi[k] for k in range(3))]
            if len(doomed) == crumb["faces"]:
                bmesh.ops.delete(bm, geom=doomed, context="FACES")
        bm.to_mesh(body.data)
        bm.free()
        print(f"removed {len(crumb_roots)} remesh crumb(s) totalling {sum(c['faces'] for c in crumbs)} faces",
              flush=True)
        bodies = bl_common.connected_bodies(body)
    if len(bodies) > 1:
        print(f"THE FUSED RESULT IS {len(bodies)} SEPARATE BODIES, not one. Largest first "
              f"(faces, extents, centre):", flush=True)
        for entry in bodies[:12]:
            print(f"  {entry['faces']:7d}  {entry['extents']}  at {entry['centre']}", flush=True)
        raise SystemExit(
            "Not saved. Every part must physically OVERLAP its neighbour -- sunk into it by at least two "
            "voxels (%.4f) -- because fusing cannot bridge a gap of any size. Move the detached parts in "
            "your build script so they intersect, rebuild the scene, and run solidify again." % (2 * voxel))

    # 3. Give the body its bake material FIRST, then unwrap, then bake the sources' colour onto it.
    # The unwrap is told not to correct for an image's aspect ratio: with the parts' materials still on the
    # body it would read the active image -- a 4-pixel-wide colour strip on one run -- and squash every
    # island to two percent of the atlas, which rendered as a smear the agent then shipped.
    image = bpy.data.images.new("SubjectBaseColor", args.size, args.size, alpha=False)
    image.filepath_raw = os.path.abspath(args.image)
    image.file_format = "PNG"
    material = bpy.data.materials.new("SubjectMaterial")
    material.use_nodes = True
    nodes, links = material.node_tree.nodes, material.node_tree.links
    bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")
    tex = nodes.new("ShaderNodeTexImage")
    tex.name = "SubjectBakeTarget"
    tex.image = image
    tex.select = True
    nodes.active = tex
    body.data.materials.clear()
    body.data.materials.append(material)

    bpy.ops.object.select_all(action="DESELECT")
    body.select_set(True)
    bpy.context.view_layer.objects.active = body
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=1.15, island_margin=0.01, correct_aspect=False, scale_to_bounds=False)
    bpy.ops.object.mode_set(mode="OBJECT")
    uv_layer = body.data.uv_layers.active
    coords = np.empty(len(uv_layer.data) * 2, dtype=np.float32)
    uv_layer.data.foreach_get("uv", coords)
    coords = coords.reshape(-1, 2)
    span = coords.max(axis=0) - coords.min(axis=0)
    print(f"unwrapped: uv spans {span[0]:.3f} x {span[1]:.3f} of the atlas", flush=True)
    if min(span) < 0.5:
        raise SystemExit("Not saved: the unwrap collapsed the layout; this is a tool fault, report it in notes.md")

    # Sources need a material with nodes for the bake to read; give a bare one a flat grey.
    for src in sources:
        if not src.data.materials:
            fallback = bpy.data.materials.new(f"{src.name}_fallback")
            fallback.use_nodes = True
            src.data.materials.append(fallback)
        for m in src.data.materials:
            if m is not None and not m.use_nodes:
                m.use_nodes = True

    scene.render.engine = "CYCLES"
    scene.cycles.samples = args.samples
    scene.cycles.device = "CPU"
    scene.render.bake.use_selected_to_active = True
    scene.render.bake.cage_extrusion = voxel * 3
    scene.render.bake.max_ray_distance = voxel * 8
    scene.render.bake.margin = 8
    bpy.ops.object.select_all(action="DESELECT")
    for src in sources:
        src.select_set(True)
        src.hide_render = False
    body.select_set(True)
    bpy.context.view_layer.objects.active = body
    bpy.ops.object.bake(type="DIFFUSE", pass_filter={"COLOR"}, use_selected_to_active=True,
                        cage_extrusion=voxel * 3, max_ray_distance=voxel * 8, margin=8, use_clear=True,
                        save_mode="INTERNAL")
    image.save()
    links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    print(f"baked colour from {len(sources)} source(s) -> {args.image}", flush=True)

    # 4. Drop the sources; only the body remains.
    for src in sources:
        bpy.data.objects.remove(src, do_unlink=True)
    for block in (bpy.data.meshes, bpy.data.materials):
        for datum in list(block):
            if datum.users == 0:
                block.remove(datum)

    if boundary or non_manifold:
        raise SystemExit("Not saved: the fused body is not closed; raise --voxel-ratio slightly and run again")
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.save))
    report = bl_common.mesh_report()
    print(f"saved -> {args.save}: {report['meshCount']} mesh, ONE body, closed, "
          f"dimensions {report.get('sceneDimensions')}")


main()

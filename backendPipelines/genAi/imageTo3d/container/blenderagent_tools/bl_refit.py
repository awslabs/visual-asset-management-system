# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Refit a reconstruction to its photograph, one step per call.

    blender -b scene.blend --factory-startup --python-exit-code 1 -P tools/bl_refit.py -- <step> [options]

    register     --reference refs/<front>_cutout.png
    rebuild      --method voxel|metaball|none --resolution 0.004 [--max-drift 0.02] [--min-volume-retention 0.5]
    fit-outline  --reference refs/<front>_cutout.png [--max-iterations 20] [--max-shift 0.03]
    relief       --reference refs/<front>_cutout.png --depth refs/front_depth.npz [--strength 0.01]
    retopo       --method decimate|quadriflow --faces 200000
    check        --resolution 0.004
    colour
    overlay      --reference refs/<front>_cutout.png --out refit/refit_front_after.png [--final]
    unturn

`register` joins the scene into ONE mesh named Subject and turns it so the side, and the height, from which
its outline best matches the photograph face -Y; `unturn` turns it back once the photograph is baked on.
Between the two, every step sees the subject from the photograph's camera. `rebuild` keeps the body it
started from as a hidden RefitSource, which `colour` reads the reconstruction's own colour from and then
removes; re-running `rebuild` starts again from it. Each step saves the scene (`--save`, default
scene.blend), except `overlay`, and records what it measured under its own name in `--report` (default
refit/refit_report.json). Lengths are fractions of the subject's height as `register` measured it.
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bmesh  # noqa: E402
import bpy  # noqa: E402
import numpy as np  # noqa: E402
from mathutils import Vector  # noqa: E402
from mathutils.bvhtree import BVHTree  # noqa: E402

import bl_common  # noqa: E402
import photo_projection  # noqa: E402
import refit_math  # noqa: E402

SUBJECT = "Subject"
SOURCE = "RefitSource"
FRAME_FILE = os.path.join("refit", "refit_frame.json")
# A separate piece holding less than this share of the body's volume is debris, not a part.
LOOSE_VOLUME_SHARE = 0.005
# The most rebuilt vertices the drift from the source is measured over.
DRIFT_SAMPLES = 50_000
# The most metaball elements a rebuild places; the lattice is coarsened to stay under it.
METABALL_CAP = 60_000
# A metaball element's radius, in element spacings.
METABALL_RADIUS = 2.0
# How many times the relief pins the vertices of the crossings it added before it gives up and is reverted.
RELIEF_ROUNDS = 3
# QuadriFlow refuses a mesh holding an edge shorter than 1e-4 units; shorter edges are dissolved first.
QUADRIFLOW_MIN_EDGE = 2e-4


# ------------------------------------------------------------------ scene

def subject():
    obj = bpy.data.objects.get(SUBJECT)
    if obj is None or obj.type != "MESH":
        raise SystemExit("The scene has no Subject: run `bl_refit.py register` first")
    return obj


def source_object():
    return bpy.data.objects.get(SOURCE)


def select_only(obj):
    bpy.ops.object.select_all(action="DESELECT")
    obj.hide_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def read_mesh(obj):
    mesh = obj.data
    vertices = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", vertices)
    return vertices.reshape(-1, 3).astype(float), bl_common.triangles_of(mesh)


def write_vertices(obj, vertices):
    obj.data.vertices.foreach_set("co", np.asarray(vertices, dtype=np.float32).ravel())
    obj.data.update()


def edges_of(mesh):
    edges = np.empty(len(mesh.edges) * 2, dtype=np.int64)
    mesh.edges.foreach_get("vertices", edges)
    return edges.reshape(-1, 2)


def replace_mesh(obj, mesh):
    old = obj.data
    obj.data = mesh
    if old.users == 0:
        bpy.data.meshes.remove(old)


def recentre(vertices):
    out = np.array(vertices, dtype=float)
    out[:, 0] -= (out[:, 0].min() + out[:, 0].max()) / 2
    out[:, 1] -= (out[:, 1].min() + out[:, 1].max()) / 2
    out[:, 2] -= out[:, 2].min()
    return out


def load_frame():
    try:
        with open(FRAME_FILE, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        raise SystemExit(f"{FRAME_FILE} is missing: run `bl_refit.py register` first")


def photograph_mask(reference):
    pixels, _, _ = bl_common.load_rgba(reference)
    alpha = pixels[:, :, 3] > 0.5
    if not alpha.any():
        raise SystemExit(f"{reference} has no opaque pixels; is it the cutout?")
    return alpha


def record(report, step, data):
    try:
        with open(report, encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError):
        doc = {}
    doc.setdefault("steps", {})[step] = data
    bl_common.write_json(report, doc)


def save(args):
    bl_common.require_inside_workspace(args.save, "save")
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.save))


def crossing_pairs(vertices, triangles):
    """The pairs of triangles that share no vertex and cross each other, as (n, 2) triangle indices."""
    tree = BVHTree.FromPolygons(vertices.tolist(), triangles.tolist(), all_triangles=True)
    pairs = np.array(tree.overlap(tree), dtype=np.int64).reshape(-1, 2)
    pairs = pairs[pairs[:, 0] < pairs[:, 1]]
    a, b = triangles[pairs[:, 0]], triangles[pairs[:, 1]]
    shared = (a[:, :, None] == b[:, None, :]).any(axis=(1, 2))
    return pairs[~shared]


def vertex_colours(mesh):
    """One RGBA row per vertex from the mesh's colour attribute, as stored, or None."""
    attributes = mesh.color_attributes
    if not len(attributes):
        return None
    attribute = attributes.active_color or attributes[0]
    values = np.empty(len(attribute.data) * 4, dtype=np.float32)
    attribute.data.foreach_get("color", values)
    rgba = values.reshape(-1, 4).astype(float)
    if attribute.domain == "CORNER":
        index = np.empty(len(mesh.loops), dtype=np.int64)
        mesh.loops.foreach_get("vertex_index", index)
        total = np.zeros((len(mesh.vertices), 4))
        count = np.zeros(len(mesh.vertices))
        np.add.at(total, index, rgba)
        np.add.at(count, index, 1.0)
        rgba = total / np.maximum(count, 1.0)[:, None]
    return rgba


def voxel_remesh(obj, size):
    mesh = obj.data
    mesh.remesh_voxel_size = size
    mesh.remesh_voxel_adaptivity = 0.0
    mesh.use_remesh_fix_poles = True
    select_only(obj)
    bpy.ops.object.voxel_remesh()


# ------------------------------------------------------------------ steps

def register(args):
    objects = bl_common.mesh_objects()
    if not objects:
        raise SystemExit("Nothing to register: the scene has no mesh objects")
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
    if len(objects) > 1:
        bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active
    obj.name = SUBJECT
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    vertices, _ = read_mesh(obj)
    height = float(np.ptp(vertices[:, 2]))
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    before = len(bm.verts)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6 * max(height, 1e-9))
    welded = before - len(bm.verts)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()

    alpha = photograph_mask(args.reference)
    vertices, triangles = read_mesh(obj)
    azimuth, elevation, search = refit_math.choose_view(vertices, triangles, alpha)
    rotation = refit_math.view_rotation(azimuth, elevation)
    turned = recentre(vertices @ rotation.T)
    write_vertices(obj, turned)
    work, _ = refit_math.resample_mask(alpha)
    score = refit_math.iou(refit_math.front_mask(turned, triangles, refit_math.fit_frame(work, turned)), work)
    bl_common.write_json(FRAME_FILE, {"azimuth": azimuth, "elevation": elevation, "rotation": rotation.tolist(),
                                      "height": height})
    save(args)
    return {"azimuth": azimuth, "elevation": elevation, "searchIou": round(search, 4), "iou": round(score, 4),
            "vertices": len(turned), "triangles": len(triangles), "welded": welded, "height": round(height, 5)}


def metaball_body(source, spacing):
    """A mesh polygonised from metaballs placed on the interior cells of a lattice over ``source``, its
    threshold solved so it encloses the source's volume. Returns the mesh and what was measured."""
    vertices, triangles = read_mesh(source)
    target = abs(refit_math.mesh_volume(vertices, triangles))
    tree = BVHTree.FromPolygons(vertices.tolist(), triangles.tolist(), all_triangles=True)
    lo, hi = vertices.min(axis=0), vertices.max(axis=0)
    ys = np.arange(lo[1] + 0.37 * spacing, hi[1], spacing)
    zs = np.arange(lo[2] + 0.37 * spacing, hi[2], spacing)
    xs = np.arange(lo[0] + 0.5 * spacing, hi[0], spacing)
    step = Vector((1.0, 0.0, 0.0))
    nudge = 1e-4 * spacing
    lines = []
    for iy, y in enumerate(ys):
        for iz, z in enumerate(zs):
            origin, hits = Vector((lo[0] - spacing, y, z)), []
            while len(hits) < 256:
                location, _, _, _ = tree.ray_cast(origin, step)
                if location is None:
                    break
                hits.append(location.x)
                origin = location + step * nudge
            if len(hits) >= 2:
                lines.append((iy, iz, refit_math.inside_along_line(hits, xs)))
    count = sum(int(inside.sum()) for _, _, inside in lines)
    stride = refit_math.choose_stride(max(count, 1), METABALL_CAP)
    centres = []
    for iy, iz, inside in lines:
        if iy % stride or iz % stride:
            continue
        keep = inside & (np.arange(len(xs)) % stride == 0)
        for x in xs[keep]:
            centres.append((float(x), float(ys[iy]), float(zs[iz])))
    if not centres:
        raise SystemExit("The metaball lattice found no interior cells: is the body closed?")

    ball = bpy.data.metaballs.new("RefitBlob")
    blob = bpy.data.objects.new("RefitBlob", ball)
    bpy.context.scene.collection.objects.link(blob)
    radius = METABALL_RADIUS * stride * spacing
    for centre in centres:
        element = ball.elements.new(type="BALL")
        element.co = centre
        element.radius = radius

    def polygonise(threshold, resolution):
        ball.threshold = threshold
        ball.resolution = resolution
        ball.render_resolution = resolution
        depsgraph = bpy.context.evaluated_depsgraph_get()
        depsgraph.update()
        return bpy.data.meshes.new_from_object(blob.evaluated_get(depsgraph))

    def volume_at(threshold):
        mesh = polygonise(threshold, 2.0 * spacing)
        mesh.calc_loop_triangles()
        tris = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int64)
        mesh.loop_triangles.foreach_get("vertices", tris)
        co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", co)
        volume = abs(refit_math.mesh_volume(co.reshape(-1, 3).astype(float), tris.reshape(-1, 3)))
        bpy.data.meshes.remove(mesh)
        return volume

    threshold, volume, how, tried = refit_math.solve_threshold(volume_at, target)
    mesh = polygonise(threshold, spacing)
    bpy.data.objects.remove(blob, do_unlink=True)
    bpy.data.metaballs.remove(ball)
    return mesh, {"elements": len(centres), "stride": stride, "interiorCells": count,
                  "threshold": round(threshold, 4), "solve": how, "solveSteps": len(tried),
                  "volumeAtSolve": round(volume / max(target, 1e-12), 4)}


def rebuild(args):
    obj = subject()
    height = float(load_frame()["height"])
    source = source_object()
    if source is None:
        source = obj.copy()
        source.data = obj.data.copy()
        source.name = SOURCE
        bpy.context.scene.collection.objects.link(source)
        source.hide_render = True
    else:
        replace_mesh(obj, source.data.copy())
    source.hide_set(True)
    result = {"method": args.method, "resolution": args.resolution}
    if args.method == "none":
        save(args)
        return result

    spacing = args.resolution * height
    if args.method == "voxel":
        voxel_remesh(obj, spacing)
    else:
        mesh, measured = metaball_body(source, spacing)
        replace_mesh(obj, mesh)
        result.update(measured)

    rebuilt, rebuilt_triangles = read_mesh(obj)
    original, original_triangles = read_mesh(source)
    tree = BVHTree.FromPolygons(original.tolist(), original_triangles.tolist(), all_triangles=True)
    sample = rebuilt[np.linspace(0, len(rebuilt) - 1, min(len(rebuilt), DRIFT_SAMPLES)).astype(int)]
    distances = [tree.find_nearest(p)[3] for p in sample.tolist()]
    measured = refit_math.drift(distances, height)
    ratio = (abs(refit_math.mesh_volume(rebuilt, rebuilt_triangles))
             / max(abs(refit_math.mesh_volume(original, original_triangles)), 1e-12))
    result.update({"drift": {k: round(v, 5) for k, v in measured.items()}, "volumeRatio": round(ratio, 4),
                   "vertices": len(rebuilt), "triangles": len(rebuilt_triangles)})
    reasons = []
    if measured["mean"] > args.max_drift:
        reasons.append(f"mean drift {measured['mean']:.4f} of the height is over {args.max_drift}")
    if ratio < args.min_volume_retention:
        reasons.append(f"volume ratio {ratio:.3f} is under {args.min_volume_retention}")
    if reasons:
        replace_mesh(obj, source.data.copy())
        result["reverted"] = "; ".join(reasons)
    save(args)
    return result


def fit_outline(args):
    obj = subject()
    height = float(load_frame()["height"])
    vertices, triangles = read_mesh(obj)
    work, _ = refit_math.resample_mask(photograph_mask(args.reference))
    fitted, result = refit_math.fit_outline(vertices, triangles, work, height, max_iterations=args.max_iterations,
                                            max_shift=args.max_shift)
    write_vertices(obj, fitted)
    save(args)
    return result


def relief(args):
    obj = subject()
    height = float(load_frame()["height"])
    alpha = photograph_mask(args.reference)
    with np.load(args.depth) as estimate:
        depth, valid = estimate["depth"].astype(float), estimate["mask"].astype(bool)
        metric_width = float(estimate["metricWidth"])
    if depth.shape != alpha.shape:
        raise SystemExit(f"The depth estimate is {depth.shape}, the photograph {alpha.shape}: not the same grid")
    vertices, triangles = read_mesh(obj)
    normals = photo_projection.vertex_normals(vertices, triangles)
    work, k = refit_math.resample_mask(alpha)
    frame = refit_math.fit_frame(work, vertices)
    inside = np.maximum(-refit_math.outline_field(work)[0], 0.0)
    offsets, result = refit_math.relief_offsets(vertices, normals, triangles, frame, k, depth, valid & alpha,
                                                inside, metric_width, args.strength * height)
    edges = edges_of(obj.data)
    offsets = refit_math.smooth(offsets, edges)
    # A reconstruction's fur and strands already cross here and there; what the relief must not do is add
    # crossings. Where it does, the vertices of those triangles and their neighbours keep their place.
    known = {tuple(p) for p in crossing_pairs(vertices, triangles).tolist()}
    pinned = np.zeros(len(vertices), dtype=bool)
    for _ in range(RELIEF_ROUNDS):
        moved = vertices + normals * offsets[:, None]
        added = [p for p in map(tuple, crossing_pairs(moved, triangles).tolist()) if p not in known]
        if not added:
            break
        involved = np.unique(triangles[np.array(added).ravel()])
        ring = np.unique(edges[np.isin(edges, involved).any(axis=1)])
        pinned[ring] = True
        offsets[pinned] = 0.0
    else:
        added = [p for p in map(tuple, crossing_pairs(vertices + normals * offsets[:, None], triangles).tolist())
                 if p not in known]
    result.update({"strength": args.strength, "crossingsBefore": len(known), "pinnedVertices": int(pinned.sum())})
    if added:
        result["reverted"] = f"the relief still added {len(added)} crossing triangle pairs"
    else:
        write_vertices(obj, vertices + normals * offsets[:, None])
    save(args)
    return result


def retopo(args):
    obj = subject()
    obj.data.calc_loop_triangles()
    triangles = len(obj.data.loop_triangles)
    result = {"method": args.method, "faces": args.faces, "trianglesBefore": triangles}
    if args.faces <= 0 or triangles <= args.faces:
        result["skipped"] = "already within the face budget" if args.faces > 0 else "no face budget"
        return result
    used = args.method
    if args.method == "quadriflow":
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bmesh.ops.dissolve_degenerate(bm, dist=QUADRIFLOW_MIN_EDGE, edges=bm.edges[:])
        bmesh.ops.triangulate(bm, faces=bm.faces[:])
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        bm.to_mesh(obj.data)
        bm.free()
        obj.data.update()
        select_only(obj)
        try:
            outcome = bpy.ops.object.quadriflow_remesh(mode="FACES", target_faces=max(args.faces // 2, 100),
                                                       use_mesh_symmetry=False, use_preserve_sharp=False,
                                                       use_preserve_boundary=False, preserve_attributes=True,
                                                       seed=0)
        except RuntimeError as e:
            outcome = {f"FAILED: {e}"}
        obj.data.calc_loop_triangles()
        if "FINISHED" not in outcome or len(obj.data.loop_triangles) == triangles:
            result["fallback"] = f"QuadriFlow did not remesh ({', '.join(sorted(outcome))}); decimated instead"
            used = "decimate"
        else:
            bm = bmesh.new()
            bm.from_mesh(obj.data)
            bmesh.ops.triangulate(bm, faces=bm.faces)
            bm.to_mesh(obj.data)
            bm.free()
    if used == "decimate":
        modifier = obj.modifiers.new("RefitDecimate", "DECIMATE")
        modifier.decimate_type = "COLLAPSE"
        modifier.ratio = args.faces / triangles
        modifier.use_collapse_triangulate = True
        select_only(obj)
        bpy.ops.object.modifier_apply(modifier=modifier.name)
    obj.data.calc_loop_triangles()
    result.update({"used": used, "trianglesAfter": len(obj.data.loop_triangles)})
    save(args)
    return result


def leaks(obj):
    """Open and non-manifold edges, non-manifold vertices, and the pieces with their volumes."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    found = {"boundaryEdges": sum(1 for e in bm.edges if e.is_boundary),
             "nonManifoldEdges": sum(1 for e in bm.edges if not e.is_manifold),
             "nonManifoldVertices": sum(1 for v in bm.verts if not v.is_manifold)}
    bm.free()
    vertices, triangles = read_mesh(obj)
    parent = list(range(len(vertices)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in edges_of(obj.data).tolist():
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    labels = np.array([find(i) for i in range(len(vertices))], dtype=np.int64)
    roots, piece = np.unique(labels, return_inverse=True)
    a, b, c = (vertices[triangles[:, i]] for i in range(3))
    signed = np.einsum("ij,ij->i", a, np.cross(b, c)) / 6.0
    volumes = np.bincount(piece[triangles[:, 0]], weights=signed, minlength=len(roots))
    found.update({"pieces": int(len(roots)), "signedVolume": float(volumes.sum()),
                  "selfIntersections": len(crossing_pairs(vertices, triangles))})
    return found, piece, volumes


def check(args):
    obj = subject()
    height = float(load_frame()["height"])
    found, piece, volumes = leaks(obj)
    repairs = []
    total = abs(volumes.sum())
    small = np.nonzero(np.abs(volumes) < LOOSE_VOLUME_SHARE * max(total, 1e-12))[0]
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    if len(small) and len(small) < len(volumes):
        doomed = np.nonzero(np.isin(piece, small))[0]
        bmesh.ops.delete(bm, geom=[bm.verts[i] for i in doomed.tolist()], context="VERTS")
        repairs.append(f"deleted {len(small)} loose piece(s) under {LOOSE_VOLUME_SHARE:.1%} of the volume")
    open_edges = [e for e in bm.edges if e.is_boundary]
    if open_edges:
        bmesh.ops.holes_fill(bm, edges=open_edges, sides=0)
        repairs.append(f"filled the holes along {len(open_edges)} open edges")
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    after, _, volumes = leaks(obj)
    if after["signedVolume"] < 0:
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bmesh.ops.reverse_faces(bm, faces=bm.faces)
        bm.to_mesh(obj.data)
        bm.free()
        repairs.append("turned the faces outwards")
        after, _, _ = leaks(obj)
    if after["boundaryEdges"] or after["nonManifoldEdges"]:
        repaired = obj.data.copy()
        voxel_remesh(obj, args.resolution * height)
        remeshed, _, _ = leaks(obj)
        if (remeshed["boundaryEdges"] + remeshed["nonManifoldEdges"]
                < after["boundaryEdges"] + after["nonManifoldEdges"]):
            repairs.append(f"still open after repair; voxel remeshed at {args.resolution} of the height")
            after = remeshed
            bpy.data.meshes.remove(repaired)
        else:
            replace_mesh(obj, repaired)
            repairs.append("a voxel remesh left it no less open, so the repaired mesh is kept")
    save(args)
    return {"found": found, "repairs": repairs, "after": after,
            "leaks": bool(after["boundaryEdges"] or after["nonManifoldEdges"])}


def colour(args):
    obj = subject()
    source = source_object()
    result = {}
    if len(obj.data.color_attributes):
        result["colour"] = "kept the body's own colour"
    elif source is None or vertex_colours(source.data) is None:
        result["colour"] = "no colour to carry: the photograph projection colours the unseen side by rows"
    else:
        src_vertices, src_triangles = read_mesh(source)
        src_colours = vertex_colours(source.data)
        tree = BVHTree.FromPolygons(src_vertices.tolist(), src_triangles.tolist(), all_triangles=True)
        vertices, _ = read_mesh(obj)
        nearest = np.empty_like(vertices)
        faces = np.empty(len(vertices), dtype=np.int64)
        for i, point in enumerate(vertices.tolist()):
            location, _, face, _ = tree.find_nearest(point)
            nearest[i] = location
            faces[i] = face
        corners = src_triangles[faces]
        weights = refit_math.barycentric(nearest, src_vertices[corners])
        rgba = (weights[:, :, None] * src_colours[corners]).sum(axis=1)
        attribute = obj.data.color_attributes.new(name="Color", type="FLOAT_COLOR", domain="POINT")
        attribute.data.foreach_set("color", rgba.astype(np.float32).ravel())
        obj.data.color_attributes.active_color = attribute
        result["colour"] = f"carried the reconstruction's colour onto {len(vertices)} vertices"
    if source is not None:
        mesh = source.data
        bpy.data.objects.remove(source, do_unlink=True)
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    save(args)
    return result


def overlay(args):
    obj = subject()
    vertices, triangles = read_mesh(obj)
    work, _ = refit_math.resample_mask(photograph_mask(args.reference))
    rendered = refit_math.front_mask(vertices, triangles, refit_math.fit_frame(work, vertices))
    picture = refit_math.overlay_image(rendered, work)
    bl_common.require_inside_workspace(args.out, "write")
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    rows, cols, _ = picture.shape
    rgba = np.concatenate([picture, np.ones((rows, cols, 1))], axis=2)[::-1].astype(np.float32)
    image = bpy.data.images.new("RefitOverlay", cols, rows, alpha=False)
    image.pixels.foreach_set(rgba.ravel())
    image.filepath_raw = os.path.abspath(args.out)
    image.file_format = "PNG"
    image.save()
    bpy.data.images.remove(image)
    return {"iou": round(refit_math.iou(rendered, work), 4), "image": args.out}


def unturn(args):
    obj = subject()
    rotation = np.array(load_frame()["rotation"], dtype=float)
    vertices, _ = read_mesh(obj)
    write_vertices(obj, recentre(vertices @ rotation))
    save(args)
    return {"rotation": "undone"}


STEPS = {"register": register, "rebuild": rebuild, "fit-outline": fit_outline, "relief": relief,
         "retopo": retopo, "check": check, "colour": colour, "overlay": overlay, "unturn": unturn}


def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--save", default="scene.blend")
    common.add_argument("--report", default=os.path.join("refit", "refit_report.json"))
    parser = argparse.ArgumentParser(prog="bl_refit.py")
    steps = parser.add_subparsers(dest="step", required=True)
    step = steps.add_parser("register", parents=[common])
    step.add_argument("--reference", required=True)
    step = steps.add_parser("rebuild", parents=[common])
    step.add_argument("--method", choices=("voxel", "metaball", "none"), default="none")
    step.add_argument("--resolution", type=float, default=0.004)
    step.add_argument("--max-drift", type=float, default=0.02)
    step.add_argument("--min-volume-retention", type=float, default=0.5)
    step = steps.add_parser("fit-outline", parents=[common])
    step.add_argument("--reference", required=True)
    step.add_argument("--max-iterations", type=int, default=20)
    step.add_argument("--max-shift", type=float, default=0.03)
    step = steps.add_parser("relief", parents=[common])
    step.add_argument("--reference", required=True)
    step.add_argument("--depth", required=True)
    step.add_argument("--strength", type=float, default=0.01)
    step = steps.add_parser("retopo", parents=[common])
    step.add_argument("--method", choices=("decimate", "quadriflow"), default="decimate")
    step.add_argument("--faces", type=int, required=True)
    step = steps.add_parser("check", parents=[common])
    step.add_argument("--resolution", type=float, default=0.004)
    steps.add_parser("colour", parents=[common])
    step = steps.add_parser("overlay", parents=[common])
    step.add_argument("--reference", required=True)
    step.add_argument("--out", required=True)
    step.add_argument("--final", action="store_true")
    steps.add_parser("unturn", parents=[common])
    args = parser.parse_args(bl_common.args_after_double_dash())

    started = time.monotonic()
    result = STEPS[args.step](args)
    result["seconds"] = round(time.monotonic() - started, 2)
    name = ("after" if args.final else "before") if args.step == "overlay" else args.step
    record(args.report, name, result)
    print(f"{args.step}: {json.dumps(result)}", flush=True)


main()

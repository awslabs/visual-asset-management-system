# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Helpers shared by the Blender-side tools the agent calls.

Everything here runs INSIDE Blender's own Python (``blender -b -P``), never in the pipeline's. It is kept
deliberately small: the agent writes its own build scripts, and these exist so the parts that are fiddly in
background mode -- a camera that frames whatever is in the scene, a render that does not need a display, a
bake that actually lands in an image, an export that carries its textures -- are done once, correctly,
rather than reinvented on every run and debugged on a GPU instance.
"""

import json
import math
import os
import sys

import bpy
from mathutils import Vector

import view_fill

# Where the agent's workspace is. Set by the pipeline before Blender is launched; the tools refuse to write
# anywhere else, which is the one safety rule the sandbox cannot express on the tools' behalf.
WORKSPACE = os.environ.get("AGENT_WORKSPACE", os.getcwd())


def args_after_double_dash():
    """The script's own arguments: everything after ``--`` on Blender's command line."""
    argv = sys.argv
    return argv[argv.index("--") + 1:] if "--" in argv else []


def inside_workspace(path):
    resolved = os.path.realpath(path)
    root = os.path.realpath(WORKSPACE)
    return resolved == root or resolved.startswith(root + os.sep)


def require_inside_workspace(path, purpose):
    if not inside_workspace(path):
        raise SystemExit(f"Refusing to {purpose} outside the workspace: {path}")
    return path


def mesh_objects():
    return [o for o in bpy.context.scene.objects if o.type == "MESH" and not o.hide_render]


def scene_bounds():
    """World-space min/max corners over every rendered mesh, or None for an empty scene."""
    lo = Vector((math.inf,) * 3)
    hi = Vector((-math.inf,) * 3)
    for obj in mesh_objects():
        for corner in obj.bound_box:
            world = obj.matrix_world @ Vector(corner)
            lo = Vector(map(min, lo, world))
            hi = Vector(map(max, hi, world))
    if lo.x == math.inf:
        return None
    return lo, hi


def frame_camera(camera, azimuth_deg, elevation_deg, margin=1.25):
    """Aim an orthographic camera at the scene from ``azimuth``/``elevation`` and fit it to the bounds.

    Orthographic, deliberately: an orthographic render from fixed angles is what the pipeline's own
    silhouette and colour checks are done with, and it removes perspective from the comparison the agent
    makes between its render and the reference, so a size difference reads as a size difference.
    """
    bounds = scene_bounds()
    if bounds is None:
        raise SystemExit("Nothing to frame: the scene has no mesh objects")
    lo, hi = bounds
    centre = (lo + hi) / 2
    radius = max((hi - lo).length / 2, 1e-4)

    direction = Vector(view_fill.camera_direction(azimuth_deg, elevation_deg))
    camera.location = centre + direction * radius * 4
    camera.rotation_euler = (centre - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = radius * 2 * margin
    camera.data.clip_start = 0.01
    camera.data.clip_end = radius * 20
    return camera


def ensure_camera_and_light():
    scene = bpy.context.scene
    camera = scene.camera
    if camera is None or camera.type != "CAMERA":
        cam_data = bpy.data.cameras.new("AgentCamera")
        camera = bpy.data.objects.new("AgentCamera", cam_data)
        scene.collection.objects.link(camera)
        scene.camera = camera
    if not any(o.type == "LIGHT" for o in scene.objects):
        # A neutral three-point-ish rig: a sun for form, a fill so the shadowed side is legible in a
        # render the agent compares against a photograph.
        for name, rot, energy in (("AgentKey", (0.9, 0.2, 0.8), 3.0), ("AgentFill", (1.1, -0.3, -2.2), 1.2)):
            light_data = bpy.data.lights.new(name, "SUN")
            light_data.energy = energy
            light = bpy.data.objects.new(name, light_data)
            light.rotation_euler = rot
            scene.collection.objects.link(light)
    world = scene.world or bpy.data.worlds.new("AgentWorld")
    scene.world = world
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    if background is not None:
        background.inputs[0].default_value = (0.05, 0.05, 0.05, 1.0)
        background.inputs[1].default_value = 0.6
    return camera


def configure_render(engine, samples, size):
    """Pick a render engine that works without a display, falling back rather than failing.

    EEVEE needs a GPU-backed OpenGL context, which a headless container may or may not provide; Cycles
    renders on CPU when no device is available, and Workbench needs nothing. A render that cannot start is
    the difference between an agent that iterates and one that stalls, so the fallback is automatic.
    """
    scene = bpy.context.scene
    scene.render.resolution_x = size
    scene.render.resolution_y = size
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = True

    wanted = engine.upper()
    available = {e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
    order = [wanted] + [e for e in ("CYCLES", "BLENDER_WORKBENCH", "BLENDER_EEVEE_NEXT", "BLENDER_EEVEE")
                        if e != wanted]
    for candidate in order:
        if candidate in available:
            scene.render.engine = candidate
            break
    if scene.render.engine == "CYCLES":
        scene.cycles.samples = samples
        scene.cycles.use_denoising = False
        # A GPU is used when the driver exposes one and otherwise the CPU is, chosen by asking rather than
        # assuming: setting the device type succeeds even with no such device, and the failure would then
        # arrive at render time as a crash the agent cannot read.
        scene.cycles.device = "CPU"
        try:
            prefs = bpy.context.preferences.addons["cycles"].preferences
            for backend in ("OPTIX", "CUDA"):
                prefs.compute_device_type = backend
                prefs.get_devices()
                if any(d.type == backend for d in prefs.devices):
                    for device in prefs.devices:
                        device.use = device.type == backend
                    scene.cycles.device = "GPU"
                    break
        except Exception:
            scene.cycles.device = "CPU"
    return scene.render.engine


def mesh_report():
    """Dimensions, counts and the material/UV state of every rendered mesh, as plain data."""
    bounds = scene_bounds()
    objects = []
    for obj in mesh_objects():
        mesh = obj.data
        objects.append({
            "name": obj.name,
            "vertices": len(mesh.vertices),
            "faces": len(mesh.polygons),
            "hasUv": bool(mesh.uv_layers),
            "materials": [m.name for m in mesh.materials if m],
            "modifiers": [m.type for m in obj.modifiers],
            "dimensions": [round(v, 4) for v in obj.dimensions],
        })
    report = {"objects": objects, "meshCount": len(objects)}
    if bounds is not None:
        lo, hi = bounds
        report["sceneDimensions"] = [round(v, 4) for v in (hi - lo)]
        report["sceneMin"] = [round(v, 4) for v in lo]
        report["sceneMax"] = [round(v, 4) for v in hi]
        report["restsOnGround"] = abs(lo.z) < 1e-3
    return report


def write_json(path, data):
    require_inside_workspace(path, "write")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)


def connected_bodies(obj):
    """The connected pieces of a mesh, largest first, each with its face count and world-space extents.

    Closedness is per shell, so a scene of closed parts that do not touch reports every part watertight
    and prints as loose pieces. The print gate counts pieces, so this is the count that decides a run.
    """
    mesh = obj.data
    parent = list(range(len(mesh.vertices)))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for edge in mesh.edges:
        a, b = (find(v) for v in edge.vertices)
        if a != b:
            parent[a] = b
    faces = {}
    for polygon in mesh.polygons:
        faces.setdefault(find(polygon.vertices[0]), []).append(polygon)
    matrix = obj.matrix_world
    bodies = []
    for root, polygons in faces.items():
        indices = {v for p in polygons for v in p.vertices}
        points = [matrix @ mesh.vertices[i].co for i in indices]
        lo = [min(p[k] for p in points) for k in range(3)]
        hi = [max(p[k] for p in points) for k in range(3)]
        bodies.append({"faces": len(polygons),
                       "extents": [round(hi[k] - lo[k], 4) for k in range(3)],
                       "centre": [round((hi[k] + lo[k]) / 2, 4) for k in range(3)]})
    return sorted(bodies, key=lambda b: -b["faces"])


COLOUR_NAMES = (
    (0.00, "red"), (0.04, "orange"), (0.11, "yellow"), (0.19, "green"), (0.45, "cyan"), (0.53, "blue"),
    (0.72, "purple"), (0.83, "pink"), (0.95, "red"),
)


def name_colour(rgb):
    """A plain word for an RGB triple in 0..1, so a colour can be compared in a sentence."""
    import colorsys

    r, g, b = (max(0.0, min(1.0, float(c))) for c in rgb)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if v < 0.12:
        return "black"
    if s < 0.12:
        return "white" if v > 0.85 else "grey"
    if 0.03 <= h <= 0.13 and v < 0.55:
        return "brown"
    name = "red"
    for start, label in COLOUR_NAMES:
        if h >= start:
            name = label
    return name


def image_colour_summary(path):
    """Mean colour of an image's opaque pixels, as hex and a word, plus the share of the frame it covers.

    Read through Blender's own image loader so it needs nothing beyond bpy and numpy. Meant for a render
    and for a reference cutout alike, so the two can be set side by side as numbers: a coat that renders
    blue against a photograph that is orange is a fact the agent cannot paraphrase away.
    """
    import numpy as np

    image = bpy.data.images.load(os.path.abspath(path), check_existing=False)
    try:
        width, height = image.size
        pixels = np.empty(width * height * image.channels, dtype=np.float32)
        image.pixels.foreach_get(pixels)
        pixels = pixels.reshape(-1, image.channels)
        alpha = pixels[:, 3] if image.channels == 4 else np.ones(len(pixels))
        opaque = pixels[alpha > 0.5][:, :3]
        if len(opaque) == 0:
            return {"meanHex": None, "colour": "nothing visible", "coverage": 0.0}
        # Render pixels come back linear; a PNG cutout is sRGB already. Both are reported in sRGB.
        mean = opaque.mean(axis=0)
        if image.colorspace_settings.name in ("Linear Rec.709", "Linear", "Non-Color"):
            mean = np.where(mean <= 0.0031308, mean * 12.92, 1.055 * np.power(np.clip(mean, 0, 1), 1 / 2.4) - 0.055)
        mean = np.clip(mean, 0.0, 1.0)
        return {"meanHex": "#%02x%02x%02x" % tuple(int(round(c * 255)) for c in mean),
                "colour": name_colour(mean), "coverage": round(float((alpha > 0.5).mean()), 4)}
    finally:
        bpy.data.images.remove(image)


def load_rgba(path):
    """The image as an (H, W, 4) float array in the image's stored values, plus its size."""
    import numpy as np

    image = bpy.data.images.load(os.path.abspath(path), check_existing=False)
    try:
        width, height = image.size
        buffer = np.empty(width * height * image.channels, dtype=np.float32)
        image.pixels.foreach_get(buffer)
        pixels = buffer.reshape(height, width, image.channels)[::-1]  # Blender stores rows bottom-up
        if image.channels == 3:
            pixels = np.concatenate([pixels, np.ones((height, width, 1), dtype=np.float32)], axis=2)
        return pixels, width, height
    finally:
        bpy.data.images.remove(image)


def triangles_of(mesh):
    """The mesh's loop triangles as an (n, 3) array of vertex indices."""
    import numpy as np

    mesh.calc_loop_triangles()
    triangles = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int64)
    mesh.loop_triangles.foreach_get("vertices", triangles)
    return triangles.reshape(-1, 3)

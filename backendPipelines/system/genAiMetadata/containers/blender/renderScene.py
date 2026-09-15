# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Blender-side render script for the BLENDER branch of the SYSTEM - GenAI Metadata pipeline. It runs inside
# `blender --background -noaudio --python renderScene.py -- <arguments>` and is the only module in this image
# that imports bpy (hence the GPL licence header; the licence text is GPL3-license.txt beside it). It imports
# one model, frames the camera over every mesh object, renders the requested views with Cycles on CPU, and
# writes scene_facts.json beside the renders for handler.py to fold into the analysis manifest.
#
# Exit codes: 0 at least one view rendered; 2 the model could not be imported or carries no faces; 3 every
# requested view failed to render; 4 bad arguments.

import argparse
import json
import math
import os
import sys
import time

import bmesh
import bpy
from mathutils import Vector

SCENE_FACTS_FILENAME = "scene_facts.json"
RENDER_FILE_PREFIX = "render_"
DEFAULT_RESOLUTION_PX = 768
DEFAULT_SAMPLES = 32

EXIT_OK = 0
EXIT_IMPORT_FAILED = 2
EXIT_NO_RENDERS = 3
EXIT_BAD_ARGUMENTS = 4

# Extensions this branch imports: the mesh and usd file classes, lower-case with the dot. `.abc` and `.blend`
# are renderer-capable but not allow-listed (no viewer declares them), so the classifier routes neither here
# today; both stay importable for a direct invocation and for the day a viewer adds the extension.
MESH_EXTENSIONS = (".glb", ".gltf", ".fbx", ".obj", ".dae", ".abc", ".blend", ".stl", ".ply")
USD_EXTENSIONS = (".usd", ".usda", ".usdc", ".usdz")

# Formats whose declared unit the importer converts into scene metres, so the world-space numbers in the
# scene facts are metres: glTF is metres by specification; the FBX importer applies GlobalSettings
# UnitScaleFactor; the Collada importer matches <asset><unit meter> to the scene (import_units=False); the
# USD importer multiplies by the stage's metersPerUnit (apply_unit_conversion_scale=True). OBJ, STL, PLY,
# Alembic and .blend carry no unit the importer reads, so their numbers stay unlabelled.
METRE_CONVERTED_EXTENSIONS = (".glb", ".gltf", ".fbx", ".dae") + USD_EXTENSIONS

# Direction from the subject toward the camera in Blender's Z-up world, and whether the view is perspective.
# Insertion order is the RENDER_VIEWS order: the first N entries are rendered, most informative first.
VIEW_DIRECTIONS = {
    "perspective_front": ((1.0, -1.0, 0.75), True),
    "front": ((0.0, -1.0, 0.0), False),
    "right": ((1.0, 0.0, 0.0), False),
    "top": ((0.0, 0.0, 1.0), False),
    "back": ((0.0, 1.0, 0.0), False),
    "left": ((-1.0, 0.0, 0.0), False),
    "bottom": ((0.0, 0.0, -1.0), False),
    "perspective_back": ((-1.0, 1.0, 0.75), True),
}
VIEW_ORDER = tuple(VIEW_DIRECTIONS)

PERSPECTIVE_LENS_MM = 50.0
SENSOR_WIDTH_MM = 36.0
FRAMING_PADDING = 1.15
ORTHO_DISTANCE_FACTOR = 3.0


class SceneImportError(Exception):
    """The model could not be imported into a renderable scene."""


def _import_blend(path):
    # Append every object of the file into the cleared scene. wm.open_mainfile would replace the scene and
    # its render settings; the file's own cameras and lights are culled by remove_cameras_and_lights().
    with bpy.data.libraries.load(path, link=False) as (data_from, data_to):
        data_to.objects = list(data_from.objects)
    for obj in data_to.objects:
        if obj is not None:
            bpy.context.scene.collection.objects.link(obj)


IMPORT_DISPATCH = {
    ".glb": lambda path: bpy.ops.import_scene.gltf(filepath=path),
    ".gltf": lambda path: bpy.ops.import_scene.gltf(filepath=path),
    ".fbx": lambda path: bpy.ops.import_scene.fbx(filepath=path),
    ".obj": lambda path: bpy.ops.wm.obj_import(filepath=path),
    ".dae": lambda path: bpy.ops.wm.collada_import(filepath=path),
    ".abc": lambda path: bpy.ops.wm.alembic_import(filepath=path),
    ".blend": _import_blend,
    ".stl": lambda path: bpy.ops.wm.stl_import(filepath=path),
    ".ply": lambda path: bpy.ops.wm.ply_import(filepath=path),
    ".usd": lambda path: bpy.ops.wm.usd_import(filepath=path),
    ".usda": lambda path: bpy.ops.wm.usd_import(filepath=path),
    ".usdc": lambda path: bpy.ops.wm.usd_import(filepath=path),
    ".usdz": lambda path: bpy.ops.wm.usd_import(filepath=path),
}


def import_model(path):
    """Import `path` with the importer for its extension; raise SceneImportError when there is none."""
    extension = os.path.splitext(path)[1].lower()
    importer = IMPORT_DISPATCH.get(extension)
    if importer is None:
        raise SceneImportError(f"no Blender importer for extension {extension!r}")
    importer(path)


def imported_units(extension):
    """The unit of the scene's world-space numbers after import: "m" when the importer converted the
    format's declared unit into scene metres, None when the format carries none."""
    return "m" if (extension or "").lower() in METRE_CONVERTED_EXTENSIONS else None


def select_views(count):
    """The first `count` views of VIEW_ORDER, clamped to 1..len(VIEW_ORDER)."""
    count = max(1, min(int(count), len(VIEW_ORDER)))
    return VIEW_ORDER[:count]


def render_file_name(view):
    return f"{RENDER_FILE_PREFIX}{view}.png"


def normalized(vector):
    length = math.sqrt(sum(component * component for component in vector))
    if length == 0.0:
        raise ValueError("a view direction cannot be the zero vector")
    return tuple(component / length for component in vector)


def combine_bounds(corners):
    """Centre and size of the axis-aligned box enclosing every corner of every object.

    `corners` is an iterable of (x, y, z) tuples: the eight bound_box corners of each mesh object with its
    matrix_world applied. Raises ValueError when there are none."""
    corners = list(corners)
    if not corners:
        raise ValueError("no corners to frame")
    mins = [min(corner[axis] for corner in corners) for axis in range(3)]
    maxs = [max(corner[axis] for corner in corners) for axis in range(3)]
    center = tuple((mins[axis] + maxs[axis]) / 2.0 for axis in range(3))
    size = tuple(maxs[axis] - mins[axis] for axis in range(3))
    return center, size


def camera_placement(center, size, direction, is_perspective,
                     lens_mm=PERSPECTIVE_LENS_MM, sensor_mm=SENSOR_WIDTH_MM, padding=FRAMING_PADDING):
    """Where the camera goes for one view so the whole bounding box is in frame.

    Perspective views fit the bounding SPHERE (radius = half the box diagonal) inside the lens' field of
    view, so a diagonal view cannot clip a corner. Orthographic views sit ORTHO_DISTANCE_FACTOR extents
    away with ortho_scale covering the two axes perpendicular to the dominant view axis."""
    direction = normalized(direction)
    radius = 0.5 * math.sqrt(sum(extent * extent for extent in size))
    if is_perspective:
        half_fov = math.atan(sensor_mm / (2.0 * lens_mm))
        distance = max((radius / math.sin(half_fov)) * padding, 0.01)
        ortho_scale = None
    else:
        distance = max(max(size) * ORTHO_DISTANCE_FACTOR, 0.01)
        dominant = max(range(3), key=lambda axis: abs(direction[axis]))
        visible = max(size[axis] for axis in range(3) if axis != dominant)
        ortho_scale = max(visible * padding, 0.01)
    location = tuple(center[axis] + direction[axis] * distance for axis in range(3))
    return {
        "location": location,
        "distance": distance,
        "ortho_scale": ortho_scale,
        "clip_end": distance + 2.0 * radius + 1.0,
    }


def clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for collection in (bpy.data.meshes, bpy.data.materials, bpy.data.cameras, bpy.data.lights, bpy.data.images):
        for datablock in list(collection):
            if datablock.users == 0:
                collection.remove(datablock)


def remove_cameras_and_lights():
    for obj in list(bpy.data.objects):
        if obj.type in ("CAMERA", "LIGHT"):
            bpy.data.objects.remove(obj, do_unlink=True)


def mesh_objects():
    return [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]


def world_corners(objects):
    for obj in objects:
        for corner in obj.bound_box:
            world = obj.matrix_world @ Vector(corner)
            yield (world.x, world.y, world.z)


def ensure_materials(objects):
    """Node materials render; slots without one get a shared neutral grey so unlit files stay visible."""
    default = None
    for obj in objects:
        if any(material is not None for material in obj.data.materials):
            for material in obj.data.materials:
                if material is not None and not material.use_nodes:
                    material.use_nodes = True
            continue
        if default is None:
            default = bpy.data.materials.new(name="GenAiDefaultGrey")
            default.use_nodes = True
            bsdf = default.node_tree.nodes.get("Principled BSDF")
            if bsdf is not None:
                bsdf.inputs["Base Color"].default_value = (0.8, 0.8, 0.8, 1.0)
                bsdf.inputs["Roughness"].default_value = 0.6
        if len(obj.data.materials):
            obj.data.materials[0] = default
        else:
            obj.data.materials.append(default)


def look_at(obj, target):
    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def setup_lighting(center, radius):
    """Three sun lights (key, fill, rim) around the subject and a neutral grey world background."""
    reach = max(radius, 1.0) * 2.0
    for name, offset, energy in (
        ("KeyLight", (1.0, -0.6, 2.0), 4.0),
        ("FillLight", (-1.0, 0.6, 1.0), 2.0),
        ("RimLight", (0.0, 1.0, 1.6), 1.5),
    ):
        light_data = bpy.data.lights.new(name=name, type="SUN")
        light_data.energy = energy
        light_data.angle = 0.2
        light = bpy.data.objects.new(name, light_data)
        light.location = tuple(center[axis] + offset[axis] * reach for axis in range(3))
        bpy.context.scene.collection.objects.link(light)
        look_at(light, center)
    world = bpy.context.scene.world
    if world is None:
        world = bpy.data.worlds.new("GenAiWorld")
        bpy.context.scene.world = world
    world.use_nodes = True
    nodes = world.node_tree.nodes
    nodes.clear()
    background = nodes.new(type="ShaderNodeBackground")
    background.inputs["Color"].default_value = (0.4, 0.4, 0.4, 1.0)
    background.inputs["Strength"].default_value = 1.0
    output = nodes.new(type="ShaderNodeOutputWorld")
    world.node_tree.links.new(background.outputs["Background"], output.inputs["Surface"])


def setup_camera():
    camera_data = bpy.data.cameras.new("GenAiCamera")
    camera = bpy.data.objects.new("GenAiCamera", camera_data)
    bpy.context.scene.collection.objects.link(camera)
    bpy.context.scene.camera = camera
    return camera


def configure_render(resolution_px, samples):
    """Cycles on CPU: the only engine that renders in --background with neither a display nor a GPU."""
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = samples
    scene.cycles.use_adaptive_sampling = True
    scene.cycles.adaptive_threshold = 0.1
    scene.cycles.use_denoising = True
    scene.cycles.denoiser = "OPENIMAGEDENOISE"
    scene.cycles.max_bounces = 4
    scene.render.use_persistent_data = True
    scene.render.resolution_x = resolution_px
    scene.render.resolution_y = resolution_px
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.render.use_file_extension = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.color_depth = "8"
    scene.render.image_settings.compression = 50
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"


def place_camera(camera, center, size, view):
    direction, is_perspective = VIEW_DIRECTIONS[view]
    placement = camera_placement(center, size, direction, is_perspective)
    camera.location = placement["location"]
    look_at(camera, center)
    camera.data.clip_start = 0.001
    camera.data.clip_end = placement["clip_end"]
    if is_perspective:
        camera.data.type = "PERSP"
        camera.data.lens = PERSPECTIVE_LENS_MM
        camera.data.sensor_width = SENSOR_WIDTH_MM
    else:
        camera.data.type = "ORTHO"
        camera.data.ortho_scale = placement["ortho_scale"]


def render_view(camera, center, size, view, output_dir):
    place_camera(camera, center, size, view)
    bpy.context.view_layer.update()
    target = os.path.join(output_dir, render_file_name(view))
    bpy.context.scene.render.filepath = target
    bpy.ops.render.render(write_still=True)
    if not os.path.isfile(target):
        raise RuntimeError(f"Blender wrote no file for view {view}")
    return target


def mesh_measurements(objects):
    """World-space surface area, closedness and volume over every mesh object, plus the UV / colour-attribute
    flags. Each mesh goes through a bmesh transformed by the object's matrix_world (scale included) before
    measuring; the set is watertight when every edge of every mesh is manifold, and only then is the summed
    volume reported -- an open mesh has no defined volume."""
    surface_area = 0.0
    volume = 0.0
    watertight = bool(objects)
    has_uv = False
    has_vertex_colors = False
    for obj in objects:
        mesh = obj.data
        if len(mesh.uv_layers) > 0:
            has_uv = True
        if len(mesh.color_attributes) > 0:
            has_vertex_colors = True
        bm = bmesh.new()
        try:
            bm.from_mesh(mesh)
            bm.transform(obj.matrix_world)
            surface_area += sum(face.calc_area() for face in bm.faces)
            if any(not edge.is_manifold for edge in bm.edges):
                watertight = False
            volume += bm.calc_volume(signed=False)
        finally:
            bm.free()
    measurements = {
        "surfaceArea": round(surface_area, 6),
        "watertight": watertight,
        "hasUv": has_uv,
        "hasVertexColors": has_vertex_colors,
    }
    if watertight:
        measurements["volume"] = round(volume, 6)
    return measurements


def usd_stage_meters_per_unit(path):
    """(metersPerUnit, authored) of a USD stage through Blender's bundled pxr module -- the composed stage's
    value, which also covers a binary .usdc the handler cannot parse -- or (None, None) when the module is
    absent or the stage does not open. LoadNone keeps payloads unloaded."""
    try:
        from pxr import Usd, UsdGeom
    except ImportError:
        return None, None
    try:
        stage = Usd.Stage.Open(path, Usd.Stage.LoadNone)
    except Exception:
        return None, None
    if stage is None:
        return None, None
    return float(UsdGeom.GetStageMetersPerUnit(stage)), bool(UsdGeom.StageHasAuthoredMetersPerUnit(stage))


def collect_scene_facts(objects, center, size, input_path):
    extension = os.path.splitext(input_path)[1].lower()
    counts = {}
    for obj in bpy.context.scene.objects:
        counts[obj.type] = counts.get(obj.type, 0) + 1
    vertices = faces = triangles = 0
    for obj in objects:
        mesh = obj.data
        vertices += len(mesh.vertices)
        faces += len(mesh.polygons)
        triangles += len(mesh.loop_triangles)
    material_names = sorted({
        slot.material.name for obj in objects for slot in obj.material_slots if slot.material is not None
    })
    images = [
        image for image in bpy.data.images
        if image.type == "IMAGE" and image.name not in ("Render Result", "Viewer Node")
    ]
    facts = {
        "schemaVersion": 1,
        "blenderVersion": bpy.app.version_string,
        "meshObjects": len(objects),
        "objectCounts": counts,
        "vertices": vertices,
        "faces": faces,
        "triangles": triangles,
        "boundsMin": [round(center[axis] - size[axis] / 2.0, 6) for axis in range(3)],
        "boundsMax": [round(center[axis] + size[axis] / 2.0, 6) for axis in range(3)],
        "dimensions": {"width": round(size[0], 6), "height": round(size[1], 6), "depth": round(size[2], 6)},
        "materials": len(material_names),
        "materialNames": material_names[:20],
        "images": len(images),
        "hasArmature": counts.get("ARMATURE", 0) > 0,
        "hasAnimation": len(bpy.data.actions) > 0,
        "upAxis": "Z",
    }
    facts.update(mesh_measurements(objects))
    units = imported_units(extension)
    if units:
        facts["units"] = units
    if extension in USD_EXTENSIONS:
        meters_per_unit, authored = usd_stage_meters_per_unit(input_path)
        if meters_per_unit is not None:
            facts["metersPerUnit"] = meters_per_unit
            facts["metersPerUnitAuthored"] = authored
    return facts


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="renderScene.py")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--views", default=",".join(VIEW_ORDER))
    parser.add_argument("--resolution", type=int, default=DEFAULT_RESOLUTION_PX)
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    return parser.parse_args(argv)


def script_arguments():
    if "--" in sys.argv:
        return sys.argv[sys.argv.index("--") + 1:]
    return []


def write_facts(output_dir, facts):
    with open(os.path.join(output_dir, SCENE_FACTS_FILENAME), "w", encoding="utf-8") as handle:
        json.dump(facts, handle)


def main():
    try:
        args = parse_args(script_arguments())
    except SystemExit:
        return EXIT_BAD_ARGUMENTS
    os.makedirs(args.output_dir, exist_ok=True)
    views = [view for view in args.views.split(",") if view]
    unknown = [view for view in views if view not in VIEW_DIRECTIONS]
    if unknown or not views:
        write_facts(args.output_dir, {"schemaVersion": 1, "error": f"unknown views: {unknown or 'none requested'}"})
        return EXIT_BAD_ARGUMENTS

    started = time.monotonic()
    clear_scene()
    try:
        import_model(args.input)
        remove_cameras_and_lights()
        bpy.context.view_layer.update()
        objects = mesh_objects()
        if not objects:
            raise SceneImportError("the file imported no mesh objects")
        if sum(len(obj.data.polygons) for obj in objects) == 0:
            raise SceneImportError("the imported mesh objects carry no faces")
        center, size = combine_bounds(world_corners(objects))
    except Exception as error:  # any loader failure is an import failure for this branch
        write_facts(args.output_dir, {
            "schemaVersion": 1,
            "blenderVersion": bpy.app.version_string,
            "error": f"{type(error).__name__}: {error}",
        })
        print(f"renderScene: import failed: {type(error).__name__}: {error}", file=sys.stderr)
        return EXIT_IMPORT_FAILED

    facts = collect_scene_facts(objects, center, size, args.input)
    facts["importSeconds"] = round(time.monotonic() - started, 3)
    ensure_materials(objects)
    configure_render(args.resolution, args.samples)
    setup_lighting(center, 0.5 * math.sqrt(sum(extent * extent for extent in size)))
    camera = setup_camera()

    rendered, failed = [], {}
    for view in views:
        try:
            render_view(camera, center, size, view, args.output_dir)
            rendered.append(view)
        except Exception as error:
            failed[view] = f"{type(error).__name__}: {error}"
            print(f"renderScene: view {view} failed: {failed[view]}", file=sys.stderr)
    facts["renderedViews"] = rendered
    facts["failedViews"] = failed
    facts["renderSeconds"] = round(time.monotonic() - started, 3)
    write_facts(args.output_dir, facts)
    return EXIT_OK if rendered else EXIT_NO_RENDERS


if __name__ == "__main__":
    sys.exit(main())

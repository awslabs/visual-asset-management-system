# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Loading and writing the formats beyond what trimesh does on its own, and applying the unit and
up-axis plan (worker child only).

- ``.off`` and the formats trimesh reads as they are load through trimesh.
- ``.dae`` and ``.3mf`` are XML: every XML document is read through the hardened parser (no DTD, no
  entity) before the loader sees the file, and a 3MF archive is bounded from its central directory
  before anything is read from it.
- ``.usd .usda .usdc .usdz`` go through ``usd_io``.

``load_model`` returns what was loaded and the unit and up axis the file declares (None for a part it
does not declare); ``export_model`` applies a ``units_axes.Plan`` and writes the output.
"""

import os
import re
import struct
import subprocess
import tempfile
import zipfile

import numpy as np

import archive_guard
import units_axes
import usd_io
import xml_guard

USD_EXTENSIONS = ('.usd', '.usda', '.usdc', '.usdz')
STEP_EXTENSIONS = ('.step', '.stp')

# The Assimp command-line tool the image builds (Dockerfile, assimp-builder stage) with the FBX importer
# and the glTF exporter only.
ASSIMP_BIN = "/opt/assimp/bin/assimp"
# Seconds kept from the worker's own time limit for loading and writing after Assimp.
ASSIMP_TIME_RESERVE_SECONDS = 30
# The most of Assimp's output kept for an error message.
ASSIMP_OUTPUT_CHARS = 2000
# An FBX file's own unit is the centimetre; ``UnitScaleFactor`` is centimetres per unit.
FBX_METERS_PER_SCALE_UNIT = 0.01
_FBX_ASCII_UNIT = re.compile(
    rb'"UnitScaleFactor"\s*,\s*"double"\s*,\s*"Number"\s*,\s*"[^"]*"\s*,\s*([-+0-9.eE]+)')
# The part of a COLLADA file that declares its unit and up axis is its <asset> element, at the top.
_DAE_HEAD_BYTES = 256 * 1024
_UNIT_PATTERN = re.compile(rb"<unit\b[^>]*\bmeter\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)
_UP_PATTERN = re.compile(rb"<up_axis>\s*([XYZxyz])_UP\s*</up_axis>")


class _NullBuilder:
    """A parse target that keeps nothing: the document is only checked."""

    def start(self, tag, attributes):
        return None

    def end(self, tag):
        return None

    def data(self, text):
        return None


def check_xml_safe(source):
    """Parse ``source`` (a path or a binary file object) through the hardened parser without building a
    tree; raises ``ValueError`` for a DTD, an entity declaration or malformed XML."""
    parser = xml_guard._safe_expat(_NullBuilder())
    handle = open(source, "rb") if isinstance(source, str) else source
    try:
        while True:
            chunk = handle.read(4 * 1024 ** 2)
            if not chunk:
                break
            parser.Parse(chunk, False)
        parser.Parse(b"", True)
    except xml_guard.expat.ExpatError as e:
        raise ValueError(f"Invalid XML: {e}")
    finally:
        if isinstance(source, str):
            handle.close()


def _check_dae(path):
    try:
        check_xml_safe(path)
    except ValueError as e:
        raise ValueError(f"{os.path.basename(path)} is not a COLLADA file this pipeline reads: {e}")
    with open(path, "rb") as handle:
        head = handle.read(_DAE_HEAD_BYTES)
    unit = _UNIT_PATTERN.search(head)
    up = _UP_PATTERN.search(head)
    meters = None
    if unit:
        try:
            meters = float(unit.group(1))
        except ValueError:
            meters = None
        if meters is not None and not (meters > 0 and meters < 1e9):
            meters = None
    # COLLADA's specification defaults: one metre per unit, Y up.
    declared_up = up.group(1).decode().lower() if up else 'y'
    if declared_up == 'x':
        raise ValueError(f"{os.path.basename(path)} declares X as its up axis, which this pipeline does not convert.")
    return (meters if meters is not None else 1.0), declared_up


def _check_3mf(path, work_dir):
    name = os.path.basename(path)
    try:
        archive_guard.check_archive(path, work_dir)
    except (archive_guard.ArchiveLimitError, zipfile.BadZipFile) as e:
        raise ValueError(f"{name} is not a 3MF package this pipeline reads: {e}")
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            member = info.filename
            if member.startswith(("/", "\\")) or ".." in re.split(r"[\\/]", member):
                raise ValueError(f"{name} holds a package member named {member[:100]!r}, which is not allowed.")
            if member.lower().endswith((".model", ".rels", ".xml")):
                try:
                    with archive.open(info) as handle:
                        check_xml_safe(handle)
                except ValueError as e:
                    raise ValueError(f"{name} is not a 3MF package this pipeline reads: {member}: {e}")


def fbx_meters_per_unit(path, scan_bytes=64 * 1024 ** 2):
    """Metres per unit an FBX file declares (``GlobalSettings`` ``UnitScaleFactor``, in centimetres),
    read from its ASCII or binary property record; the FBX default of one centimetre when it names
    none or a value that is not a positive number."""
    with open(path, "rb") as handle:
        data = handle.read(scan_bytes)
    factor = None
    match = _FBX_ASCII_UNIT.search(data)
    if match:
        try:
            factor = float(match.group(1))
        except ValueError:
            factor = None
    else:
        at = data.find(b"UnitScaleFactor")
        number = data.find(b"Number", at) if at >= 0 else -1
        position = number + len(b"Number") if number >= 0 and number - at < 64 else -1
        # Binary properties: string fields are b'S' + uint32 length + bytes, a double is b'D' + 8 bytes.
        for _ in range(4):
            if position < 0 or position >= len(data):
                break
            code = data[position:position + 1]
            if code == b"S" and position + 5 <= len(data):
                (length,) = struct.unpack("<I", data[position + 1:position + 5])
                position += 5 + length
            elif code == b"D" and position + 9 <= len(data):
                (factor,) = struct.unpack("<d", data[position + 1:position + 9])
                break
            else:
                break
    if factor is None or not (0 < factor < 1e9):
        factor = 1.0
    return factor * FBX_METERS_PER_SCALE_UNIT


def check_fbx_scene(path, name, max_triangles=None, max_nodes=None):
    """Refuse an FBX file before Assimp reads it when its scene places more models than the
    ``SceneLimit`` node bound, places more triangles or vertices through instancing than the triangle
    budget allows beyond what the file stores once, or nests deeper than
    ``fbx_scene.MAX_FBX_NESTING_DEPTH``. Assimp builds a node for every placement and copies the
    geometry of every instance before it returns, so a few kilobytes can describe billions of
    placements; the count is read from the file in one linear pass (the 3D thumbnail's check)."""
    import fbx_scene
    import scene_limits
    limit = scene_limits.SceneLimit("FBX scene", max_triangles, max_nodes)
    try:
        size = fbx_scene.scene_size(path, limit.max_nodes)
    except ValueError as e:
        raise ValueError(f"{name} could not be read as FBX: {e}")
    if not limit.expect(size.models):
        raise ValueError(f"{name}: the FBX scene places more than {limit.max_nodes:,} models, more than "
                         f"this function converts.")
    allowed_triangles = max(limit.max_triangles, size.stored_triangles)
    if size.triangles > allowed_triangles:
        raise ValueError(f"{name}: the FBX scene places more than {allowed_triangles:,} triangles through "
                         f"instancing, more than this function converts.")
    allowed_vertices = max(scene_limits.VERTICES_PER_TRIANGLE * limit.max_triangles, size.stored_vertices)
    if size.vertices > allowed_vertices:
        raise ValueError(f"{name}: the FBX scene places more than {allowed_vertices:,} vertices through "
                         f"instancing, more than this function converts.")
    return size


def _load_fbx(job, trimesh, resolver):
    """Convert the FBX to GLB with the Assimp tool, in the input's directory, then load the GLB as the
    staged model. Assimp runs in this worker's process group, under its address-space and file-size
    limits and with its scrubbed environment; it applies the file's up axis (the GLB is Y up) and not
    its unit, which is read from the file here. Texture images the GLB names are read through
    ``resolver``, so only the files fetched with the input can reach the model."""
    path = job["input_file"]
    name = os.path.basename(path)
    check_fbx_scene(path, name)
    if not os.path.isfile(ASSIMP_BIN):
        raise RuntimeError("The Assimp tool is not in this image, so FBX cannot be read.")
    intermediate = os.path.join(tempfile.mkdtemp(prefix="fbx_", dir=os.path.dirname(job["input_dir"].rstrip("/"))),
                                os.path.splitext(name)[0] + ".glb")
    timeout = max(int(job.get("tool_timeout_seconds") or 600) - ASSIMP_TIME_RESERVE_SECONDS, 10)
    try:
        completed = subprocess.run(  # nosec B603 - fixed binary, no shell, paths from the job
            [ASSIMP_BIN, "export", path, intermediate, "-fglb2"], cwd=os.path.dirname(path),
            stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout,
            env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"})
    except subprocess.TimeoutExpired:
        raise ValueError(f"{name} could not be read as FBX within {timeout} seconds.")
    if completed.returncode != 0 or not os.path.isfile(intermediate):
        detail = (completed.stdout + completed.stderr).decode("utf-8", errors="replace")[-ASSIMP_OUTPUT_CHARS:]
        lines = [line for line in detail.splitlines() if "rror" in line or "fail" in line.lower()]
        raise ValueError(f"{name} could not be read as FBX: " + ("; ".join(lines[-3:]) or "the importer failed")[:500])
    loaded = trimesh.load(intermediate, file_type="glb", resolver=resolver)
    os.remove(intermediate)
    return loaded, fbx_meters_per_unit(path), 'y'


# STEP tessellation: the linear deflection relative to each edge's length and the angular deflection
# in radians, passed to cascadio (Open CASCADE). cascadio writes metres and keeps the file's Z up.
STEP_LINEAR_DEFLECTION = 0.01
STEP_ANGULAR_DEFLECTION = 0.5


def step_input_enabled(environ=None):
    """Whether this image was built with STEP input (app.pipelines.useConversion3dBasic.enableStepInput,
    passed by CDK as the ENABLE_STEP_INPUT build argument and environment variable)."""
    environ = os.environ if environ is None else environ
    return str(environ.get("ENABLE_STEP_INPUT", "")).strip().lower() == "true"


def _load_step(job, trimesh):
    """Tessellate a STEP file with cascadio to a GLB and load it: ``(model, 1.0, 'z')``. The assembly's
    placements are counted from the text first and a file past the scene bound is refused."""
    import scene_limits
    import step_limits
    path = job["input_file"]
    name = os.path.basename(path)
    try:
        import cascadio
    except ImportError:
        raise ValueError(f"{name} is a STEP file, and this deployment does not read STEP: set "
                         f"app.pipelines.useConversion3dBasic.enableStepInput to true and deploy again.")
    limit = scene_limits.SceneLimit("STEP assembly")
    if not limit.expect(step_limits.assembly_occurrences(path, limit.max_nodes)):
        raise ValueError(f"{name}: the STEP assembly places more than {limit.max_nodes:,} components, more "
                         f"than this function converts.")
    intermediate = os.path.join(tempfile.mkdtemp(prefix="step_", dir=os.path.dirname(job["input_dir"].rstrip("/"))),
                                os.path.splitext(name)[0] + ".glb")
    try:
        status = cascadio.step_to_glb(path, intermediate, tol_linear=STEP_LINEAR_DEFLECTION,
                                      tol_angular=STEP_ANGULAR_DEFLECTION, tol_relative=True,
                                      use_parallel=False)
    except Exception as e:  # noqa: BLE001 - an unreadable STEP is the author's to fix
        raise ValueError(f"{name} could not be read as STEP: {str(e)[:300]}")
    if status != 0 or not os.path.isfile(intermediate) or os.path.getsize(intermediate) == 0:
        raise ValueError(f"{name} could not be read as STEP: Open CASCADE wrote no model.")
    loaded = trimesh.load(intermediate, file_type="glb")
    os.remove(intermediate)
    return loaded, 1.0, 'z'


def load_model(job, trimesh):
    """``(loaded, declared_meters_per_unit, declared_up_axis)`` for the staged input."""
    import referenced_files
    path = job["input_file"]
    extension = os.path.splitext(job["input_name"])[1].lower()
    if extension in USD_EXTENSIONS:
        scene, meters, up = usd_io.read_usd(path, job["input_dir"])
        return scene, meters, up
    declared_meters = declared_up = None
    if extension == '.dae':
        declared_meters, declared_up = _check_dae(path)
    elif extension == '.3mf':
        _check_3mf(path, os.path.dirname(path))
    resolver = referenced_files.ConfinedResolver(
        job["input_dir"], job["input_relative_path"], job["by_reference"])
    if extension in STEP_EXTENSIONS:
        loaded, meters, up = _load_step(job, trimesh)
        return _faceless_mesh_as_points(loaded, trimesh), meters, up
    if extension == '.fbx':
        loaded, meters, up = _load_fbx(job, trimesh, resolver)
        return _faceless_mesh_as_points(loaded, trimesh), meters, up
    loaded = trimesh.load(path, resolver=resolver)
    if extension == '.3mf':
        declared_meters = units_axes.meters_per_unit_from_name(
            (getattr(loaded, "metadata", None) or {}).get("units")) or units_axes.UNITS['mm']
        declared_up = 'z'
    loaded = _faceless_mesh_as_points(loaded, trimesh)
    meters, up = units_axes.declared_for(extension, declared_meters, declared_up)
    return loaded, meters, up


def _faceless_mesh_as_points(loaded, trimesh):
    """A mesh with vertices and no faces (a vertex-only OFF file loads as one) is a point cloud:
    trimesh's exporters write such a mesh as an empty model without raising, so it is converted to a
    ``PointCloud`` that the point-capable targets keep."""
    if isinstance(loaded, trimesh.Trimesh) and len(loaded.faces) == 0 and len(loaded.vertices) > 0:
        return trimesh.PointCloud(loaded.vertices)
    return loaded


def apply_plan(loaded, plan):
    matrix = units_axes.transform_matrix(plan)
    if matrix is None:
        return
    loaded.apply_transform(np.array(matrix, dtype=np.float64))


def _meshes(loaded):
    import trimesh
    if isinstance(loaded, trimesh.Scene):
        return [m for m in loaded.dump() if getattr(m, "faces", None) is not None and len(m.faces)]
    return [loaded]


def _concatenated(loaded):
    import trimesh
    meshes = _meshes(loaded)
    if not meshes:
        raise ValueError("The model has no triangles to write.")
    return meshes[0] if len(meshes) == 1 else trimesh.util.concatenate(meshes)


def _write_dae(loaded, plan, output_file):
    import trimesh.exchange.dae as dae
    data = dae.export_collada(_meshes(loaded))
    if isinstance(data, str):
        data = data.encode("utf-8")
    unit = f'<unit name="unit" meter="{plan.meters_per_unit!r}"/>'.encode()
    up = b"Z_UP" if plan.up_axis == 'z' else b"Y_UP"
    patched = re.sub(rb"<up_axis>[A-Z]_UP</up_axis>", unit + b"<up_axis>" + up + b"</up_axis>", data, count=1)
    if patched == data:
        raise RuntimeError("The COLLADA export has no up_axis element to declare the unit with.")
    with open(output_file, "wb") as handle:
        handle.write(patched)


def _write_3mf(loaded, plan, output_file):
    import trimesh
    scene = loaded if isinstance(loaded, trimesh.Scene) else trimesh.Scene(loaded)
    data = scene.export(file_type="3mf")
    unit = units_axes.threemf_unit_name(plan.meters_per_unit)
    source = tempfile.TemporaryFile()
    try:
        source.write(data)
        source.seek(0)
        with zipfile.ZipFile(source) as archive, zipfile.ZipFile(output_file, "w", zipfile.ZIP_DEFLATED) as out:
            for info in archive.infolist():
                content = archive.read(info.filename)
                if info.filename.lower().endswith(".model"):
                    content = content.replace(b'unit="millimeter"', f'unit="{unit}"'.encode(), 1)
                out.writestr(info.filename, content)
    finally:
        source.close()


# The outputs whose exporter writes curves (trimesh Path geometry); every other drops them.
PATH_OUTPUTS = ('.glb', '.gltf')
# The outputs that store triangles only; a point cloud beside triangles in one scene is dropped there.
TRIANGLE_ONLY_OUTPUTS = ('.stl', '.dae', '.3mf', '.off')


def prune_for_output(loaded, extension, logger=None):
    """Remove from a scene the geometry the output format cannot hold: curves (a STEP file's edges and
    product and manufacturing information load as Path geometry) for every output but glTF and GLB, and
    point clouds beside triangles for a triangle-only output. ``check_exportable`` has already refused
    a model that has nothing the output can hold. Returns the number of geometries removed."""
    import trimesh
    if not isinstance(loaded, trimesh.Scene):
        return 0
    removed = []
    for name, geometry in list(loaded.geometry.items()):
        is_path = isinstance(geometry, trimesh.path.path.Path)
        is_points = isinstance(geometry, trimesh.PointCloud)
        if (is_path and extension not in PATH_OUTPUTS) or (is_points and extension in TRIANGLE_ONLY_OUTPUTS):
            removed.append(name)
    for name in removed:
        loaded.delete_geometry(name)
    if removed and logger is not None:
        logger.info(f"{len(removed)} curve or point geometries are not written to {extension}")
    return len(removed)


def export_model(loaded, plan, job):
    """Apply ``plan`` and write ``job["output_file"]`` in ``job["output_filetype"]``."""
    extension = job["output_filetype"]
    output_file = job["output_file"]
    prune_for_output(loaded, extension)
    apply_plan(loaded, plan)
    if extension in USD_EXTENSIONS:
        usd_io.write_usd(loaded, output_file, plan.meters_per_unit, plan.up_axis, extension, job["input_name"])
    elif extension == '.dae':
        _write_dae(loaded, plan, output_file)
    elif extension == '.3mf':
        _write_3mf(loaded, plan, output_file)
    elif extension == '.off':
        _concatenated(loaded).export(output_file, file_type="off")
    else:
        # NOTE: output_filetype carries a leading dot, e.g. ".stl", but trimesh's exporter registry is
        # keyed without it ("stl"). Passing the dotted form raises ValueError("%s exporter not
        # available!", ".stl") and fails every conversion, so strip it here.
        loaded.export(output_file, file_type=extension.lstrip('.'))

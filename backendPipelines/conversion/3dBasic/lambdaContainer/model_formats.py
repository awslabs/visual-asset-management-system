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
import tempfile
import zipfile

import numpy as np

import archive_guard
import units_axes
import usd_io
import xml_guard

USD_EXTENSIONS = ('.usd', '.usda', '.usdc', '.usdz')
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


def export_model(loaded, plan, job):
    """Apply ``plan`` and write ``job["output_file"]`` in ``job["output_filetype"]``."""
    extension = job["output_filetype"]
    output_file = job["output_file"]
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

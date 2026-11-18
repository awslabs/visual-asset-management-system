# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The part of a conversion that parses and writes untrusted model files.

``lambda.py`` runs this module as a child process (``python conversion_worker.py <job> <result>``) so
the parsers run

- without the function's AWS credentials: the child is started with a scrubbed environment (no
  ``AWS_*`` variable, ``HOME=/tmp``) and this module never creates an AWS client,
- under an address-space limit (``RLIMIT_AS``), so a model that expands past it fails with a
  ``MemoryError`` the parent reports, instead of the function being killed for memory, and
- under a wall-clock limit the parent enforces, so a parse that does not finish cannot use the
  function's whole timeout.

The parent keeps all Amazon S3 traffic: it downloads the input and the files it references into the
job's directories before starting the child, and uploads what the child wrote afterwards. The job and
the result are small JSON files; nothing else crosses the process boundary.

Importing this module imports neither trimesh nor boto3; ``convert_model`` imports trimesh when it
runs, so the parent process (which only checks the input and moves files) never loads the parsers.
"""

import json
import os
import sys
import traceback

# The output formats that store triangles and nothing else. A point cloud (an `.xyz` file, or a `.ply`
# with vertices and no faces) has none, and trimesh writes it to STL as a header and a zero triangle
# count -- an 84-byte file -- without raising, so the run would report success for an empty model.
# OBJ, PLY, GLB and GLTF all store the points.
MESH_ONLY_OUTPUT_FORMATS = {'.stl'}

# Exceptions the child reports to the parent by type, so the parent re-raises the same kind and the
# run's error text is the one the model's author can act on.
USER_ERRORS = {"ValueError": ValueError}


def geometry_counts(loaded):
    """``(vertices, triangles)`` summed over what trimesh loaded: a mesh, a point cloud, a path, or a
    scene holding any of those."""
    geometry = getattr(loaded, "geometry", None)
    parts = list(geometry.values()) if isinstance(geometry, dict) else [loaded]
    vertices = triangles = 0
    for part in parts:
        part_vertices = getattr(part, "vertices", None)
        part_faces = getattr(part, "faces", None)
        vertices += 0 if part_vertices is None else len(part_vertices)
        triangles += 0 if part_faces is None else len(part_faces)
    return vertices, triangles


def check_exportable(loaded, output_filetype, input_name):
    """Raise ``ValueError`` when the export would write a model with nothing in it: an input that
    loaded no geometry at all, or one with no triangles for a target that stores only triangles."""
    vertices, triangles = geometry_counts(loaded)
    if vertices == 0:
        raise ValueError(f"{input_name} contains no geometry, so there is nothing to convert")
    if output_filetype in MESH_ONLY_OUTPUT_FORMATS and triangles == 0:
        raise ValueError(
            f"{input_name} contains no triangles (it is a point cloud), and {output_filetype} stores "
            f"triangle meshes only. Convert it to .ply, .obj, .glb or .gltf, which store points.")


def convert_model(job, trimesh=None):
    """Load the staged input and write the converted model to ``job["output_file"]``.

    ``job`` holds ``input_file`` (the staged input), ``input_dir`` and ``input_relative_path`` (where
    it was staged), ``by_reference`` (each reference as the model spells it, mapped to the local file
    that was fetched for it), ``output_file``, ``output_filetype`` (with its leading dot) and
    ``input_name``. ``trimesh`` is the module to use; it is imported here when not given.
    """
    if trimesh is None:
        import trimesh
    import referenced_files

    # The loader is given a resolver that serves only the fetched files, so no reference reaches
    # anything else on the local disk.
    resolver = referenced_files.ConfinedResolver(
        job["input_dir"], job["input_relative_path"], job["by_reference"])
    mesh = trimesh.load(job["input_file"], resolver=resolver)
    check_exportable(mesh, job["output_filetype"], job["input_name"])
    # NOTE: output_filetype carries a leading dot, e.g. ".stl", but trimesh's exporter registry is keyed
    # without it ("stl"). Passing the dotted form raises ValueError("%s exporter not available!",
    # ".stl") and fails every conversion, so strip it here.
    mesh.export(job["output_file"], file_type=job["output_filetype"].lstrip('.'))


def _limit_address_space(limit_bytes):
    """Cap this process's address space; a no-op when the platform has no RLIMIT_AS."""
    if not limit_bytes:
        return
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (int(limit_bytes), int(limit_bytes)))
    except (ImportError, ValueError, OSError):
        pass


def _write_result(path, result):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(result, handle)


def main(argv):
    job_path, result_path = argv[1], argv[2]
    with open(job_path, encoding="utf-8") as handle:
        job = json.load(handle)
    # Before anything heavy is imported, so the limit covers the parsers' own allocations.
    _limit_address_space(job.get("address_space_bytes"))
    try:
        convert_model(job)
    except MemoryError:
        _write_result(result_path, {"ok": False, "type": "MemoryError",
                                    "message": "The model needs more memory than this function has."})
        return 1
    except BaseException as e:  # noqa: BLE001 - every failure is reported to the parent
        kind = type(e).__name__
        if kind not in USER_ERRORS:
            traceback.print_exc(file=sys.stderr)
        _write_result(result_path, {"ok": False, "type": kind, "message": str(e)})
        return 1
    _write_result(result_path, {"ok": True})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

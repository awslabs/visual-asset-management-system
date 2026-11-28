# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The part of a conversion that parses and writes untrusted model files.

``lambda.py`` runs this module as a child process (``python conversion_worker.py <job> <result>``) so
the parsers run

- with none of the function's AWS credentials in its environment: the child is started with a
  scrubbed environment (no ``AWS_*`` variable, ``HOME=/tmp``), the parent closes its own ``/proc``
  files to it, and this module never creates an AWS client (the child still shares the user and the
  network namespace of the parent),
- under an address-space limit (``RLIMIT_AS``), so a model that expands past it fails with a
  ``MemoryError`` the parent reports, instead of the function being killed for memory, with a limit on
  the size of any one file it writes and with core dumps off (a limit that is asked for and cannot be
  applied fails the job), and
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
MESH_ONLY_OUTPUT_FORMATS = {'.stl', '.dae', '.3mf', '.off'}

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
            f"triangle meshes only. Convert it to .ply, .obj, .glb, .gltf or .usdc, which store points.")


def rename_materials(loaded):
    """Give every material of the loaded model the name ``material_<n>`` (one number per distinct
    material, in geometry order).

    trimesh 5 writes an OBJ export's texture images and ``newmtl`` lines under the material's own name,
    and a glTF material name is free text: one holding ``/`` or ``:`` or equal to ``..`` fails the
    export or the write-back, and any other renames the files the export has always written
    (``material_0.png``). Done in the worker so the names never reach a file name; used for OBJ
    output only, since a glTF or GLB output keeps the model's material names."""
    geometry = getattr(loaded, "geometry", None)
    parts = list(geometry.values()) if isinstance(geometry, dict) else [loaded]
    numbers = {}
    for part in parts:
        material = getattr(getattr(part, "visual", None), "material", None)
        if material is None or not hasattr(material, "name"):
            continue
        number = numbers.setdefault(id(material), len(numbers))
        try:
            material.name = f"material_{number}"
        except (AttributeError, TypeError):
            pass


# The output formats whose exporter writes a file named after a material.
MATERIAL_NAMED_FILE_FORMATS = {'.obj'}


def convert_model(job, trimesh=None):
    """Load the staged input and write the converted model to ``job["output_file"]``.

    ``job`` holds ``input_file`` (the staged input), ``input_dir`` and ``input_relative_path`` (where
    it was staged), ``by_reference`` (each reference as the model spells it, mapped to the local file
    that was fetched for it), ``output_file``, ``output_filetype`` (with its leading dot) and
    ``input_name``, and optionally ``options`` (the unit and up-axis options of the input configuration).
    ``trimesh`` is the module to use; it is imported here when not given.
    """
    if trimesh is None:
        import trimesh
    import model_formats
    import units_axes

    options = units_axes.Options.from_config(job.get("options"))
    extension = os.path.splitext(job["input_name"])[1].lower()
    handling = options.resolved_reference_handling(job["output_filetype"])
    if extension in units_axes.USD_LAYER_INPUTS and handling in ("convert-linked", "package"):
        # The layers are kept as authored: nothing is composed, so there is no model to load. They are
        # confined first, so a reference that is not a fetched file is removed rather than followed.
        import usd_io
        skipped = []
        usd_io.confine_dependencies(job["input_file"], job["input_dir"], skipped)
        if handling == "convert-linked":
            written = usd_io.convert_linked(job["input_file"], job["input_dir"], job["output_file"],
                                            job["output_filetype"], job.get("output_path_extension"))
        else:
            usd_io.package_usdz(job["input_file"], job["input_dir"], job["output_file"])
            written = [job["output_file"]]
        return {"written": len(written), "removed": [list(entry) for entry in skipped[:100]]}

    # The loader is given a resolver that serves only the fetched files, so no reference reaches
    # anything else on the local disk.
    loaded, declared_meters, declared_up = model_formats.load_model(job, trimesh)
    check_exportable(loaded, job["output_filetype"], job["input_name"])
    plan = units_axes.plan(options, job["output_filetype"], declared_meters, declared_up)
    if job["output_filetype"] in MATERIAL_NAMED_FILE_FORMATS:
        rename_materials(loaded)
    # A USD output names its files by where the write-back puts them as it is written (usd_io), so no
    # written layer is reopened.
    model_formats.export_model(loaded, plan, job)


# The scanned collectors by extension (reference_collectors.py); imported when a scan job runs.
_SCAN_COLLECTORS = {".usd": "_usd_references", ".usda": "_usd_references", ".usdc": "_usd_references",
                    ".usdz": "_usdz_references", ".dae": "_dae_references", ".fbx": "_fbx_references"}
# References kept per scanned file, as in the thumbnail container (MAX_REFERENCES_PER_FILE).
MAX_REFERENCES_PER_FILE = 100_000


def scan_references(job):
    """The references of each file in ``job["files"]`` (``[local path, extension]`` pairs), read with
    the vendored collectors: ``{local path: [[value, percent_encoded, required, kind], ...]}``, or an
    error text for a file whose references cannot be read. Run by the parent's fetch
    (``referenced_files.fetch_referenced_files`` ``scan``) so USD and XML parsing never happens in the
    process that holds the function's credentials."""
    import reference_collectors
    found = {}
    for path, extension in job["files"]:
        name = _SCAN_COLLECTORS.get(extension)
        if name is None:
            found[path] = f"no collector for {extension}"
            continue
        try:
            entries = getattr(reference_collectors, name)(path)
            found[path] = [list(entry) for entry in entries[:MAX_REFERENCES_PER_FILE]]
        except Exception as e:  # noqa: BLE001 - reported to the parent per file
            found[path] = f"{type(e).__name__}: {str(e)[:300]}"
    return found


class LimitError(RuntimeError):
    """A resource limit the job asked for could not be applied."""


def _set_limit(resource, which, value):
    """Set ``which`` to ``value`` (soft and hard); a lower hard limit already in force is kept, as it
    is the stricter one. Raises ``LimitError`` when the limit cannot be applied."""
    try:
        _, hard = resource.getrlimit(which)
        if hard != resource.RLIM_INFINITY:
            value = min(value, hard)
        resource.setrlimit(which, (value, value))
    except (ValueError, OSError) as e:
        raise LimitError(f"a resource limit could not be applied: {e}")


def apply_limits(job):
    """Apply the limits the job asks for before anything is parsed: the address space, the size of any
    one file written, and no core dump. A limit that was asked for and cannot be applied raises
    ``LimitError``, so the job never runs without it."""
    address_space = job.get("address_space_bytes")
    file_size = job.get("file_size_bytes")
    if not address_space and not file_size:
        return
    try:
        import resource
    except ImportError:
        raise LimitError("resource limits are not available on this platform")
    if address_space:
        _set_limit(resource, resource.RLIMIT_AS, int(address_space))
    if file_size:
        _set_limit(resource, resource.RLIMIT_FSIZE, int(file_size))
        # A write past the limit then fails with an error instead of ending the process with a signal.
        import signal
        signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    _set_limit(resource, resource.RLIMIT_CORE, 0)


def run_job(job):
    """What one job does: a reference scan returns its findings; a conversion returns its report."""
    if job.get("mode") == "scan":
        return {"references": scan_references(job)}
    report = convert_model(job)
    return {"report": report or {}}


# What usd-core's reader says when one array of a crate is past the LZ4 block limit.
_CRATE_UNREADABLE_MARKERS = ("TfFastCompression", "DecompressFromBuffer", "LZ4 error")


def translate_error(error, job):
    """The error the parent reports: a USD crate that cannot be decompressed becomes a ``ValueError``
    saying the file could not be read (the file is the author's to fix), where it would otherwise be a
    ``failed unexpectedly`` with the detail only in the log."""
    text = str(error)
    if not isinstance(error, ValueError) and any(marker in text for marker in _CRATE_UNREADABLE_MARKERS):
        name = job.get("input_name", "the input") if isinstance(job, dict) else "the input"
        return ValueError(
            f"{name} could not be read: a USD crate stores an array larger than the 2 GiB a crate can "
            f"decompress (one mesh with hundreds of millions of indices). Export the scene again with "
            f"its large meshes split, or in another format, and run the conversion again.")
    return error


def peak_memory_mib():
    """The most resident memory this process held, in MiB (None where the platform does not say). The
    parent records the largest of its workers in conversion-run.json."""
    try:
        import resource
    except ImportError:
        return None
    # Linux reports kibibytes; macOS (a developer's machine) reports bytes.
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)


def _write_result(path, result):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(result, handle)


def main(argv):
    job_path, result_path = argv[1], argv[2]
    with open(job_path, encoding="utf-8") as handle:
        job = json.load(handle)
    try:
        # Before anything heavy is imported, so the limits cover the parsers' own allocations.
        apply_limits(job)
        output = run_job(job)
    except MemoryError:
        _write_result(result_path, {"ok": False, "type": "MemoryError",
                                    "message": "The model needs more memory than this function has."})
        return 1
    except BaseException as e:  # noqa: BLE001 - every failure is reported to the parent
        e = translate_error(e, job)
        kind = type(e).__name__
        if kind not in USER_ERRORS:
            traceback.print_exc(file=sys.stderr)
        _write_result(result_path, {"ok": False, "type": kind, "message": str(e)})
        return 1
    _write_result(result_path, {"ok": True, **output, "peakMemoryMiB": peak_memory_mib()})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

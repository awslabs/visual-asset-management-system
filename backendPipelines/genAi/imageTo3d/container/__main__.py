#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""
VAMS Image to 3D Container

Orchestrates: load definition -> resolve settings -> ensure weights cached -> download the input
photo -> validate + remove background -> generate a mesh -> optional print-readiness cleanup ->
export -> upload.

The container handles generation and S3 I/O only. The outer workflow callback belongs to the
pipelineEnd Lambda, which is reached on both the success and the failure branch of the state
machine; a non-zero exit from a Batch RUN_JOB task is what turns a crash into a task failure.
"""

import json
import logging
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

import benchmark
import export
import generate
import meshcleanup
import preprocess
import printfeatures
import texturing
from model_manager import (
    backup_cache_to_s3,
    ensure_models_cached,
    fetch_rembg_session,
    fetch_repo,
    resolve_hf_home,
    resolve_rembg_home,
    session_weight_candidates,
)
from vams_utils.aws import s3

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

INPUT_DIR = Path("/tmp/input")  # nosec B108 - the container's own writable scratch space
WORK_DIR = Path("/tmp/work")  # nosec B108 - the container's own writable scratch space
OUTPUT_DIR = Path("/tmp/output")  # nosec B108 - the container's own writable scratch space
# Per-file metadata documents live outside OUTPUT_DIR because that whole tree uploads to the asset's FILES
# prefix, and a metadata document among an asset's files is an asset file rather than metadata.
FILE_METADATA_DIR = Path("/tmp/output_metadata")  # nosec B108 - the container's own scratch space

# The allow-list of settings a run's input configuration may set, with their defaults
CONFIG_PATH = Path(__file__).resolve().parent / "config.json"

# Input files with these extensions are a STARTING MESH for a model that refines one, not a photograph.
MESH_INPUT_EXTENSIONS = (".glb", ".gltf", ".obj", ".stl", ".ply")

GATE_REPORT_FILENAME = "imageTo3d_report.json"
# The same report in the shape the execution carries. Held as a constant because it has to appear
# in the output-upload skip list as well: neither report is an asset file, and a name that only the
# writer knew would be uploaded into the asset as a stray.
EXECUTION_REPORT_FILENAME = "imageTo3d_report.execution.json"

# The definition of the run in progress, kept where the failure path can reach it: a failure that carries
# its own diagnostics needs the results prefix to put them on, and the exception has no other way to it.
CURRENT_DEFINITION = None


def load_pipeline_definition() -> dict:
    """The pipeline definition, from ``argv[1]`` (a path or raw JSON) or PIPELINE_DEFINITION."""
    source = None
    if len(sys.argv) > 1 and sys.argv[1].strip():
        source = sys.argv[1].strip()
    elif os.environ.get("PIPELINE_DEFINITION"):
        source = os.environ["PIPELINE_DEFINITION"]
    if not source:
        raise ValueError(
            "No pipeline definition provided as an argument or in PIPELINE_DEFINITION")

    definition_path = Path(source)
    if source.endswith(".json") and definition_path.exists():
        logger.info(f"Reading the pipeline definition from {source}")
        return json.loads(definition_path.read_text(encoding="utf-8"))

    logger.info("Reading the pipeline definition from the supplied JSON")
    return json.loads(source)


def load_allowed_settings() -> dict:
    """The two-level allow-list and its defaults from ``config.json``.

    ``common`` holds the settings every run accepts; ``models`` holds one block per model key
    carrying that model's own parameters. Opened by an absolute path derived from ``__file__``: the
    entrypoint's working directory is not guaranteed to be the code directory.
    """
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return {
        "common": config.get("common") or {},
        "models": config.get("models") or {},
    }


def allowed_for_model(allowed: dict, model: str) -> dict:
    """The keys and defaults one model's run accepts: the common block plus that model's own block.

    A key belonging to a different model is absent from this map, so resolution drops it rather than
    letting one model's parameter reach an adapter that reads the same name for a different purpose.
    """
    accepted = dict(allowed.get("common") or {})
    accepted.update((allowed.get("models") or {}).get(model) or {})
    return accepted


def _is_set(value) -> bool:
    """Whether a supplied value counts as present. A blank counts as absent."""
    return value is not None and str(value).strip() != ""


def resolve_one(key: str, default, params: dict):
    """One setting's effective value and the source it came from.

    The template's configuration body is the only channel for a run's options. Asset and database
    metadata are deliberately NOT consulted: a second, invisible input channel makes a run's behaviour
    depend on state that is not visible on the execute form, and it has to agree with itself in two
    places -- the Lambda that picks the container image and the container that picks the adapter -- or a
    run silently reconstructs with one model inside another model's image.

    A blank value counts as absent: an optional enum or string field submits "" from a headless trigger,
    and "" must resolve to the template's default rather than to an empty setting. A value supplied as a
    JSON number arrives typed, so it is coerced to the text every setting reader expects.
    """
    if _is_set((params or {}).get(key)):
        return str(params[key]).strip(), "inputConfiguration"
    return str(default).strip(), "default"


def resolve_model_key(allowed: dict, params: dict, definition: dict) -> str:
    """The validated model key, resolved ahead of every other setting.

    MODEL decides which per-model keys the run accepts, so it cannot be resolved in the same pass as
    the settings it gates.
    """
    raw, source = resolve_one("MODEL", (allowed.get("common") or {}).get("MODEL", ""), params)
    logger.info(f"Setting MODEL={raw} (from {source})")
    return resolve_model({"MODEL": raw}, definition)


def resolve_settings(allowed: dict, params: dict, model: str) -> dict:
    """The effective settings for the model running: the common keys plus that model's own.

    ``allowed`` is the two-level allow-list from ``config.json``; ``model`` is the already-validated
    model key. A key belonging to another model is dropped, so a TRELLIS2_* value left in a config body
    cannot reach a TripoSR run.
    """
    params = params if isinstance(params, dict) else {}
    accepted = allowed_for_model(allowed, model)

    settings = {}
    for key, default in accepted.items():
        settings[key], source = resolve_one(key, default, params)
        logger.info(f"Setting {key}={settings[key]} (from {source})")

    # The validated key, so generation and the report agree on one model.
    settings["MODEL"] = model

    other_models = {k for other, block in (allowed.get("models") or {}).items()
                    if other != model for k in block}
    supplied = set(params)
    dropped_other_model = sorted(supplied & other_models - set(accepted))
    if dropped_other_model:
        logger.info(f"Dropped settings belonging to another model: "
                    f"{', '.join(dropped_other_model)}")
    unknown = sorted(supplied - other_models - set(accepted))
    if unknown:
        logger.info(f"Dropped settings not in config.json: {', '.join(unknown)}")
    return settings


def resolve_model(settings: dict, definition: dict) -> str:
    """The validated model key. The definition's model is the state machine's own choice.

    Validated here as well as at the vamsExecute boundary: a direct container invocation never
    passes through that boundary, and an unknown key would otherwise surface as a model-loading
    failure deep inside an adapter.
    """
    model = settings.get("MODEL", "") or definition.get("model", "")
    model = generate.normalize_model_key(model)
    if model not in generate.MODEL_ADAPTERS:
        raise ValueError(
            f"Unsupported image-to-3D model '{model}'; "
            f"expected one of {', '.join(sorted(generate.MODEL_ADAPTERS))}")
    return model


def _is_true(value) -> bool:
    return str(value or "").strip().lower() == "true"


def download_input_image(input_file: dict) -> Path:
    """Fetch one input photo into the local input directory."""
    bucket = input_file.get("bucketName", "")
    object_key = input_file.get("objectKey", "")
    if not bucket or not object_key:
        raise ValueError("The pipeline definition's inputFile needs a bucketName and an objectKey")

    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    local_path = INPUT_DIR / Path(object_key).name
    logger.info(f"Downloading the input image: s3://{bucket}/{object_key} -> {local_path}")
    if s3.download(bucket, object_key, str(local_path)) is None:
        raise RuntimeError(f"Failed to download s3://{bucket}/{object_key}")
    return local_path


def select_starting_meshes(meshes: list) -> list:
    """The meshes that count as starting points: one per stem, preferring the glTF.

    A prior pipeline step hands its whole output on -- the same model as .glb, .obj and .stl -- and a run
    that treats those as three different starting meshes refuses them. They are one model; the glTF carries
    the texture, so it is the one kept. Meshes with different stems are different models and all stay, so
    the one-mesh rule downstream still catches a genuine surplus.
    """
    preference = {".glb": 0, ".gltf": 1, ".obj": 2, ".ply": 3, ".stl": 4}
    by_stem = {}
    for path in meshes:
        path = Path(path)
        current = by_stem.get(path.stem)
        if current is None or preference.get(path.suffix.lower(), 9) < preference.get(current.suffix.lower(), 9):
            by_stem[path.stem] = path
    return [by_stem[stem] for stem in sorted(by_stem)]


def download_input_images(definition: dict) -> list:
    """Every view of the subject, primary first, as local paths.

    A model that reconstructs from several views reads all of them; every other model reads the first and
    the rest are refused by its adapter rather than ignored. The definition's ``inputFiles`` list is
    additive, so a definition written before it existed -- or a direct container invocation -- falls back
    to the single ``inputFile`` and behaves exactly as it did.

    Duplicate object keys are dropped rather than downloaded twice: two manifest entries can name the same
    file, and a view list holding the same image twice would condition the model on it twice.
    """
    entries = definition.get("inputFiles") or []
    if not entries:
        entries = [definition.get("inputFile", {}) or {}]

    # Entries are filtered for a usable location BEFORE anything is fetched, so "the definition named no
    # input file" reports itself as that rather than as a download failing on a blank key.
    usable = [entry for entry in entries
              if (entry or {}).get("bucketName") and (entry or {}).get("objectKey")]
    if not usable:
        raise ValueError(
            "The pipeline definition carried no usable input file: every entry was missing a bucketName "
            "or an objectKey.")

    paths, seen = [], set()
    for entry in usable:
        key = entry["objectKey"]
        if key in seen:
            logger.warning(f"Skipping a repeated input file: {key}")
            continue
        seen.add(key)
        paths.append(download_input_image(entry))
    if len(paths) > 1:
        logger.info(f"Downloaded {len(paths)} views: "
                    f"{', '.join(Path(path).name for path in paths)}")
    return paths


def execution_report(entries: dict) -> dict:
    """The report as DATA, for the execution to carry.

    Metadata values must be strings, so a nested part of the report -- the gate's per-criterion verdict,
    the projection's recovered pose, the per-stage timings -- travels through asset metadata JSON-encoded
    inside a string. The execution has no such constraint, and a reader there wants the gate report as an
    object rather than as an escaped string inside one. The ``IMAGE_TO_3D_`` prefix goes too: it exists to
    namespace keys sharing an asset's metadata with everything else, and a report of this run's own is
    not sharing with anything.
    """
    report = {}
    for key, value in entries.items():
        name = key[len("IMAGE_TO_3D_"):].lower() if key.startswith("IMAGE_TO_3D_") else key
        if isinstance(value, str) and value[:1] in "{[":
            try:
                report[name] = json.loads(value)
                continue
            except ValueError:
                pass
        report[name] = value
    return report


def write_gate_report(report_path: Path, definition: dict, model: str, settings: dict,
                      image_info: dict, gate_report, notes, elapsed: float,
                      texture_status: str, benchmark_report=None, feature_report=None,
                      projection_report=None) -> dict:
    """The generation and gate report, written locally and returned as its entry map.

    ``IMAGE_TO_3D_TEXTURE_STATUS`` is what makes a grey result legible without downloading the mesh:
    it names which of the texture paths in ``texturing`` the run took. ``benchmark_report`` is
    present only when the template asked for one, and carries the per-stage timings and host facts a
    model-to-model comparison is read from.
    """
    entries = {
        "IMAGE_TO_3D_MODEL": model,
        "IMAGE_TO_3D_JOB_NAME": definition.get("jobName", ""),
        "IMAGE_TO_3D_BACKGROUND_REMOVAL": settings.get("BACKGROUND_REMOVAL", ""),
        "IMAGE_TO_3D_MESH_CLEANUP": settings.get("MESH_CLEANUP", ""),
        "IMAGE_TO_3D_PRINT_GATE_POLICY": settings.get("PRINT_GATE_POLICY", ""),
        "IMAGE_TO_3D_TARGET_FACE_COUNT": settings.get("TARGET_FACE_COUNT", ""),
        "IMAGE_TO_3D_TARGET_SIZE_MM": settings.get("TARGET_SIZE_MM", ""),
        "IMAGE_TO_3D_EXPORT_FORMATS": settings.get("EXPORT_FORMATS", ""),
        "IMAGE_TO_3D_TEXTURE_STATUS": texture_status,
        "IMAGE_TO_3D_SOURCE_IMAGE": json.dumps(image_info),
        "IMAGE_TO_3D_GENERATION_SECONDS": f"{elapsed:.1f}",
    }
    if notes:
        entries["IMAGE_TO_3D_GENERATION_NOTES"] = json.dumps(notes)
    if gate_report is not None:
        entries["IMAGE_TO_3D_PRINT_READY"] = "true" if gate_report.passed else "false"
        entries["IMAGE_TO_3D_PRINT_GATE_REPORT"] = json.dumps(gate_report.as_dict())
    if feature_report:
        # The join mode and body count belong beside the gate verdict: with a texture-preserving join
        # the features stay separate overlapping shells, which every gate criterion accepts.
        entries["IMAGE_TO_3D_PRINT_FEATURES"] = json.dumps(feature_report)
    if projection_report:
        # Which pose the colour was taken from, and how much of the surface the photograph actually
        # saw. A report that only said "coloured" would not distinguish a measured front from a back
        # inferred entirely from it.
        entries["IMAGE_TO_3D_PROJECTED_COLOUR"] = json.dumps(projection_report)
    if benchmark_report is not None:
        entries["IMAGE_TO_3D_BENCHMARK"] = benchmark.as_metadata_value(benchmark_report)

    document = export.build_metadata_document(entries)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    logger.info(f"Wrote the generation report: {report_path}")

    # The same content in the shape the EXECUTION wants, written beside it. Which of the two is uploaded
    # depends on whether the run has a results prefix; see upload_gate_report.
    execution_path = report_path.with_name(EXECUTION_REPORT_FILENAME)
    execution_path.write_text(json.dumps(execution_report(entries), indent=2), encoding="utf-8")
    logger.info(f"Wrote the run report for the execution: {execution_path}")
    return document


def file_metadata_entries(filename: str, model: str, job_name: str, source_image: str, notes,
                          gate_report, texture_status: str, elapsed: float, benchmark_report=None,
                          projection_report=None) -> dict:
    """What each exported mesh carries as its OWN metadata, so the file identifies itself.

    The run report is asset-level, which means it describes whichever run wrote it last and says nothing
    about any single file. A mesh sitting in an asset beside others from other models is then only
    identifiable by opening the execution that produced it. These entries travel with the file instead:
    the model that made it, whether it carries colour, and the measurements of the run.

    Deliberately flat rather than one JSON blob, so the values are readable in the asset's metadata view
    and usable in a search. The full benchmark document is included as well, for anything the flat fields
    leave out.
    """
    # Named `measurements` rather than `benchmark`, which is the module this file imports.
    measurements = benchmark_report or {}
    host = measurements.get("host") or {}
    stages = measurements.get("stageSeconds") or {}
    shape = measurements.get("shape") or {}

    entries = {
        "IMAGE_TO_3D_MODEL": model,
        "IMAGE_TO_3D_MODEL_NAME": (notes or {}).get("modelName", ""),
        "IMAGE_TO_3D_MODEL_LICENSE": (notes or {}).get("modelLicense", ""),
        "IMAGE_TO_3D_TEXTURE_STATUS": texture_status,
        # The status above describes the MESH; this describes the file, and they differ for an STL.
        "IMAGE_TO_3D_COLOUR_IN_FILE": (
            "true" if export.carries_colour_in_file(filename)
            and texture_status != texturing.TEXTURE_NONE else "false"),
        # Which photograph this mesh came from, and which run made it. Both are how a file is traced
        # back without opening the execution, which is the point of these entries.
        "IMAGE_TO_3D_SOURCE_IMAGE_NAME": source_image,
        "IMAGE_TO_3D_JOB_NAME": job_name,
        "IMAGE_TO_3D_TOTAL_SECONDS": f"{elapsed:.1f}",
    }
    if gate_report is not None:
        entries["IMAGE_TO_3D_PRINT_READY"] = "true" if gate_report.passed else "false"
        entries["IMAGE_TO_3D_FACE_COUNT"] = str(measurements.get("faceCount", ""))
        entries["IMAGE_TO_3D_BODIES"] = f"{gate_report.bodies}/{gate_report.expected_bodies}"
    if projection_report:
        entries["IMAGE_TO_3D_COLOUR_SOURCE"] = "photograph"
        entries["IMAGE_TO_3D_COLOUR_MEASURED_FRACTION"] = str(
            projection_report.get("verticesMeasured", ""))
    if measurements:
        # The measurements a later comparison is read from, flat: which hardware, and where the time went.
        entries["IMAGE_TO_3D_INSTANCE_TYPE"] = host.get("instanceType", "")
        entries["IMAGE_TO_3D_GPU"] = host.get("gpuName", "")
        for stage in ("generate", "preprocess", "cleanup", "projectColour"):
            if stage in stages:
                entries[f"IMAGE_TO_3D_SECONDS_{stage.upper()}"] = str(stages[stage])
        if "fillRatio" in shape:
            entries["IMAGE_TO_3D_FILL_RATIO"] = str(shape["fillRatio"])
        # And the whole document, for anything the flat fields above leave out.
        entries["IMAGE_TO_3D_BENCHMARK"] = benchmark.as_metadata_value(benchmark_report)
    return {k: v for k, v in entries.items() if v != ""}


def write_file_reports(output_dir: Path, mesh_files, entries_for) -> list:
    """One metadata document per exported mesh, written where the files upload will NOT sweep them up.

    They go to the metadata prefix, not the files prefix, so they are written to their own directory:
    a ``*.metadata.json`` landing among an asset's files would be an asset file rather than metadata.
    """
    written = []
    for path in mesh_files:
        document = export.build_metadata_document(entries_for(path.name))
        target = output_dir / f"{path.name}{export.METADATA_SUFFIX}"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(document, indent=2), encoding="utf-8")
        written.append(target)
        logger.info(f"Wrote file metadata for {path.name}: {target.name}")
    return written


def upload_file_reports(reports, output_metadata: dict, relative_subdir: str) -> None:
    """Upload each per-file metadata document to the metadata prefix under the name of its file.

    The write-back fails when a document names a file the output does not carry, so these are written
    only for files the export actually produced.
    """
    bucket = (output_metadata or {}).get("bucketName", "")
    metadata_dir = (output_metadata or {}).get("objectDir", "")
    if not bucket or not metadata_dir:
        logger.warning("No output metadata prefix in the definition; the per-file metadata is not "
                       "uploaded")
        return

    for report in reports:
        filename = report.name[: -len(export.METADATA_SUFFIX)]
        object_key = export.file_metadata_key(metadata_dir, relative_subdir, filename)
        if s3.upload(bucket, object_key, str(report)) is None:
            raise RuntimeError(f"Failed to upload file metadata to s3://{bucket}/{object_key}")
        logger.info(f"Uploaded file metadata: s3://{bucket}/{object_key}")


DIAGNOSTIC_TEXT_SUFFIXES = (".json", ".md", ".txt", ".py", ".log")
DIAGNOSTIC_RESULTS_MAX_BYTES = 64 * 1024


def upload_diagnostics(paths, output_results: dict, temporary_files: dict = None, job_name: str = "") -> None:
    """Files that record HOW a mesh was made, uploaded so the execution shows them.

    An agent's renders, notes and transcript belong to the RUN, like the report: they describe one
    attempt, not the asset. But the workflow's output step READS every object under the results prefix as
    UTF-8 text and records the content on the execution, so only small text may go there: a PNG fails
    the decode, and a transcript over the DynamoDB item limit fails the write, and either marks a run
    that produced a passing model FAILED -- which is how the first two finished agent runs were reported.
    Small text files go beside the run report; renders, the event stream and anything over 64 KB go to
    the auxiliary prefix, and a small index of where they went goes with the text. A direct invocation
    with no workflow context keeps everything with its other output.
    """
    if not paths:
        return
    results_bucket = (output_results or {}).get("bucketName", "")
    results_dir = (output_results or {}).get("objectDir", "")
    if not (results_bucket and results_dir):
        logger.info(f"No results prefix; {len(paths)} diagnostic file(s) stay with the run's output")
        return
    aux_bucket = (temporary_files or {}).get("bucketName", "")
    aux_dir = (temporary_files or {}).get("objectDir", "")
    aux_prefix = f"{aux_dir}imageTo3d/agent/{job_name or 'run'}/"
    text_count, bulky = 0, []
    for path in [Path(p) for p in paths]:
        if not path.is_file():
            continue
        size = path.stat().st_size
        if path.suffix.lower() in DIAGNOSTIC_TEXT_SUFFIXES and size <= DIAGNOSTIC_RESULTS_MAX_BYTES:
            if s3.upload(results_bucket, f"{results_dir}agent/{path.name}", str(path)) is None:
                logger.warning(f"Could not upload the diagnostic {path.name}")
            else:
                text_count += 1
        elif aux_bucket and aux_dir:
            if s3.upload(aux_bucket, f"{aux_prefix}{path.name}", str(path)) is None:
                logger.warning(f"Could not upload the diagnostic {path.name}")
            else:
                bulky.append({"name": path.name, "bytes": size, "s3": f"s3://{aux_bucket}/{aux_prefix}{path.name}"})
        else:
            logger.info(f"No auxiliary prefix; the diagnostic {path.name} ({size} bytes) stays with the run's output")
    if bulky:
        index_path = Path(paths[0]).parent / "bulky_diagnostics.json"
        index_path.write_text(json.dumps({"note": "Renders and the full event stream, kept off the results "
                                                  "prefix because it is read as text onto the execution.",
                                          "files": bulky}, indent=2), encoding="utf-8")
        if s3.upload(results_bucket, f"{results_dir}agent/{index_path.name}", str(index_path)) is not None:
            text_count += 1
    logger.info(f"Uploaded {text_count} diagnostic text file(s) to s3://{results_bucket}/{results_dir}agent/"
                f" and {len(bulky)} bulky file(s) to s3://{aux_bucket}/{aux_prefix}")


def upload_gate_report(report_path: Path, output_results: dict, output_metadata: dict,
                       relative_subdir: str) -> None:
    """Upload the run's report where it belongs: to the EXECUTION, not into the asset.

    The report describes a RUN -- which model produced the mesh, what the print gate measured, what the
    projection recovered, how long each stage took. The workflow's output processing lists the results
    prefix, reads each object's content and records it against the execution, so a report written there
    is readable on the run that produced it and is kept per run.

    Asset metadata is the wrong home for it, which is why this no longer writes there when a results
    prefix exists: asset-level metadata is SINGULAR, so an asset carrying output from several models
    keeps only the last run's report, and every earlier one is silently replaced. Per-FILE metadata is
    unaffected -- that is what identifies the model on each mesh, and it stays.

    Falls back to the metadata prefix when no results prefix is supplied, which is a direct invocation
    with no workflow context.
    """
    results_bucket = (output_results or {}).get("bucketName", "")
    results_dir = (output_results or {}).get("objectDir", "")
    if results_bucket and results_dir:
        object_key = f"{results_dir}{GATE_REPORT_FILENAME}"
        # The plain form, not the metadata write-back envelope: the execution reads an object's content
        # verbatim, so shipping it a document addressed to the metadata writer would put that writer's
        # own fields (updateType, type) on the run report and leave every nested part escaped.
        execution_path = report_path.with_name(EXECUTION_REPORT_FILENAME)
        upload_path = execution_path if execution_path.exists() else report_path
        if s3.upload(results_bucket, object_key, str(upload_path)) is None:
            raise RuntimeError(
                f"Failed to upload the run report to s3://{results_bucket}/{object_key}")
        logger.info(f"Uploaded the run report for the execution: "
                    f"s3://{results_bucket}/{object_key}")
        return

    bucket = (output_metadata or {}).get("bucketName", "")
    metadata_dir = (output_metadata or {}).get("objectDir", "")
    if not bucket or not metadata_dir:
        logger.warning("No results or metadata prefix in the definition; the run report is not "
                       "uploaded")
        return

    object_key = export.metadata_report_key(metadata_dir, relative_subdir)
    if s3.upload(bucket, object_key, str(report_path)) is None:
        raise RuntimeError(f"Failed to upload the generation report to s3://{bucket}/{object_key}")
    logger.info(f"Uploaded the generation report: s3://{bucket}/{object_key}")


def postprocess(mesh, settings, run_cleanup=True, texture_paths=(), output_dir="", timer=None,
                photo_path="", input_view_azimuth=None):
    """Everything between a generated mesh and an exportable one, in the order the pipeline runs it.

    Returns ``(mesh, gate_report, texture_status, feature_report, projection_report)``.

    Extracted so it can be replayed OFFLINE against a stored mesh, which is what
    ``benchmark/replay.py`` does. Every stage in here is CPU work on geometry -- decimation, the repair
    ladder, the print gate, texture carry-over, print features -- and almost every change made to this
    pipeline has been to one of them, yet verifying one cost a container rebuild and a GPU job. The
    order matters and is easy to get wrong from memory, so there is one definition of it rather than a
    reproduction that drifts:

    * the texture is captured BEFORE decimation, against the topology its uv field was measured over;
    * decimation runs before the repair ladder, and welds uv seams first;
    * the texture is re-attached before the rescale, so re-projection matches positions in one space;
    * colour projected from the photograph goes on once the topology is final, and only onto a mesh
      that has none of its own;
    * the rescale precedes the print features, whose dimensions are millimetres of the printed object;
    * the gate is re-measured after the features, which add shells on purpose;
    * per-vertex colour stays PER VERTEX, which glTF carries natively and a renderer interpolates
      correctly; the atlas an OBJ needs is baked at export, once no further geometry can be added.

    ``timer`` is optional: the container passes its own so the stages are costed, and a replay omits it.
    ``photo_path`` is the image the model reconstructed from, needed only for the projection, and
    ``input_view_azimuth`` is the azimuth that model's output frame puts that photograph at, or None
    where it has not been calibrated.
    """
    from contextlib import nullcontext

    def stage(name):
        return timer.stage(name) if timer is not None else nullcontext()

    gate_report = None
    if run_cleanup:
        with stage("cleanup"):
            # Decimation, the repair ladder and the reconstruction rungs all hand back geometry
            # alone, so the texture is captured against the pre-cleanup topology and re-attached
            # once the cleanup has settled.
            snapshot = texturing.capture_texture(mesh)
            mesh = export.decimate(mesh, settings.get("TARGET_FACE_COUNT", 0))
            mesh, gate_report = meshcleanup.run_cleanup(mesh, settings)
            # The texture goes back on ahead of the rescale: re-projection matches the repaired
            # vertices against the captured positions, so both have to be in the same space.
            # Scaling carries a texture visual through as it is.
            mesh, texture_status = texturing.restore_texture(
                mesh, snapshot, texture_paths, output_dir)
    else:
        logger.info("Mesh cleanup disabled; the mesh is exported exactly as generated")
        texture_status = texturing.generated_texture_status(mesh)

    # A preserved atlas ships exactly as the model baked it, and what it needs depends on how the model
    # laid it out. A model whose atlas is contiguous keeps it -- an incomplete one only needs its
    # unwritten black texels flooded from the nearest written colour, the fix the re-bake path applies.
    # A model whose atlas is densely chart-packed tears when a renderer samples across its island edges,
    # so its atlas is collapsed to per-vertex colour instead, which glTF carries and a renderer
    # interpolates over the continuous surface correctly (see ModelAdapter.ATLAS_IS_CHART_PACKED); the
    # flood remains its fallback when there is no atlas to collapse. A mesh with no atlas of its own is
    # untouched by either path. This covers both preserved paths -- the cleanup-off passthrough and a
    # cleanup that left the model's own texture visual in place.
    if texture_status == texturing.TEXTURE_PRESERVED:
        adapter = generate.MODEL_ADAPTERS.get(settings.get("MODEL"))
        if getattr(adapter, "ATLAS_IS_CHART_PACKED", False):
            with stage("collapseAtlas"):
                collapsed = texturing.collapse_atlas_to_vertex_colors(mesh)
                if collapsed is not None:
                    mesh, texture_status = collapsed
                else:
                    mesh = texturing.flood_preserved_atlas(mesh)
        else:
            with stage("floodAtlas"):
                mesh = texturing.flood_preserved_atlas(mesh)

    # A model that produces geometry alone can still be coloured from the photograph it reconstructed
    # from. Only a mesh with no colour at all is touched, so this can never overwrite a model's own
    # appearance; it runs once the topology is final, so the sampled colours belong to the vertices that
    # ship rather than to a set a later repair rung would have replaced.
    projection_report = None
    projection_mode = str(settings.get("PROJECT_PHOTO_COLOUR", "off") or "off").strip().lower()
    if projection_mode != "off" and photo_path and texture_status == texturing.TEXTURE_NONE:
        with stage("projectColour"):
            mesh, projection_report = texturing.project_photograph(
                mesh, photo_path, settings, declared_azimuth=input_view_azimuth)
        if projection_report is not None:
            texture_status = texturing.TEXTURE_PROJECTED

    # The rescale sits outside the cleanup block: TARGET_SIZE_MM is the physical size of the printed
    # object, which is just as meaningful for a mesh exported as generated, and the print features
    # below are dimensioned in millimetres and so depend on it having happened.
    mesh = export.rescale(mesh, settings.get("TARGET_SIZE_MM", 0))

    # Print features come after the rescale so their millimetre dimensions mean millimetres of the
    # printed object, and before the gate outcome is resolved so the verdict describes what ships.
    with stage("printFeatures"):
        mesh, feature_report = printfeatures.apply_features(mesh, settings)
        if feature_report and gate_report is not None:
            # The features add shells on purpose, so the re-measure is told how many to expect.
            gate_report = meshcleanup.regate(
                gate_report, mesh,
                expected_bodies=int(feature_report.get("printFeatureExpectedBodies", 1)))

    # Per-vertex colour stays per vertex. glTF carries a colour array natively and exactly, and a
    # renderer interpolates it across a triangle correctly, which is the whole point of it; converting
    # it to a texture is what the OBJ writer asks for, and only it. See texturing.bake_vertex_colours
    # for what a palette does to a rendered surface.
    return mesh, gate_report, texture_status, feature_report, projection_report


def _segmentation_backend(settings) -> str:
    """The background-removal backend this run will actually use, resolved early for the weight cache.

    IMAGE_CLEANUP=skip turns segmentation off, and it does so further down, once the input is in hand. The
    weight cache runs before that, so asking for the raw setting there would fetch a backend's weights for
    a run that is about to bypass it -- 2.4 GB for SAM, on a GPU instance.
    """
    if str(settings.get("IMAGE_CLEANUP", "auto") or "auto").strip().lower() == "skip":
        return "none"
    return str(settings.get("BACKGROUND_REMOVAL", "rembg"))


def run() -> None:
    """The pipeline body. Every failure path exits non-zero through ``main``."""
    start_time = time.time()
    timer = benchmark.StageTimer()

    definition = load_pipeline_definition()
    global CURRENT_DEFINITION
    CURRENT_DEFINITION = definition
    logger.info(f"Pipeline definition: {json.dumps(definition)}")
    # Asset buckets outside the deployment Region, registered before the first S3 call.
    s3.register_bucket_regions(definition.get("bucketRegions"))

    asset_id = definition.get("assetId", "")
    input_file = definition.get("inputFile", {}) or {}
    output_files = definition.get("outputFiles", {}) or {}
    output_metadata = definition.get("outputMetadata", {}) or {}
    # Where a report meant for the EXECUTION goes, rather than into the asset. Empty on a direct
    # invocation with no workflow context, in which case the report falls back to asset metadata.
    output_results = definition.get("outputResults", {}) or {}

    # The metadata + input configuration S3 LOCATIONS travel in the definition; the files are read
    # here. fetch_* return {} for an absent file and for an unreadable one alike, so log which.
    from vams_utils import manifest_io

    input_configuration_location = definition.get("inputConfigurationS3Location", "")

    params = manifest_io.fetch_input_configuration(input_configuration_location)

    logger.info(f"Input configuration from '{input_configuration_location}': "
                f"{'empty or unreadable' if not params else str(params)}")

    database_id = definition.get("databaseId", "")


    # MODEL is resolved first: it decides which per-model keys the rest of the resolution accepts.
    allowed = load_allowed_settings()
    model = resolve_model_key(allowed, params, definition)
    settings = resolve_settings(allowed, params, model)
    formats = export.parse_export_formats(settings.get("EXPORT_FORMATS", ""))
    run_cleanup = _is_true(settings.get("MESH_CLEANUP", "true"))
    gate_policy = settings.get("PRINT_GATE_POLICY", "") or "failClosed"

    # Output keys are composed before any GPU work: a definition that cannot address its own output
    # must fail immediately rather than after a generation run.
    relative_subdir = export.compute_relative_subdir(input_file.get("objectKey", ""), asset_id)
    logger.info(f"Relative subdirectory within asset {asset_id}: '{relative_subdir}'")

    hf_home = resolve_hf_home(model)
    os.environ["HF_HOME"] = hf_home
    # rembg reads its weights from U2NET_HOME, which the image points at a baked copy of the default
    # session's file. Moved under the model's cache directory, the S3 backup and restore carry them with
    # the model's own weights, and the baked copy seeds the default session so it never downloads.
    image_rembg_home = os.environ.get("U2NET_HOME")
    rembg_home = resolve_rembg_home(hf_home)
    os.environ["U2NET_HOME"] = rembg_home
    segmentation = _segmentation_backend(settings)
    session = preprocess.backend_session(segmentation)
    segmentation_weights = (session_weight_candidates(*session, rembg_home),) if session else ()
    # Timed separately from generation: a cold cache downloads weights over the network, and folding
    # that into the model's own duration makes the first run of any model look like a slow model.
    with timer.stage("weightCache"):
        ensure_models_cached(
            hf_home=hf_home,
            s3_bucket=os.environ.get("S3_MODEL_BUCKET", ""),
            s3_prefix=os.environ.get("S3_MODEL_PREFIX", ""),
            invalidate=_is_true(settings.get("INVALIDATE_MODEL_CACHE", "false")),
            # The repos this model declares, so the check asks whether ITS weights are cached rather
            # than whether the cache holds anything -- and so it can answer from a handful of directory
            # reads instead of a walk of the whole tree. The selected background-removal backend's own
            # weights join them, so a cache holding the model's but not the backend's reports a miss and
            # restores rather than reporting a hit and downloading during preprocessing.
            repos=(tuple(getattr(generate.MODEL_ADAPTERS.get(model), "HF_REPOS", ()))
                   + preprocess.backend_repos(segmentation)),
            required_files=segmentation_weights,
        )

        # ensure_models_cached restores from S3; it does not fetch from HuggingFace. The reconstruction
        # model's own repositories are fetched by its adapter before generation, but a segmentation
        # backend's are not -- transformers downloads those lazily from inside preprocessing, straight
        # onto the shared filesystem, and that write is what `OSError: [Errno 116] Stale file handle`
        # has ended runs on. Fetching them here puts the download under fetch_repo's retry and inside
        # this stage's own timing, so a cold backend costs a slow stage rather than a failed job.
        for repo in preprocess.backend_repos(segmentation):
            logger.info(f"Ensuring segmentation weights for {repo}")
            fetch_repo(repo, cache_dir=os.path.join(hf_home, "hub"),
                       token=os.environ.get("HF_TOKEN") or None)
        # A rembg session's weights likewise: fetched here, under the same retry, into the directory the
        # backup carries, rather than by rembg from inside preprocessing on every run.
        if session:
            fetch_rembg_session(*session, home=rembg_home, seed_from=image_rembg_home)

    with timer.stage("inputDownload"):
        input_files = download_input_images(definition)
        # A run's input files are photographs, plus -- for the one model that can refine an existing
        # model -- at most one mesh. They are told apart by extension here, once, so the image pipeline
        # below never sees a mesh and the model receives each kind by name.
        input_images = [p for p in input_files if p.suffix.lower() not in MESH_INPUT_EXTENSIONS
                        and not Path(p).name.lower().startswith("material")]
        input_meshes = select_starting_meshes([p for p in input_files if p.suffix.lower() in MESH_INPUT_EXTENSIONS])
        if len(input_meshes) > 1:
            raise ValueError(
                f"At most one mesh may be supplied as a starting point; got "
                f"{', '.join(m.name for m in input_meshes)}")
        starting_mesh = str(input_meshes[0]) if input_meshes else None
        # The primary view. Asset identity, the relative output subdirectory and the reported source-image
        # information all come from this one, whether or not other views were supplied. A mesh-only run
        # has no image; the model decides whether that is acceptable.
        input_image = input_images[0] if input_images else None
        if input_image is None:
            logger.info(f"No reference image supplied; the run starts from {starting_mesh}")
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # An input that is already a clean cutout needs no cleanup, and skipping it removes a stage's
    # latency and cost rather than merely doing no harm. The switch resolves to the same "no
    # segmentation" value the backend list already carries, so there is one code path to reason about
    # instead of two overlapping controls: the framing stage keys off the same value, because without a
    # mask it has no way to locate the subject.
    cleanup_mode = str(settings.get("IMAGE_CLEANUP", "auto") or "auto").strip().lower()
    if cleanup_mode == "skip":
        logger.info(
            "IMAGE_CLEANUP=skip: the input is taken as already segmented, so background removal and "
            "framing are both bypassed and the image reaches the model exactly as supplied")
        settings["BACKGROUND_REMOVAL"] = "none"

    with timer.stage("preprocess"):
        if input_image is not None:
            prepared_image, image_info = preprocess.prepare_input_image(
                str(input_image), str(WORK_DIR), settings.get("BACKGROUND_REMOVAL", "rembg"),
                # A backend with options of its own reads them from the run's settings; SAM takes its
                # prompt from there.
                settings)
        else:
            prepared_image, image_info = None, {"source": "none", "startingMesh": Path(starting_mesh).name}
        # Each further view goes through the SAME preprocessing, because a model conditioned on several
        # views needs them segmented consistently -- one cut-out view beside one uncut view describes two
        # different subjects. Names differ per view, so the outputs cannot collide in one directory.
        prepared_views = [prepared_image] if prepared_image else []
        for extra in input_images[1:]:
            prepared_extra, _ = preprocess.prepare_input_image(
                str(extra), str(WORK_DIR), settings.get("BACKGROUND_REMOVAL", "rembg"), settings)
            prepared_views.append(prepared_extra)

    with timer.stage("generate"):
        result = generate.generate(prepared_image, str(WORK_DIR), settings,
                                   extra_images=prepared_views[1:],
                                   # The photographs as supplied, for a model that wants the context the
                                   # cutout removed; and the mesh to refine, for the one that can.
                                   source_images=[str(p) for p in input_images],
                                   starting_mesh=starting_mesh)
    logger.info(f"Generation produced {result.mesh_path}")

    import trimesh

    with timer.stage("meshLoad"):
        mesh = trimesh.load(result.mesh_path, force="mesh")
        # Models disagree on which axis is up, so the mesh is brought into one canonical space before
        # anything measures or exports it. Everything after this point is z-up.
        mesh = export.normalize_up_axis(mesh, result.up_axis)
        # A model that baked its own atlas may have written the image as a loose file with nothing
        # linking it to the mesh. Binding it here, before the texture is captured, is what puts it on
        # the path the cleanup carries across and the export embeds.
        mesh = texturing.attach_generated_texture(mesh, result.texture_paths)
    mesh, gate_report, texture_status, feature_report, projection_report = postprocess(
        mesh, settings, run_cleanup=run_cleanup, texture_paths=result.texture_paths,
        output_dir=str(OUTPUT_DIR), timer=timer,
        # The prepared image rather than the original download: the background removal stage has already
        # produced the subject mask the projection needs, as its alpha channel.
        photo_path=str(prepared_image),
        # The azimuth this model's output frame puts the input photograph at. A figure's outline cannot
        # determine it and the search can prefer a wrong answer there, so the model's own calibration
        # takes precedence over a free recovery.
        input_view_azimuth=result.input_view_azimuth)

    if run_cleanup:
        write_outputs, gate_error = meshcleanup.resolve_gate_outcome(gate_report, gate_policy)
    else:
        write_outputs, gate_error = True, None

    stem = Path(input_file.get("objectKey", "mesh")).stem

    if write_outputs:
        with timer.stage("export"):
            # Each format's exporter emits whatever the mesh's visual carries: the GLB embeds the
            # texture or the colour array, the OBJ gets its MTL and maps written alongside, the STL
            # carries no colour by design. The model's raw texture files are copied in only on the
            # degraded path, where the visual could not carry them. A mesh coloured per vertex hands
            # the OBJ writer a baked atlas, since an OBJ carries a colour array only through an
            # extension many readers ignore.
            exported = export.write_formats(
                mesh, str(OUTPUT_DIR), stem, formats,
                textured_variant=lambda: texturing.bake_vertex_colours(mesh))
    else:
        logger.error("The print gate failed under failClosed; no mesh output is written")
        exported = []

    # The report is written after the exports so the benchmark can measure what was produced, and
    # before the upload so it travels with them in the same walk.
    elapsed = time.time() - start_time
    benchmark_report = None
    if benchmark.is_enabled(settings.get("BENCHMARK_REPORT", "false")):
        benchmark_report = benchmark.build_report(
            timer, model, settings, image_info,
            output_paths=sorted(OUTPUT_DIR.glob("*")),
            extra={"totalRunSeconds": round(elapsed, 2),
                   "textureStatus": texture_status,
                   "printReady": None if gate_report is None else gate_report.passed,
                   # The comparison reads these to flag a result whose metrics look right: a subject in
                   # more pieces than expected, and a repair that kept the gate happy by removing volume.
                   "bodies": None if gate_report is None else gate_report.bodies,
                   "expectedBodies": None if gate_report is None else gate_report.expected_bodies,
                   "volumeRetained": None if gate_report is None else gate_report.volume_retained,
                   "piecesFused": None if gate_report is None else gate_report.pieces_fused,
                   "faceCount": len(mesh.faces),
                   # Coarse shape measurements of what ships. They mean little in isolation and a lot
                   # compared across models given the same photograph, which is what the comparison does.
                   "shape": export.shape_descriptors(mesh),
                   "outputsWritten": write_outputs,
                   "printFeatures": feature_report or {},
                   "projectedColour": projection_report or {}},
        )
        logger.info(f"Benchmark report: {benchmark.as_metadata_value(benchmark_report)}")

    report_path = OUTPUT_DIR / GATE_REPORT_FILENAME
    write_gate_report(report_path, definition, model, settings, image_info,
                      gate_report, result.notes, elapsed, texture_status, benchmark_report,
                      feature_report, projection_report)

    # And one document per exported mesh, so each FILE names the model that produced it and carries the
    # run's measurements. The asset-level report above describes whichever run wrote it last, which is no
    # help when an asset holds meshes from several models. Written under their own directory: a
    # *.metadata.json among the asset's files would be an asset file rather than metadata.
    file_reports = write_file_reports(
        FILE_METADATA_DIR, [Path(path) for path in exported],
        lambda filename: file_metadata_entries(
            filename, model, definition.get("jobName", ""), Path(input_file.get("objectKey", "")).name,
            result.notes, gate_report, texture_status, elapsed, benchmark_report, projection_report))

    if write_outputs:
        # A later pipeline in the same workflow is handed only this run's OUTPUTS, so a workflow that
        # reconstructs and then refines needs the photograph carried across beside the mesh.
        if str(settings.get("PASS_THROUGH_INPUTS", "false")).strip().lower() == "true":
            for source in input_images:
                shutil.copy2(source, OUTPUT_DIR / Path(source).name)
            logger.info(f"Passed {len(input_images)} input image(s) through to the outputs for the next pipeline")
        # The report goes to the metadata prefix under its own name, so it is filtered out of the
        # files walk rather than uploaded twice.
        with timer.stage("upload"):
            export.upload_output_dir(
                s3.upload, str(OUTPUT_DIR), output_files.get("bucketName", ""),
                output_files.get("objectDir", ""), relative_subdir,
                skip_names=(GATE_REPORT_FILENAME, EXECUTION_REPORT_FILENAME))

    upload_gate_report(report_path, output_results, output_metadata, relative_subdir)
    upload_diagnostics(getattr(result, "diagnostics", None) or [], output_results,
                       definition.get("temporaryFiles", {}) or {}, definition.get("jobName", ""))
    upload_file_reports(file_reports, output_metadata, relative_subdir)

    try:
        backup_cache_to_s3(hf_home=hf_home,
                           s3_bucket=os.environ.get("S3_MODEL_BUCKET", ""),
                           s3_prefix=os.environ.get("S3_MODEL_PREFIX", ""),
                           required_files=segmentation_weights)
    except Exception as backup_error:
        logger.warning(f"Backing the model cache up to S3 failed (non-fatal): {backup_error}")

    if gate_error is not None:
        raise gate_error

    logger.info(f"Pipeline completed in {time.time() - start_time:.1f}s")


def main() -> None:
    try:
        run()
    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        logger.error(traceback.format_exc())
        # A failure that carries its own evidence -- an agent's event stream and notes -- puts that
        # evidence on the execution, where the run report would have gone. Without this the files that
        # explain the failure die with the container and the log holds one line.
        diagnostics = getattr(e, "diagnostics", None)
        if diagnostics and CURRENT_DEFINITION is not None:
            try:
                upload_diagnostics(diagnostics, CURRENT_DEFINITION.get("outputResults", {}) or {},
                                   CURRENT_DEFINITION.get("temporaryFiles", {}) or {},
                                   CURRENT_DEFINITION.get("jobName", ""))
            except Exception as upload_error:  # nosec B110 - the original failure is what matters
                logger.warning(f"Could not upload the failure diagnostics: {upload_error}")
        sys.stdout.flush()
        sys.stderr.flush()
        sys.exit(1)


if __name__ == "__main__":
    main()

# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Lambda handler for the BLENDER render branch of the SYSTEM - GenAI Metadata pipeline.

Downloads the primary file (at its S3 version) and, when the template asks for it, the sibling files a
mesh or USD loader references, runs Blender once under a timeout derived from the remaining invocation
time, uploads the rendered views to `{auxTempPrefix}renders/`, computes trimesh attributes, records the
unit the file declares, writes the execution's analysis manifest, and returns the pipeline state extended
with this branch's keys. Render faults never fail the invocation: they leave `renderSkipped: "error"` in the
manifest and on the state and the run continues attributes-only.
"""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from containerLogger import safeLogger
from formatUnits import declared_units
from meshAttributes import apply_declared_units, compute_trimesh_attributes, merge_scene_facts

# Adaptive retry with client-side rate limiting. A pipeline lambda runs against throttling-prone services
# for the length of a job, so a bare client sits on botocore's default mode with no rate limiting and a
# sustained burst surfaces as a throttling error on the caller.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = safeLogger(service="systemGenAiMetadataBlenderRender")

s3_client = boto3.client('s3', config=retry_config)

RENDER_BRANCH = "BLENDER"
DEFAULT_FILE_CLASS = "mesh"
RENDERS_PREFIX = "renders/"

# Shared with renderScene.py, which cannot be imported here (it imports bpy); the test suite pins equality.
RENDER_FILE_PREFIX = "render_"
SCENE_FACTS_FILENAME = "scene_facts.json"
VIEW_ORDER = ("perspective_front", "front", "right", "top", "back", "left", "bottom", "perspective_back")

DEFAULT_RENDER_VIEWS = 8
RENDER_RESOLUTION_PX = 768
RENDER_SAMPLES = 32

# Time kept back from the remaining invocation time for attributes, uploads and the manifest write; the
# render itself gets the rest, never less than MIN_RENDER_SECONDS.
RENDER_RESERVE_SECONDS = 90
MIN_RENDER_SECONDS = 30
DEFAULT_RENDER_SECONDS_WITHOUT_CONTEXT = 600
# trimesh runs after the render only when at least this much invocation time is left.
TRIMESH_MIN_REMAINING_MS = 60_000

SHA256_MAX_BYTES = 512 * 1024 * 1024
MAX_SIBLING_FILES = 500
MAX_SIBLING_TOTAL_BYTES = 2 * 1024 ** 3
# Files a mesh or USD loader references beside the primary file.
SIBLING_EXTENSIONS = frozenset({
    ".bin", ".mtl",
    ".png", ".jpg", ".jpeg", ".tga", ".bmp", ".tif", ".tiff", ".webp", ".exr", ".hdr", ".dds", ".ktx", ".ktx2",
    ".usd", ".usda", ".usdc", ".usdz",
})
OUTPUT_TAIL_CHARS = 4000

BLENDER_EXECUTABLE = "blender"
BLENDER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "renderScene.py")

REQUIRED_EVENT_KEYS = ("inputS3AssetFilePath", "analysisManifestS3Location", "inputOutputS3AssetAuxiliaryFilesPath")


def parse_s3_uri(uri):
    """(bucket, key) of an s3:// URI; ValueError for anything else."""
    if not isinstance(uri, str) or not uri.startswith("s3://"):
        raise ValueError(f"not an s3:// URI: {uri!r}")
    bucket, _, key = uri[len("s3://"):].partition("/")
    if not bucket or not key:
        raise ValueError(f"an s3:// URI needs a bucket and a key: {uri!r}")
    return bucket, key


def read_json_object(bucket, key):
    """The JSON object stored at bucket/key, or None when the key does not exist."""
    try:
        body = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read()
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return None
        raise
    parsed = json.loads(body.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError(f"s3://{bucket}/{key} is not a JSON object")
    return parsed


def _as_bool(value, default):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes"):
            return True
        if lowered in ("false", "0", "no"):
            return False
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def load_render_options(config_location):
    """Render options from the template configuration (`includeSiblingFiles`, `renderViews`); the defaults
    plus a warning when the configuration is missing or unreadable, the defaults alone when none is named."""
    options = {"includeSiblingFiles": True, "renderViews": DEFAULT_RENDER_VIEWS}
    warnings = []
    if not config_location:
        return options, warnings
    try:
        bucket, key = parse_s3_uri(config_location)
        body = read_json_object(bucket, key)
    except (ValueError, ClientError) as error:
        warnings.append(f"input configuration unreadable ({type(error).__name__}); using defaults")
        return options, warnings
    if body is None:
        warnings.append("input configuration missing; using defaults")
        return options, warnings
    options["includeSiblingFiles"] = _as_bool(body.get("includeSiblingFiles"), True)
    try:
        requested = int(body.get("renderViews", DEFAULT_RENDER_VIEWS))
    except (TypeError, ValueError):
        requested = DEFAULT_RENDER_VIEWS
        warnings.append("renderViews is not an integer; using the default")
    options["renderViews"] = max(1, min(requested, len(VIEW_ORDER)))
    return options, warnings


def download_primary(bucket, key, version_id, destination):
    """Download the primary file at the manifest's version (the current version when none is given)."""
    if version_id:
        s3_client.download_file(bucket, key, destination, ExtraArgs={"VersionId": version_id})
    else:
        s3_client.download_file(bucket, key, destination)
    return destination


def confined_local_path(root, relative_key):
    """The path `relative_key` resolves to under `root`, or None when it is empty or escapes the root.
    A leading separator is re-rooted; a `..` that stays inside is kept."""
    relative = relative_key.lstrip("/")
    if not relative:
        return None
    root_real = os.path.realpath(root)
    candidate = os.path.realpath(os.path.join(root_real, relative))
    if candidate == root_real or not candidate.startswith(root_real + os.sep):
        return None
    return candidate


def list_sibling_objects(bucket, parent_prefix):
    objects = []
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=parent_prefix):
        for entry in page.get("Contents", []):
            objects.append({"key": entry["Key"], "size": int(entry.get("Size", 0))})
    return objects


def download_siblings(bucket, primary_key, input_root):
    """Download the loader-referenced files beside the primary (textures, .mtl, .bin, USD layers) into
    `input_root`, confined to it and bounded by MAX_SIBLING_FILES / MAX_SIBLING_TOTAL_BYTES.
    Returns (downloaded local paths, warnings)."""
    parent_prefix = primary_key[: primary_key.rfind("/") + 1]
    downloaded, warnings = [], []
    total_bytes = 0
    for entry in list_sibling_objects(bucket, parent_prefix):
        key = entry["key"]
        if key == primary_key or key.endswith("/"):
            continue
        if os.path.splitext(key)[1].lower() not in SIBLING_EXTENSIONS:
            continue
        if len(downloaded) >= MAX_SIBLING_FILES:
            warnings.append(f"sibling download stopped at {MAX_SIBLING_FILES} files")
            break
        if total_bytes + entry["size"] > MAX_SIBLING_TOTAL_BYTES:
            warnings.append(f"sibling download stopped at {MAX_SIBLING_TOTAL_BYTES} bytes")
            break
        local_path = confined_local_path(input_root, key[len(parent_prefix):])
        if local_path is None:
            warnings.append(f"skipped sibling resolving outside the input directory: {key}")
            continue
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        s3_client.download_file(bucket, key, local_path)
        downloaded.append(local_path)
        total_bytes += entry["size"]
    return downloaded, warnings


def remaining_millis(context):
    getter = getattr(context, "get_remaining_time_in_millis", None)
    if getter is None:
        return DEFAULT_RENDER_SECONDS_WITHOUT_CONTEXT * 1000
    return int(getter())


def render_budget_seconds(context):
    """Seconds Blender may run: the remaining invocation time minus the reserve for attributes, uploads and
    the manifest write, never below MIN_RENDER_SECONDS; a fixed default without a Lambda context."""
    if getattr(context, "get_remaining_time_in_millis", None) is None:
        return DEFAULT_RENDER_SECONDS_WITHOUT_CONTEXT
    budget = int(remaining_millis(context) / 1000) - RENDER_RESERVE_SECONDS
    return max(MIN_RENDER_SECONDS, budget)


def blender_environment(work_dir):
    """Environment for the Blender subprocess: every directory it writes lives under the work directory."""
    env = dict(os.environ)
    for variable, folder in (
        ("HOME", "home"),
        ("XDG_CONFIG_HOME", "xdg-config"),
        ("XDG_CACHE_HOME", "xdg-cache"),
        ("XDG_RUNTIME_DIR", "xdg-runtime"),
        ("BLENDER_USER_RESOURCES", "blender-user"),
        ("TMPDIR", "tmp"),
    ):
        path = os.path.join(work_dir, folder)
        os.makedirs(path, exist_ok=True)
        env[variable] = path
    env["DISPLAY"] = ""
    return env


def blender_command(input_path, output_dir, views):
    return [
        BLENDER_EXECUTABLE, "--background", "-noaudio", "--python", BLENDER_SCRIPT, "--",
        "--input", input_path,
        "--output-dir", output_dir,
        "--views", ",".join(views),
        "--resolution", str(RENDER_RESOLUTION_PX),
        "--samples", str(RENDER_SAMPLES),
    ]


def _tail(text):
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    return text[-OUTPUT_TAIL_CHARS:]


def run_blender(input_path, output_dir, views, timeout_seconds, env):
    """Run the render script once for every requested view.

    subprocess.run drains both pipes through communicate(), so capture_output cannot deadlock; a render of
    at most a few minutes reporting its log at exit is acceptable, and the bounded tail is what reaches the
    execution record when it fails. Returns {returncode, timedOut, outputTail, seconds}."""
    command = blender_command(input_path, output_dir, views)
    logger.info({"message": "Starting Blender", "views": list(views), "timeoutSeconds": timeout_seconds})
    started = time.monotonic()
    try:
        completed = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
            command, capture_output=True, text=True, timeout=timeout_seconds, env=env, cwd=output_dir)
    except subprocess.TimeoutExpired as expired:
        tail = _tail(expired.stdout) + _tail(expired.stderr)
        logger.error({"message": "Blender timed out", "timeoutSeconds": timeout_seconds, "outputTail": tail})
        return {"returncode": None, "timedOut": True, "outputTail": tail,
                "seconds": round(time.monotonic() - started, 3)}
    tail = _tail(completed.stdout) + _tail(completed.stderr)
    logger.info({"message": "Blender finished", "returncode": completed.returncode, "outputTail": tail})
    return {"returncode": completed.returncode, "timedOut": False, "outputTail": tail,
            "seconds": round(time.monotonic() - started, 3)}


def collect_renders(output_dir):
    return sorted(
        name for name in os.listdir(output_dir)
        if name.startswith(RENDER_FILE_PREFIX) and name.endswith(".png"))


def read_scene_facts(output_dir):
    path = os.path.join(output_dir, SCENE_FACTS_FILENAME)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            facts = json.load(handle)
    except (OSError, ValueError):
        return None
    return facts if isinstance(facts, dict) else None


def upload_renders(bucket, aux_prefix, output_dir, names):
    keys = []
    for name in names:
        key = f"{aux_prefix}{RENDERS_PREFIX}{name}"
        s3_client.upload_file(os.path.join(output_dir, name), bucket, key, ExtraArgs={"ContentType": "image/png"})
        keys.append(key)
    return keys


def sha256_of_file(path, max_bytes=None):
    if max_bytes is None:
        max_bytes = SHA256_MAX_BYTES
    if os.path.getsize(path) > max_bytes:
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fact_number(value):
    """A human-readable number: thousands separators, at most three decimals, no trailing zeros."""
    return f"{float(value):,.3f}".rstrip("0").rstrip(".") or "0"


def build_facts(attributes):
    """The manifest's `facts`: a flat {label: human-readable string} map -- the metadata generator joins it
    as `label: value` into the model prompt and the embedding source text -- built from the merged attribute
    sections. An absent section or key contributes nothing; every value is a str."""
    facts = {}
    geometry = attributes.get("sys_geometry") or {}
    dimensions = geometry.get("dimensions") or {}
    if all(axis in dimensions for axis in ("width", "height", "depth")):
        size = " x ".join(_fact_number(dimensions[axis]) for axis in ("width", "height", "depth"))
        facts["dimensions"] = f"{size} {geometry['units']}" if geometry.get("units") else size
    statistics = attributes.get("sys_statistics") or {}
    for label, key in (
        ("meshes", "meshCount"), ("vertices", "vertices"), ("faces", "faces"), ("triangles", "triangles"),
    ):
        if isinstance(statistics.get(key), int):
            facts[label] = f"{statistics[key]:,}"
    visual = attributes.get("sys_visual") or {}
    if visual.get("materialNames"):
        facts["materials"] = ", ".join(str(name) for name in visual["materialNames"])
    elif isinstance(visual.get("materialCount"), int):
        facts["materials"] = f"{visual['materialCount']:,}"
    if isinstance(visual.get("textureCount"), int):
        facts["textures"] = f"{visual['textureCount']:,}"
    scene = attributes.get("sys_scene") or {}
    for label, key in (("armature", "hasArmature"), ("animation", "hasAnimation")):
        if isinstance(scene.get(key), bool):
            facts[label] = "yes" if scene[key] else "no"
    return facts


def build_sys_file(event, key, local_path, existing):
    """sys_file for the manifest: the pre-written one when constructPipeline supplied it, otherwise from the
    event and the downloaded file; sha256 is added when absent and the file is within the hashing cap."""
    sys_file = dict(existing or {})
    name = os.path.basename(key)
    sys_file.setdefault("name", name)
    sys_file.setdefault("ext", os.path.splitext(name)[1].lower())
    if "sizeBytes" not in sys_file:
        sys_file["sizeBytes"] = int(event.get("fileSize") or os.path.getsize(local_path))
    sys_file.setdefault("contentType", event.get("contentType") or "")
    sys_file.setdefault("etag", event.get("etag") or "")
    sys_file.setdefault("versionId", event.get("versionId") or "")
    if not sys_file.get("sha256"):
        digest = sha256_of_file(local_path)
        if digest:
            sys_file["sha256"] = digest
    return sys_file


def write_analysis_manifest(bucket, key, manifest):
    s3_client.put_object(
        Bucket=bucket, Key=key,
        Body=json.dumps(manifest, sort_keys=True).encode("utf-8"),
        ContentType="application/json")


def lambda_handler(event, context):
    logger.info({"message": "BLENDER render branch invoked", "fileClass": event.get("fileClass"),
                 "relativePath": event.get("relativePath"), "assetId": event.get("assetId")})
    missing = [key for key in REQUIRED_EVENT_KEYS if not event.get(key)]
    if missing:
        raise ValueError(f"BLENDER render branch event lacks required keys: {missing}")

    input_bucket, input_key = parse_s3_uri(event["inputS3AssetFilePath"])
    manifest_bucket, manifest_key = parse_s3_uri(event["analysisManifestS3Location"])
    aux_bucket, aux_prefix = parse_s3_uri(event["inputOutputS3AssetAuxiliaryFilesPath"])
    if not aux_prefix.endswith("/"):
        aux_prefix += "/"
    version_id = event.get("versionId") or ""
    file_class = event.get("fileClass") or DEFAULT_FILE_CLASS
    extension = os.path.splitext(input_key)[1].lower()
    warnings = []

    work_dir = tempfile.mkdtemp(prefix="genai-blender-")
    try:
        input_root = os.path.join(work_dir, "input")
        output_dir = os.path.join(work_dir, "output")
        os.makedirs(input_root)
        os.makedirs(output_dir)

        existing = read_json_object(manifest_bucket, manifest_key) or {}
        existing_attributes = dict(existing.get("attributes") or {})
        options, option_warnings = load_render_options(event.get("inputConfigurationS3Location", ""))
        warnings.extend(option_warnings)

        local_path = os.path.join(input_root, os.path.basename(input_key))
        download_primary(input_bucket, input_key, version_id, local_path)
        if options["includeSiblingFiles"]:
            _siblings, sibling_warnings = download_siblings(input_bucket, input_key, input_root)
            warnings.extend(sibling_warnings)

        views = VIEW_ORDER[:options["renderViews"]]
        outcome = run_blender(local_path, output_dir, views, render_budget_seconds(context),
                              blender_environment(work_dir))
        rendered = collect_renders(output_dir)
        facts = read_scene_facts(output_dir)
        if outcome["timedOut"]:
            warnings.append(
                f"Blender timed out after {outcome['seconds']}s; output tail: {outcome['outputTail'][-500:]}")
        elif outcome["returncode"] != 0:
            warnings.append(
                f"Blender exited with code {outcome['returncode']}; output tail: {outcome['outputTail'][-500:]}")
        if facts and facts.get("error"):
            warnings.append(f"Blender import failed: {facts['error']}")
        if facts and facts.get("failedViews"):
            warnings.append(f"views failed to render: {sorted(facts['failedViews'])}")

        render_images = upload_renders(aux_bucket, aux_prefix, output_dir, rendered)
        render_skipped = None if render_images else "error"

        computed = {}
        if remaining_millis(context) >= TRIMESH_MIN_REMAINING_MS:
            computed, trimesh_warnings = compute_trimesh_attributes(local_path, extension)
            warnings.extend(trimesh_warnings)
        else:
            warnings.append("trimesh attributes skipped: insufficient remaining time")
        attributes = dict(existing_attributes)
        attributes.update(merge_scene_facts(computed, facts, extension))
        # The unit the file declares (header parse; the composed USD stage's metersPerUnit when Blender read it)
        # is recorded under sys_format beside the loader; sys_geometry.units stays the measuring loader's label.
        declared = declared_units(local_path, extension, (facts or {}).get("metersPerUnit"))
        attributes = apply_declared_units(attributes, declared, extension)
        attributes["sys_file"] = build_sys_file(event, input_key, local_path, existing_attributes.get("sys_file"))

        manifest = {
            "schemaVersion": 1,
            "fileClass": file_class,
            "renderBranch": RENDER_BRANCH,
            "attributes": attributes,
            "renderImages": render_images,
            "textExcerpt": existing.get("textExcerpt"),
            "facts": build_facts(attributes),
            "warnings": warnings,
            "renderSkipped": render_skipped,
            "renderDiagnostics": {
                "blender": facts or {},
                "renderSeconds": outcome["seconds"],
                "requestedViews": list(views),
                "renderedViews": [name[len(RENDER_FILE_PREFIX):-len(".png")] for name in rendered],
                "blenderExitCode": outcome["returncode"],
            },
        }
        write_analysis_manifest(manifest_bucket, manifest_key, manifest)
        logger.info({"message": "BLENDER render branch finished", "renderImages": len(render_images),
                     "renderSkipped": render_skipped, "warnings": len(warnings)})
        # The whole state goes back, extended with the branch-task contract keys (analysisManifestS3Location,
        # renderBranch, fileClass, renderSkipped, renderImageCount, warningCount) and the informational extras:
        # the state machine takes `$.Payload` as the new state and every field GenerateMetadataTask reads
        # survives; `renderSkipped` on the state is the usd-fallback signal.
        result = dict(event)
        result.update({
            "analysisManifestS3Location": event["analysisManifestS3Location"],
            "renderBranch": RENDER_BRANCH,
            "fileClass": file_class,
            "renderSkipped": render_skipped,
            "renderImageCount": len(render_images),
            "warningCount": len(warnings),
            "renderStatus": "SUCCEEDED" if render_skipped is None else "DEGRADED",
            "renderImages": render_images,
            "warnings": warnings,
        })
        return result
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

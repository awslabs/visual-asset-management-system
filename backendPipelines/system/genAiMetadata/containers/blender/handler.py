# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Lambda handler for the BLENDER render branch of the SYSTEM - GenAI Metadata pipeline.

Downloads the primary file (at its S3 version) at its path relative to its asset root and, unless the
template turns it off (`resolveDependencies`), the files it references, transitively, from that asset
alone (`scene_dependencies`, a byte-identical copy of the 3D thumbnail's resolver), each at the version its
HeadObject returned and at its own asset-relative path, so `../` references resolve on disk. When the
referenced files would not fit the Lambda's byte budget and the Fargate renderer is deployed, it returns
`renderBranch: "FARGATE"` with `dependencyOverflow: true` before rendering, and the state machine runs the
Fargate render job instead. Otherwise it runs Blender once under a timeout derived from the remaining
invocation time, uploads the rendered views to `{auxTempPrefix}renders/`, computes trimesh attributes,
records the unit the file declares, writes the execution's analysis manifest (with the referenced-file
report under `dependencyReport`; for USD the unresolved paths of Blender's own dependency check are the
missing list, except the files the walk fetched, which are listed under `notComposed`), and returns the
pipeline state extended with this branch's keys. Render faults never fail
the invocation: they leave `renderSkipped: "error"` in the manifest and on the state and the run continues
attributes-only.
"""

import hashlib
import json
import os
import posixpath
import shutil
import subprocess
import tempfile
import time

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from containerLogger import safeLogger
import executionLogKeys
from formatUnits import declared_units
from meshAttributes import apply_declared_units, compute_trimesh_attributes, merge_scene_facts
import scene_dependencies

# Adaptive retry with client-side rate limiting. A pipeline lambda runs against throttling-prone services
# for the length of a job, so a bare client sits on botocore's default mode with no rate limiting and a
# sustained burst surfaces as a throttling error on the caller.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = safeLogger(service="systemGenAiMetadataBlenderRender")

s3_client = boto3.client('s3', config=retry_config)

# An asset bucket may be in another Region than this function. The pipeline state carries a bucket name
# -> Region map (`bucketRegions`, built by constructPipeline from the manifest), the handler registers it,
# and every request for a registered bucket is signed with a client for that Region; the client above
# serves the buckets the map leaves out (the auxiliary bucket, a state from an older Lambda). Per-Region
# clients keep the adaptive retry mode and address the regional us-east-1 endpoint, which an interface
# endpoint's private DNS covers where the global s3.amazonaws.com name is not.
_bucket_regions = {}
_clients_by_region = {}
_regional_retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'},
                                s3={'us_east_1_regional_endpoint': 'regional'})


def register_bucket_regions(bucket_regions):
    """Record the Region of each named bucket from a state's bucketRegions map."""
    for bucket_name, region in (bucket_regions or {}).items():
        if bucket_name and region:
            _bucket_regions[str(bucket_name)] = str(region)


def client_for_bucket(bucket_name):
    """The S3 client for the Region a bucket was registered in; the default client otherwise."""
    region = _bucket_regions.get(bucket_name)
    if not region or region == s3_client.meta.region_name:
        return s3_client
    regional = _clients_by_region.get(region)
    if regional is None:
        regional = boto3.client("s3", region_name=region, config=_regional_retry_config)
        _clients_by_region[region] = regional
    return regional


RENDER_BRANCH = "BLENDER"
DEFAULT_FILE_CLASS = "mesh"
RENDERS_PREFIX = "renders/"

# Shared with renderScene.py, which cannot be imported here (it imports bpy); the test suite pins equality.
RENDER_FILE_PREFIX = "render_"
SCENE_FACTS_FILENAME = "scene_facts.json"
VIEW_ORDER = ("perspective_front", "front", "right", "top", "back", "left", "bottom", "perspective_back")
# renderScene's facts-only request: the scene is imported and measured, no view is rendered.
NO_VIEWS = "none"

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
OUTPUT_TAIL_CHARS = 4000

# Template tag RESOLVE_DEPENDENCIES arrives under this key; a configuration written before the tag
# existed carries INCLUDE_SIBLING_FILES' key, read when the new one is absent.
RESOLVE_DEPENDENCIES_KEY = "resolveDependencies"
LEGACY_RESOLVE_DEPENDENCIES_KEY = "includeSiblingFiles"
# The configuration keys that may lower the referenced-file limits (scene_dependencies.limits_from_config).
DEPENDENCY_LIMITS_KEY = "dependencyLimits"
# The manifest key the referenced-file report is written under (read by the metadata step).
DEPENDENCY_REPORT_KEY = "dependencyReport"
# Time kept back from the referenced-file walk for Blender's import and render, on top of
# RENDER_RESERVE_SECONDS; the walk always gets at least MIN_DEPENDENCY_SECONDS.
DEPENDENCY_RENDER_RESERVE_SECONDS = 300
MIN_DEPENDENCY_SECONDS = 30
# The Fargate render branch, and whether this deployment has it (the construct sets the variable).
FARGATE_BRANCH = "FARGATE"
USE_FARGATE_RENDERER = os.environ.get("USE_FARGATE_RENDERER", "false").strip().lower() == "true"
# The unresolved paths of Blender's USD dependency check kept in the report.
MAX_VERIFIED_MISSING = 200
# A missing or unloaded file of these types leaves the composed scene partial.
USD_SCENE_EXTENSIONS = (".usd", ".usda", ".usdc", ".usdz")

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
    """Render options from the template configuration (`resolveDependencies`, read from the earlier
    `includeSiblingFiles` key when absent; `renderViews`; the limit overrides); the defaults plus a warning
    when the configuration is missing or unreadable, the defaults alone when none is named."""
    options = {RESOLVE_DEPENDENCIES_KEY: True, "renderViews": DEFAULT_RENDER_VIEWS, DEPENDENCY_LIMITS_KEY: {}}
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
    options[RESOLVE_DEPENDENCIES_KEY] = _as_bool(
        body.get(RESOLVE_DEPENDENCIES_KEY, body.get(LEGACY_RESOLVE_DEPENDENCIES_KEY)), True)
    options[DEPENDENCY_LIMITS_KEY] = {key: body[key] for key in scene_dependencies.LIMIT_OVERRIDE_KEYS if key in body}
    try:
        requested = int(body.get("renderViews", DEFAULT_RENDER_VIEWS))
    except (TypeError, ValueError):
        requested = DEFAULT_RENDER_VIEWS
        warnings.append("renderViews is not an integer; using the default")
    options["renderViews"] = max(1, min(requested, len(VIEW_ORDER)))
    return options, warnings


def apply_state_options(options, event):
    """The render options with the pipeline state's `resolveDependencies` applied: constructPipeline copies
    the template value onto the state, so when the state carries the key it decides; otherwise the
    configuration's value (or its default) stands."""
    if RESOLVE_DEPENDENCIES_KEY not in (event or {}):
        return options
    options = dict(options)
    options[RESOLVE_DEPENDENCIES_KEY] = _as_bool(event[RESOLVE_DEPENDENCIES_KEY], options[RESOLVE_DEPENDENCIES_KEY])
    return options


def download_primary(bucket, key, version_id, destination):
    """Download the primary file at the manifest's version (the current version when none is given)."""
    if version_id:
        client_for_bucket(bucket).download_file(bucket, key, destination, ExtraArgs={"VersionId": version_id})
    else:
        client_for_bucket(bucket).download_file(bucket, key, destination)
    return destination


def input_location(event, input_key, input_root):
    """(asset root key, the input's root-relative path, its local path, the local root): the input is
    mirrored at its asset-relative path under `input_root/asset`, so the files it references land at theirs
    and relative references (`../` included) resolve on disk as they do in the asset."""
    root_key = scene_dependencies.confinement_root(input_key, str(event.get("assetId") or ""),
                                                   str(event.get("relativePath") or ""),
                                                   str(event.get("assetRootS3Key") or ""))
    local_root = os.path.join(input_root, "asset")
    input_rel = scene_dependencies.relative_to_root(input_key, root_key)
    local_path = scene_dependencies.local_path_for(local_root, input_rel) or \
        os.path.join(input_root, os.path.basename(input_key))
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    return root_key, input_rel, local_path, local_root


def dependency_limits(options):
    """The Lambda byte budget and the default file, depth and lookup limits, each lowered by the
    configuration's overrides."""
    base = scene_dependencies.FetchLimits(max_total_bytes=scene_dependencies.LAMBDA_MAX_TOTAL_BYTES)
    return scene_dependencies.limits_from_config(base, options.get(DEPENDENCY_LIMITS_KEY))


def dependency_deadline(context):
    """The `time.monotonic()` value the walk stops at: the remaining invocation time less the render
    reserves, never under MIN_DEPENDENCY_SECONDS."""
    seconds = remaining_millis(context) / 1000.0 - RENDER_RESERVE_SECONDS - DEPENDENCY_RENDER_RESERVE_SECONDS
    return time.monotonic() + max(MIN_DEPENDENCY_SECONDS, seconds)


def discover_dependencies(bucket, root_key, input_rel, local_path, local_root, extension, options, context):
    """The resolver after its discovery phase (documents downloaded, every other file sized and its version
    pinned), or None when the format references nothing or discovery faulted (a warning: the input alone
    renders)."""
    if not scene_dependencies.has_scene_references(extension):
        return None
    resolver = scene_dependencies.DependencyResolver(
        scene_dependencies.ClientStore(client_for_bucket(bucket), bucket, log=logger), root_key, local_root,
        dependency_limits(options), deadline=dependency_deadline(context), log=logger)
    try:
        resolver.discover(input_rel, local_path, extension)
    except Exception as error:  # the walk never fails the render
        logger.warning({"message": "Referenced-file discovery failed", "error": f"{type(error).__name__}: {error}"})
        return None
    return resolver


def fargate_reads_region(region):
    """Whether the Fargate render job, which runs in the VPC's isolated subnets, can read a bucket in
    ``region``: the deployment Region (``""`` or ``AWS_REGION``), or a Region in the comma-separated
    ``CROSS_REGION_S3_ENDPOINT_REGIONS`` the VPC has a cross-Region S3 interface endpoint for."""
    if not region or region == os.environ.get("AWS_REGION", ""):
        return True
    reachable = {name.strip() for name in os.environ.get("CROSS_REGION_S3_ENDPOINT_REGIONS", "").split(",")}
    return region in reachable


def overflows_to_fargate(resolver):
    """True when the deployment has the Fargate renderer and the referenced files discovery sized exceed
    the Lambda byte budget (and no configuration lowered the budget below it, which Fargate would keep)."""
    if not USE_FARGATE_RENDERER or resolver is None:
        return False
    budget = scene_dependencies.LAMBDA_MAX_TOTAL_BYTES
    return resolver.limits.max_total_bytes >= budget and resolver.predicted_bytes > budget


def _local_relative(path, local_root):
    """`path` relative to the local root (posix separators), or None when it is outside it."""
    base = os.path.realpath(local_root)
    target = os.path.realpath(str(path))
    if not target.startswith(base + os.sep):
        return None
    return os.path.relpath(target, base).replace(os.sep, "/")


def _package_reference_target(rel):
    """For OpenUSD's package-relative path ``<package>.usdz[<path>]``, the asset-relative file the walk
    resolved the same reference to: ``<path>`` against the package's own folder (``../parts/x.usda`` in
    ``usdz/a.usdz`` is ``parts/x.usda``). OpenUSD anchors every relative reference of a package's layer
    inside the package, so it reports such a reference unresolved even when the walk fetched the file.
    None for a path that is not package-relative; ``""`` when the reference leaves the asset."""
    package, bracket, inner = rel.partition("[")
    if not bracket or not inner.endswith("]") or not package.lower().endswith(".usdz"):
        return None
    inner = inner[:-1].replace("\\", "/")
    if not inner or inner.startswith("/"):
        return ""
    target = posixpath.normpath(posixpath.join(posixpath.dirname(package), inner))
    return "" if target == ".." or target.startswith("../") else target


def apply_usd_verification(report, check, local_root, skipped=(), loads_nothing=()):
    """Replace the report's missing list with the unresolved paths of Blender's own dependency check of
    the composed stage (renderScene's `usdDependencies`), which also sees what the per-layer walk cannot
    (an asset path assembled by composition). A path outside the local root was refused, not missing, and
    one the walk skipped for a limit is in the asset; neither is listed. A path the walk named only as one
    that loads nothing (`loads_nothing`: a layer or prim metadata asset path such as `customLayerData`,
    or a renderer's own MDL module) removes nothing from the scene when absent and is not listed either,
    the rule the walk's own report and the 3D thumbnail's incomplete preview apply. A file the walk
    fetched is never listed as missing: when the composed stage still does not load it (a `.usdz`
    package's reference to a file outside the package, which OpenUSD anchors inside the package) it is
    listed under `notComposed`. A package-relative path is reported as the asset file the walk looked for.
    Without a check the report is kept."""
    if not isinstance(check, dict) or not isinstance(check.get("unresolved"), list):
        return report
    skipped = set(skipped) | set(loads_nothing)
    fetched = {entry.get("rel") for entry in report.get("fetched") or [] if isinstance(entry, dict)}
    missing = []
    not_composed = []
    for path in check["unresolved"]:
        path = str(path)
        package, bracket, inner = path.partition("[")
        if bracket and inner.endswith("]"):
            # Split before resolving the local path: a `..` inside the brackets is not a path component.
            package_rel = _local_relative(package, local_root)
            if not package_rel:
                continue
            rel = package_rel + "[" + inner
            target = _package_reference_target(rel)
            if target == "":
                continue
            rel = target or rel
        else:
            rel = _local_relative(path, local_root)
            if not rel:
                continue
        if rel in skipped:
            continue
        bucket = not_composed if rel in fetched else missing
        if rel not in bucket:
            bucket.append(rel)
    missing = missing[:MAX_VERIFIED_MISSING]
    not_composed = not_composed[:MAX_VERIFIED_MISSING]
    required = [rel for rel in missing if os.path.splitext(rel)[1].lower() in USD_SCENE_EXTENSIONS]
    report = dict(report)
    report["missing"] = missing
    report["missingRequired"] = required
    report["notComposed"] = not_composed
    report["partial"] = bool(report.get("partial")) or bool(required) or any(
        os.path.splitext(rel)[1].lower() in USD_SCENE_EXTENSIONS for rel in not_composed)
    report["verifiedBy"] = "openusd"
    return report


def usd_verification_warnings(report, shown=5):
    """The manifest warning for the files the walk fetched that the composed USD stage does not load."""
    names = (report or {}).get("notComposed") or []
    if not names:
        return []
    more = f" and {len(names) - shown} more" if len(names) > shown else ""
    return [f"{len(names)} referenced file{'' if len(names) == 1 else 's'} fetched but not loaded by the composed "
            f"USD stage (a .usdz package resolves its relative references inside the package): "
            f"{', '.join(name[:120] for name in names[:shown])}{more}"]


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
    """The Blender invocation; an empty view list asks renderScene for the scene facts alone (`--views none`)."""
    return [
        BLENDER_EXECUTABLE, "--background", "-noaudio", "--python", BLENDER_SCRIPT, "--",
        "--input", input_path,
        "--output-dir", output_dir,
        "--views", ",".join(views) or NO_VIEWS,
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


def reroute_to_fargate(event, existing, resolver, file_class, warnings, manifest_bucket, manifest_key):
    """Hand the run to the Fargate render job before anything is rendered: the manifest keeps the
    discovery report (the Fargate branch replaces it with its own) and one warning saying why, so a failed
    Fargate job still leaves the reason with the analysis; the state returns `renderBranch: "FARGATE"` with
    `dependencyOverflow: true`, which DependencyOverflowChoice routes to the Fargate render job. The state
    stays small: the job receives it whole through one environment variable."""
    report = resolver.report()
    reason = (f"the referenced files ({resolver.predicted_bytes} bytes) exceed the Lambda budget "
              f"({scene_dependencies.LAMBDA_MAX_TOTAL_BYTES} bytes); the scene is rendered by the Fargate "
              f"render job")
    manifest = {"schemaVersion": 1, "fileClass": file_class, "attributes": {}, "renderImages": [],
                "textExcerpt": None, "facts": {}, "warnings": [], "renderSkipped": None}
    manifest.update(existing or {})
    manifest["renderBranch"] = FARGATE_BRANCH
    manifest["warnings"] = list(manifest.get("warnings") or []) + list(warnings) + [reason]
    manifest[DEPENDENCY_REPORT_KEY] = report.to_dict()
    write_analysis_manifest(manifest_bucket, manifest_key, manifest)
    logger.info({"message": "BLENDER render branch re-routed to Fargate", "predictedBytes": resolver.predicted_bytes,
                 "files": len(resolver.order)})
    result = dict(event)
    result.update({
        "analysisManifestS3Location": event["analysisManifestS3Location"],
        "renderBranch": FARGATE_BRANCH,
        "dependencyOverflow": True,
        "fileClass": file_class,
        "renderSkipped": None,
        "renderImageCount": 0,
        "warningCount": len(manifest["warnings"]),
    })
    return result


def lambda_handler(event, context):
    # VAMS reads this function's shared log group for the run's execution ids, so they go on every
    # line; the previous invocation's ids are removed first (see executionLogKeys).
    executionLogKeys.bind_execution_ids(logger, event)
    logger.info({"message": "BLENDER render branch invoked", "fileClass": event.get("fileClass"),
                 "relativePath": event.get("relativePath"), "assetId": event.get("assetId")})
    missing = [key for key in REQUIRED_EVENT_KEYS if not event.get(key)]
    if missing:
        raise ValueError(f"BLENDER render branch event lacks required keys: {missing}")

    # Sign each asset bucket's requests for its own Region before any S3 call.
    register_bucket_regions(event.get("bucketRegions"))
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
        options = apply_state_options(options, event)
        warnings.extend(option_warnings)

        root_key, input_rel, local_path, local_root = input_location(event, input_key, input_root)
        download_primary(input_bucket, input_key, version_id, local_path)
        resolver = None
        if options[RESOLVE_DEPENDENCIES_KEY]:
            resolver = discover_dependencies(input_bucket, root_key, input_rel, local_path, local_root, extension,
                                             options, context)
        if overflows_to_fargate(resolver):
            input_region = event.get("inputBucketRegion", "") or ""
            if fargate_reads_region(input_region):
                return reroute_to_fargate(event, existing, resolver, file_class, warnings, manifest_bucket,
                                          manifest_key)
            # The Fargate job cannot read the scene's bucket, so the scene renders here with what fits.
            warnings.append(
                f"the referenced files ({resolver.predicted_bytes} bytes) exceed the Lambda budget, but the "
                f"Fargate render job runs in the VPC, which has no cross-Region Amazon S3 endpoint for "
                f"{input_region}; the scene is rendered in Lambda with the referenced files that fit the budget")
        report = None
        if resolver is not None:
            try:
                resolver.fetch()
            except Exception as error:  # the walk never fails the render; the report shows what was fetched
                logger.warning({"message": "Referenced-file fetch failed",
                                "error": f"{type(error).__name__}: {error}"})
            report = resolver.report()
            warnings.extend(report.warnings())

        # Renders exist only as analysis-model input; with the GenAI layer off Blender imports and measures
        # the scene without rendering a view.
        render_images = _as_bool(event.get("renderImages"), _as_bool(event.get("genAiAnalysisEnabled"), True))
        views = VIEW_ORDER[:options["renderViews"]] if render_images else []
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

        render_image_keys = upload_renders(aux_bucket, aux_prefix, output_dir, rendered)
        render_skipped = None if (render_image_keys or not render_images) else "error"

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
            "renderImages": render_image_keys,
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
        if report is not None:
            manifest[DEPENDENCY_REPORT_KEY] = apply_usd_verification(
                report.to_dict(), (facts or {}).get("usdDependencies"), local_root, resolver.skipped,
                resolver.loads_nothing())
            # `warnings` is the manifest's (and the returned state's) list.
            warnings.extend(usd_verification_warnings(manifest[DEPENDENCY_REPORT_KEY]))
        write_analysis_manifest(manifest_bucket, manifest_key, manifest)
        logger.info({"message": "BLENDER render branch finished", "renderImages": len(render_image_keys),
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
            "renderImageCount": len(render_image_keys),
            "warningCount": len(warnings),
            "renderStatus": "SUCCEEDED" if render_skipped is None else "DEGRADED",
            "renderImages": render_image_keys,
            "warnings": warnings,
        })
        return result
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

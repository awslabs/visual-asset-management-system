# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Lambda entry point of the 3D render image: the RENDER3D branch of the SYSTEM GenAI metadata pipeline.

Downloads one input file, extracts ``sys_*`` attributes and renders still frames with the thumbnail
pipeline's format handlers, uploads the frames beside the analysis manifest, merges attributes, facts,
warnings and render keys into the manifest constructPipeline pre-wrote, and returns the state with the
manifest location. The parent task token is never touched here; PipelineEndTask reports it.

Event (the state object, or ``{"body": state}``):
    analysisManifestS3Location    s3://aux-bucket/{auxTempPrefix}analysis.json            required
    inputS3AssetFilePath          s3://asset-bucket/{key}                                  required
    inputConfigurationS3Location  the rendered template configBody; its ``renderViews``
                                  (template tag RENDER_VIEWS, 1..8) sets the still count,
                                  default 4 when the key or the file is absent              optional
    fileClass                     classifier value, copied into the manifest                optional
    fileExt                       ".stp", dotted lower-case; derived from the key if absent optional

Returns the whole state merged with analysisManifestS3Location, renderBranch, fileClass, renderSkipped,
renderImageCount and warningCount (BRANCH_RESULT_KEYS). The Fargate analysis job
(preview_pipeline.analysis.batch_job) calls the same function; importing this module starts nothing —
Xvfb and the work directories are set up per invocation.

The size gate and the point cap are constructPipeline's; a file routed to this branch is rendered.
"""

import dataclasses
import os
from typing import List, Tuple

from preview_pipeline import core, renderer
from preview_pipeline.analysis import analysis, workdirs
from preview_pipeline.utils import image_utils, manifest_io
from preview_pipeline.utils import s3_utils as s3
from preview_pipeline.utils.logging import get_logger

logger = get_logger()

RENDER_BRANCH = "RENDER3D"
MANIFEST_SCHEMA_VERSION = 1
RENDER_IMAGE_SUBDIR = "render/"
RENDER_IMAGE_NAME = "render3d_{index:02d}.png"
# Template tag RENDER_VIEWS arrives under this key in the input configuration.
RENDER_VIEWS_KEY = "renderViews"
# The keys this branch merges into the returned state; the state machine passes the whole payload on.
BRANCH_RESULT_KEYS = ("analysisManifestS3Location", "renderBranch", "fileClass", "renderSkipped",
                      "renderImageCount", "warningCount")


@dataclasses.dataclass
class Render3dRequest:
    analysis_manifest_s3_location: str
    input_s3_asset_file_path: str
    input_configuration_s3_location: str
    file_class: str
    file_extension: str


def parse_request(body) -> Render3dRequest:
    if not isinstance(body, dict):
        raise ValueError("The event body must be a JSON object")
    manifest_location = str(body.get("analysisManifestS3Location") or "")
    input_path = str(body.get("inputS3AssetFilePath") or "")
    if not manifest_location.startswith("s3://"):
        raise ValueError("analysisManifestS3Location must be an s3:// URI")
    if not input_path.startswith("s3://"):
        raise ValueError("inputS3AssetFilePath must be an s3:// URI")

    extension = body.get("fileExt") or os.path.splitext(input_path)[1]
    extension = ("." + str(extension).lower().lstrip(".")) if extension else ""
    if not extension:
        raise ValueError("The input file has no extension and fileExt was not supplied")

    return Render3dRequest(
        analysis_manifest_s3_location=manifest_location,
        input_s3_asset_file_path=input_path,
        input_configuration_s3_location=str(body.get("inputConfigurationS3Location") or ""),
        file_class=str(body.get("fileClass") or ""),
        file_extension=extension,
    )


def load_render_options(config_location: str) -> Tuple[dict, List[str]]:
    """Render options from the template configuration: ``renderViews`` clamped to
    ``[1, renderer.MAX_STILL_VIEWS]``, ``renderer.DEFAULT_STILL_VIEWS`` when absent. An unreadable or
    malformed configuration is a warning, not a fault: the render happens on the defaults."""
    options = {RENDER_VIEWS_KEY: renderer.DEFAULT_STILL_VIEWS}
    warnings: List[str] = []
    try:
        body = manifest_io.fetch_input_configuration(config_location)
    except manifest_io.InputConfigurationError as exc:
        warnings.append(f"Input configuration unreadable; rendering with the defaults: {exc}")
        return options, warnings
    if RENDER_VIEWS_KEY in body:
        try:
            requested = int(body[RENDER_VIEWS_KEY])
        except (TypeError, ValueError):
            warnings.append(f"{RENDER_VIEWS_KEY} is not an integer; using the default")
            requested = renderer.DEFAULT_STILL_VIEWS
        options[RENDER_VIEWS_KEY] = max(1, min(requested, renderer.MAX_STILL_VIEWS))
    return options, warnings


def empty_manifest(file_class: str) -> dict:
    return {"schemaVersion": MANIFEST_SCHEMA_VERSION, "fileClass": file_class, "renderBranch": RENDER_BRANCH,
            "attributes": {}, "renderImages": [], "textExcerpt": None, "facts": {}, "warnings": [],
            "renderSkipped": None}


def merge_manifest(existing, result, request: Render3dRequest, render_keys: list, extra_warnings=()) -> dict:
    """The pre-written manifest with this branch's results merged in: attributes and facts are added key
    by key (so ``sys_file`` from constructPipeline survives), warnings are appended (``extra_warnings``
    first, then the analysis warnings), render keys replace the list, and ``renderSkipped`` is the
    analysis outcome: ``None``, ``"unsupported"`` or ``"error"``."""
    manifest = dict(existing) if isinstance(existing, dict) else {}
    for key, default in empty_manifest(request.file_class).items():
        if key not in manifest:
            manifest[key] = default
    manifest["schemaVersion"] = MANIFEST_SCHEMA_VERSION
    manifest["renderBranch"] = RENDER_BRANCH
    if request.file_class:
        manifest["fileClass"] = request.file_class

    attributes = dict(manifest.get("attributes") or {})
    attributes.update(result.attributes)
    manifest["attributes"] = attributes
    facts = dict(manifest.get("facts") or {})
    facts.update(result.facts)
    manifest["facts"] = facts
    manifest["warnings"] = (list(manifest.get("warnings") or []) + list(extra_warnings)
                            + list(result.warnings))
    manifest["renderImages"] = list(render_keys)
    manifest["renderSkipped"] = result.render_skipped
    return manifest


def render_prefix_for(manifest_key: str) -> str:
    """The bucket-relative prefix the still frames are written under: ``render/`` beside the manifest."""
    directory = manifest_key.rsplit("/", 1)[0] + "/" if "/" in manifest_key else ""
    return directory + RENDER_IMAGE_SUBDIR


def _upload_frames(frames, bucket: str, prefix: str, local_dir: str) -> list:
    keys = []
    if not frames:
        return keys
    os.makedirs(local_dir, exist_ok=True)
    for index, frame in enumerate(frames):
        name = RENDER_IMAGE_NAME.format(index=index)
        local_path = os.path.join(local_dir, name)
        image_utils.save_png(frame, local_path)
        keys.append(s3.put_file(bucket, prefix + name, local_path, "image/png"))
    return keys


def lambda_handler(event, context):
    body = event.get("body", event) if isinstance(event, dict) else event
    request = parse_request(body)
    options, option_warnings = load_render_options(request.input_configuration_s3_location)
    n_views = options[RENDER_VIEWS_KEY]
    logger.info(f"render3d: {request.input_s3_asset_file_path} -> {request.analysis_manifest_s3_location} "
                f"(views={n_views})")

    workdirs.ensure_runtime_dirs()
    work_dir = workdirs.make_invocation_dir("render3d_")
    try:
        input_dir = os.path.join(work_dir, "input")
        os.makedirs(input_dir, exist_ok=True)
        input_bucket, input_key = s3.parse_s3_uri(request.input_s3_asset_file_path)
        local_path = s3.download(input_bucket, input_key,
                                 os.path.join(input_dir, os.path.basename(input_key)))
        if local_path is None or not os.path.isfile(local_path):
            raise RuntimeError(f"Unable to download {request.input_s3_asset_file_path}")
        if request.file_extension == ".gltf":
            try:
                core._download_gltf_dependencies(input_bucket, input_key, input_dir)
            except Exception as exc:
                logger.warning(f"Failed to download some GLTF dependencies: {exc}")

        result = analysis.analyze_local_file(local_path, request.file_extension, n_views=n_views,
                                             work_dir=work_dir)

        manifest_bucket, manifest_key = s3.parse_s3_uri(request.analysis_manifest_s3_location)
        render_keys = _upload_frames(result.frames, manifest_bucket, render_prefix_for(manifest_key),
                                     os.path.join(work_dir, "render"))
        manifest = merge_manifest(s3.get_json(manifest_bucket, manifest_key), result, request, render_keys,
                                  option_warnings)
        s3.put_json(manifest_bucket, manifest_key, manifest)
        logger.info(f"render3d: wrote {len(render_keys)} frames, attributes {sorted(result.attributes)}, "
                    f"renderSkipped={manifest['renderSkipped']}, warnings={len(manifest['warnings'])}")

        response = dict(body)
        response.update({
            "analysisManifestS3Location": request.analysis_manifest_s3_location,
            "renderBranch": RENDER_BRANCH,
            "fileClass": manifest["fileClass"],
            "renderSkipped": manifest["renderSkipped"],
            "renderImageCount": len(render_keys),
            "warningCount": len(manifest["warnings"]),
        })
        return response
    finally:
        workdirs.remove_dir(work_dir)

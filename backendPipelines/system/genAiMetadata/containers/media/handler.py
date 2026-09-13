# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""MEDIA branch of the SYSTEM GenAI metadata pipeline (the `MediaExtractTask` Lambda): receives the pipeline
state, downloads the one input file, runs the extractor for its file class, uploads the analysis images
beside the analysis manifest that constructPipeline pre-wrote, rewrites that manifest, and returns the state
with its own fields laid over it. Anything that stops the manifest from being rewritten raises; the state
machine's Catch degrades the render and continues attributes-only."""

import json
import mimetypes
import os
import shutil
import tempfile
from typing import Any, Callable, Dict, List, Optional, Tuple

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from customLogging.logger import safeLogger

from media_extractors import audio, data, documents, images, svg, text, video
from media_extractors.common import (
    CLASS_AUDIO,
    CLASS_DATA,
    CLASS_DOCUMENT,
    CLASS_IMAGE,
    CLASS_TEXT,
    CLASS_TILES3D,
    CLASS_VIDEO,
    DEFAULT_MAX_TEXT_CHARS,
    RENDER_BRANCH,
    BranchResult,
    ExtractContext,
    class_for_extension,
)

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md: the Lambda reads and writes
# Amazon S3 for the length of the extraction, so a bare client would surface a sustained burst as a
# throttling error instead of smoothing it.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = safeLogger(service="systemGenAiMetadataMediaExtract")

s3_client = boto3.client('s3', config=retry_config)

MANIFEST_SCHEMA_VERSION = 1
RENDER_IMAGE_PREFIX = "renders/"
_DEFAULT_CONTENT_TYPE = "application/octet-stream"
# The template default of EXTRACT_GEO_LOCATION, applied when the state carries no `extractGeoLocation`.
EXTRACT_GEO_LOCATION_DEFAULT = True
# Classes under which a .json arrives; the text path re-sniffs the parsed root (text / tiles3d / data for GeoJSON).
_JSON_SNIFFED_CLASSES = (CLASS_TEXT, CLASS_TILES3D, CLASS_DATA)

_EXTRACTORS: Dict[str, Callable[[str, ExtractContext], BranchResult]] = {
    CLASS_VIDEO: video.extract_video,
    CLASS_AUDIO: audio.extract_audio,
    CLASS_DOCUMENT: documents.extract_pdf,
    CLASS_TEXT: text.extract_text,
    CLASS_TILES3D: text.extract_text,
    CLASS_DATA: data.extract_data,
}


class MediaTaskError(ValueError):
    """The task payload or the pre-written analysis manifest is unusable."""


def parse_s3_uri(uri: Optional[str]) -> Tuple[str, str]:
    if not uri or not str(uri).startswith("s3://"):
        return "", ""
    bucket, _, key = str(uri)[len("s3://"):].partition("/")
    return bucket, key


def read_event(event: Any) -> Dict[str, Any]:
    """The state object, unwrapped from a `body` envelope when a direct invocation supplies one."""
    payload = event.get("body", event) if isinstance(event, dict) else event
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError as exc:
            raise MediaTaskError(f"Task payload is not JSON: {exc}")
    if not isinstance(payload, dict):
        raise MediaTaskError("Task payload must be a JSON object")
    return payload


def s3_location(payload: Dict[str, Any], key: str) -> Tuple[str, str]:
    """(bucket, key) of the s3:// URI the state carries under `key`; MediaTaskError names the field when it is
    missing or not an s3:// URI with both a bucket and a key."""
    bucket, object_key = parse_s3_uri(payload.get(key))
    if not bucket or not object_key:
        raise MediaTaskError(f"Task payload field '{key}' must be an s3:// URI with a bucket and key, got {payload.get(key)!r}")
    return bucket, object_key


def render_prefix_for(manifest_key: str) -> str:
    """The manifest's directory -- the execution's auxTempPrefix, trailing slash included; "" at the bucket root."""
    return manifest_key[:manifest_key.rfind("/") + 1]


def _positive_int(value: Any, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def state_flag(value: Any, default: bool) -> bool:
    """A boolean state field: a bool as is, the strings "true"/"false" (a direct invocation may quote them),
    otherwise `default`."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return default


def load_max_text_chars(config_location: Optional[str]) -> Tuple[int, List[str]]:
    """`maxTextChars` from the template configuration (tag `MAX_TEXT_CHARS`): the default alone when no
    configuration is named, the default plus a warning when it is unreadable, not an object, or carries no
    positive integer."""
    if not config_location:
        return DEFAULT_MAX_TEXT_CHARS, []
    bucket, key = parse_s3_uri(config_location)
    if not bucket or not key:
        return DEFAULT_MAX_TEXT_CHARS, [f"input configuration location is not an s3:// URI ({config_location!r}); using the default maxTextChars"]
    try:
        body = json.loads(s3_client.get_object(Bucket=bucket, Key=key)["Body"].read())
    except (ClientError, ValueError) as exc:
        return DEFAULT_MAX_TEXT_CHARS, [f"input configuration unreadable ({type(exc).__name__}); using the default maxTextChars"]
    if not isinstance(body, dict):
        return DEFAULT_MAX_TEXT_CHARS, ["input configuration is not a JSON object; using the default maxTextChars"]
    value = body.get("maxTextChars", DEFAULT_MAX_TEXT_CHARS)
    resolved = _positive_int(value, 0)
    if resolved <= 0:
        return DEFAULT_MAX_TEXT_CHARS, [f"maxTextChars {value!r} is not a positive integer; using the default"]
    return resolved, []


def select_extractor(file_class: Optional[str], extension: str) -> Tuple[Callable, str]:
    """The extractor for the class constructPipeline chose; the extension decides when the class is absent. A
    `.json` always takes the text path, which re-sniffs the parsed root and reclassifies to `tiles3d` or `data`
    (GeoJSON) itself, so a GeoJSON file the classifier already called `data` is never read as CSV."""
    resolved = file_class or class_for_extension(extension)
    if resolved == CLASS_IMAGE:
        return (svg.extract_svg if extension.lower() == ".svg" else images.extract_image), resolved
    if extension.lower() == ".json" and resolved in _JSON_SNIFFED_CLASSES:
        return text.extract_text, resolved
    if resolved not in _EXTRACTORS:
        raise MediaTaskError(f"fileClass '{resolved}' (extension '{extension}') is not served by the MEDIA branch")
    return _EXTRACTORS[resolved], resolved


def fetch_manifest(location: str) -> dict:
    bucket, key = parse_s3_uri(location)
    if not bucket or not key:
        raise MediaTaskError(f"Analysis manifest location is malformed: {location}")
    try:
        manifest = json.loads(s3_client.get_object(Bucket=bucket, Key=key)["Body"].read())
    except (ClientError, ValueError) as exc:
        raise MediaTaskError(f"Analysis manifest at {location} could not be read: {exc}")
    if not isinstance(manifest, dict):
        raise MediaTaskError(f"Analysis manifest at {location} is not a JSON object")
    return manifest


def download_input(bucket: str, key: str, version_id: Optional[str], work_dir: str, file_name: str) -> str:
    local_path = os.path.join(work_dir, "input" + os.path.splitext(file_name)[1].lower())
    extra_args = {"VersionId": version_id} if version_id else None
    s3_client.download_file(bucket, key, local_path, ExtraArgs=extra_args)
    return local_path


def upload_renders(paths: List[str], aux_bucket: str, aux_prefix: str) -> List[str]:
    keys = []
    for index, local_path in enumerate(paths, start=1):
        key = f"{aux_prefix}{RENDER_IMAGE_PREFIX}media-{index:02d}.png"
        with open(local_path, "rb") as handle:
            s3_client.put_object(Bucket=aux_bucket, Key=key, Body=handle, ContentType="image/png")
        keys.append(key)
    return keys


def merge_manifest(existing: dict, result: BranchResult, render_keys: List[str]) -> dict:
    """The pre-written manifest with this class's section, facts and warnings laid over it."""
    attributes = dict(existing.get("attributes") or {})
    attributes.update(result.attributes)
    facts = dict(existing.get("facts") or {})
    facts.update(result.facts)
    return {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "fileClass": result.file_class,
        "renderBranch": RENDER_BRANCH,
        "attributes": attributes,
        "renderImages": list(render_keys),
        "textExcerpt": result.text_excerpt,
        "facts": facts,
        "warnings": list(existing.get("warnings") or []) + list(result.warnings),
        "renderSkipped": result.render_skipped,
    }


def write_manifest(location: str, manifest: dict) -> None:
    bucket, key = parse_s3_uri(location)
    s3_client.put_object(
        Bucket=bucket, Key=key, Body=json.dumps(manifest, default=str).encode("utf-8"),
        ContentType="application/json")


def lambda_handler(event, context):
    payload = read_event(event)
    bucket, key = s3_location(payload, "inputS3AssetFilePath")
    aux_bucket, manifest_key = s3_location(payload, "analysisManifestS3Location")
    manifest_location = payload["analysisManifestS3Location"]
    aux_prefix = render_prefix_for(manifest_key)
    version_id = payload.get("versionId") or None
    file_name = os.path.basename(str(payload.get("relativePath") or key))
    extension = os.path.splitext(file_name)[1].lower()
    extractor, file_class = select_extractor(payload.get("fileClass"), extension)
    extract_geo_location = state_flag(payload.get("extractGeoLocation"), EXTRACT_GEO_LOCATION_DEFAULT)
    logger.info({"message": "MEDIA extract task", "fileClass": file_class, "fileName": file_name,
                 "assetId": payload.get("assetId"), "analysisManifestS3Location": manifest_location,
                 "extractGeoLocation": extract_geo_location})
    existing = fetch_manifest(manifest_location)
    max_text_chars, config_warnings = load_max_text_chars(payload.get("inputConfigurationS3Location"))
    work_dir = tempfile.mkdtemp(prefix="media-")
    try:
        local_path = download_input(bucket, key, version_id, work_dir, file_name)
        ctx = ExtractContext(
            file_name=file_name,
            extension=extension,
            content_type=payload.get("contentType") or mimetypes.guess_type(file_name)[0] or _DEFAULT_CONTENT_TYPE,
            max_text_chars=max_text_chars,
            work_dir=work_dir,
            extract_geo_location=extract_geo_location,
        )
        result = extractor(local_path, ctx)
        result.warnings = config_warnings + list(result.warnings)
        render_keys = upload_renders(result.render_images, aux_bucket, aux_prefix)
        manifest = merge_manifest(existing, result, render_keys)
        write_manifest(manifest_location, manifest)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
    logger.info({"message": "MEDIA extract complete", "fileClass": manifest["fileClass"],
                 "renderImageCount": len(render_keys), "renderSkipped": manifest["renderSkipped"],
                 "warningCount": len(manifest["warnings"])})
    # The whole state goes back so a LambdaInvoke with outputPath "$.Payload" keeps every hop field
    # (assetId, databaseId, relativePath, versionId, outputS3Asset*Path, ...) for GenerateMetadataTask.
    return {
        **payload,
        "analysisManifestS3Location": manifest_location,
        "fileClass": manifest["fileClass"],
        "renderBranch": RENDER_BRANCH,
        "renderSkipped": manifest["renderSkipped"],
        "renderImageCount": len(render_keys),
        "warningCount": len(manifest["warnings"]),
    }

# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""MEDIA branch of the SYSTEM GenAI metadata pipeline (the `MediaExtractTask` Lambda): receives the pipeline
state, reads the one input file, runs the extractor for its file class, uploads the analysis images beside the
analysis manifest that constructPipeline pre-wrote, rewrites that manifest, and returns the state with its own
fields laid over it. The input is downloaded to ephemeral storage up to INPUT_MAX_DOWNLOAD_BYTES; over that bound
a video or audio file is read over a presigned URL by range, and any other class keeps its sys_file attributes.
Anything that stops the manifest from being rewritten raises; the state machine's Catch degrades the render and
continues attributes-only."""

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
import executionLogKeys

from media_extractors import audio, data, documents, generic, gis, images, office, svg, text, video
from media_extractors.common import (
    CLASS_AUDIO,
    CLASS_DATA,
    CLASS_DOCUMENT,
    CLASS_IMAGE,
    CLASS_OTHER,
    CLASS_TEXT,
    CLASS_TILES3D,
    CLASS_VIDEO,
    DEFAULT_MAX_TEXT_CHARS,
    INPUT_MAX_DOWNLOAD_BYTES,
    JSON_SNIFFED_EXTENSIONS,
    RENDER_BRANCH,
    RENDER_SKIPPED_SIZE,
    BranchResult,
    ExtractContext,
    class_for_extension,
    scrub_urls,
)
from media_extractors.s3_ranged import RangedS3Reader
from media_extractors.s3_regions import client_for_bucket, register_bucket_regions
import videoSegments
from contentChunks import CONTENT_EMBED_MAX_FILE_BYTES, CONTENT_TEXT_MAX_CHARS

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
# Template defaults of CONTENT_CHUNKING and VIDEO_SEGMENT_SECONDS, applied when the state carries no copy.
CONTENT_CHUNKING_DEFAULT = True
VIDEO_SEGMENT_SECONDS_DEFAULT = 0
# Classes whose full text is captured for content chunking.
FULL_TEXT_CLASSES = (CLASS_DOCUMENT, CLASS_TEXT, CLASS_DATA)
# The manifest's fullTextSkipped value for a file over CONTENT_EMBED_MAX_FILE_BYTES.
FULL_TEXT_SKIPPED_SIZE = "size"
# Auxiliary objects beside the manifest: the captured text with its page offsets, the video window plan with
# the bare array the Distributed Map's ItemReader iterates, and the prefix the Map's ResultWriter writes under.
FULL_TEXT_KEY = "text/full.txt"
PAGE_OFFSETS_KEY = "text/pages.json"
SEGMENT_PLAN_KEY = "segments/plan.json"
SEGMENT_ITEMS_KEY = "segments/items.json"
MAP_RESULTS_PREFIX = "segments/results/"
# A video whose windows would be analysed while the analysis model takes no image input: each window is described
# from its frames, so no plan is written (VideoSegmentChoice then routes past the segment Map) and the video's
# sys_media attribute and the manifest's warnings say why.
SEGMENTS_SKIPPED_NO_IMAGE_INPUT = "analysisModelTakesNoImageInput"
SEGMENTS_SKIPPED_WARNING = ("video segments not analysed: the analysis model takes no image input "
                            "(bedrockModels.analysisModelSupportsImages is false); per-window analysis needs an "
                            "image-capable analysis model")
# Classes under which a .json or .geojson arrives; the text path re-sniffs the parsed root (text / tiles3d / data
# for GeoJSON).
_JSON_SNIFFED_CLASSES = (CLASS_TEXT, CLASS_TILES3D, CLASS_DATA)
# Classes read over a presigned URL by range when the input is over INPUT_MAX_DOWNLOAD_BYTES: ffmpeg reads the
# container header and seeks by HTTP range, so these need no local copy. Every other class over the bound keeps
# its pre-written sys_file attributes.
URL_READ_CLASSES = (CLASS_VIDEO, CLASS_AUDIO)
# The presigned input URL's lifetime: longer than the function's 600-second timeout.
INPUT_PRESIGN_SECONDS = 900
# constructPipeline's signature-sniff labels (sys_file.detectedFormat) for a zip container, and for a tar or a
# compressed stream that may hold one.
ZIP_DETECTED_FORMAT = "zip"
TAR_DETECTED_FORMATS = ("tar", "gzip", "bzip2", "xz")

_EXTRACTORS: Dict[str, Callable[[str, ExtractContext], BranchResult]] = {
    CLASS_VIDEO: video.extract_video,
    CLASS_AUDIO: audio.extract_audio,
    CLASS_DOCUMENT: documents.extract_pdf,
    CLASS_TEXT: text.extract_text,
    CLASS_TILES3D: text.extract_text,
    CLASS_DATA: data.extract_data,
    CLASS_OTHER: generic.extract_generic,
}

# Office formats: the extension routes them whatever class the state carries — document, document, data.
_OFFICE_EXTRACTORS: Dict[str, Tuple[Callable[[str, ExtractContext], BranchResult], str]] = {
    ".docx": (office.extract_docx, CLASS_DOCUMENT),
    ".pptx": (office.extract_pptx, CLASS_DOCUMENT),
    ".xlsx": (office.extract_xlsx, CLASS_DATA),
}
# GIS containers read without GDAL: the extension routes them the same way, both under the data class.
_GIS_EXTRACTORS: Dict[str, Tuple[Callable[[str, ExtractContext], BranchResult], str]] = {
    ".gpkg": (gis.extract_gis, CLASS_DATA),
    ".lyrx": (gis.extract_gis, CLASS_DATA),
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
    """The extractor for the class constructPipeline chose; the extension decides when the class is absent. An
    office or GIS extension routes to its own extractor whatever the class says. A `.json` or `.geojson` always
    takes the text path, which re-sniffs the parsed root and reclassifies to `tiles3d` or `data` (GeoJSON) itself,
    so a GeoJSON file the classifier already called `data` is never read as CSV. A class the table does not name,
    or no class at all for an extension no table names, is `other`: the generic extractor probes the bytes."""
    ext = (extension or "").lower()
    if ext in _OFFICE_EXTRACTORS:
        extractor, office_class = _OFFICE_EXTRACTORS[ext]
        return extractor, (file_class or office_class)
    if ext in _GIS_EXTRACTORS:
        extractor, gis_class = _GIS_EXTRACTORS[ext]
        return extractor, (file_class or gis_class)
    resolved = file_class or class_for_extension(ext) or CLASS_OTHER
    if resolved == CLASS_IMAGE:
        return (svg.extract_svg if ext == ".svg" else images.extract_image), resolved
    if ext in JSON_SNIFFED_EXTENSIONS and resolved in _JSON_SNIFFED_CLASSES:
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
    # The input file's bucket may be in another Region; its reads are signed for that Region.
    client_for_bucket(bucket, s3_client).download_file(bucket, key, local_path, ExtraArgs=extra_args)
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


def upload_full_text(result: BranchResult, aux_bucket: str, aux_prefix: str) -> dict:
    """Writes the captured text and its page offsets beside the manifest and returns the five manifest fields:
    the two locations, the character count, the truncation flag and a None skip reason. The text is cut at
    CONTENT_TEXT_MAX_CHARS; page entries beyond the cut are dropped."""
    text = result.full_text
    truncated = bool(result.full_text_truncated)
    if len(text) > CONTENT_TEXT_MAX_CHARS:
        text, truncated = text[:CONTENT_TEXT_MAX_CHARS], True
    offsets = [entry for entry in result.page_offsets if int(entry.get("start", 0)) < len(text)]
    text_key = f"{aux_prefix}{FULL_TEXT_KEY}"
    pages_key = f"{aux_prefix}{PAGE_OFFSETS_KEY}"
    s3_client.put_object(Bucket=aux_bucket, Key=text_key, Body=text.encode("utf-8"),
                         ContentType="text/plain; charset=utf-8")
    s3_client.put_object(Bucket=aux_bucket, Key=pages_key, Body=json.dumps(offsets).encode("utf-8"),
                         ContentType="application/json")
    return {
        "fullTextS3Location": f"s3://{aux_bucket}/{text_key}",
        "fullTextChars": len(text),
        "fullTextTruncated": truncated,
        "fullTextSkipped": None,
        "pageOffsetsS3Location": f"s3://{aux_bucket}/{pages_key}",
    }


def oversized_input_result(file_class: str, file_name: str, file_size: int,
                           detected_format: Optional[str] = None) -> BranchResult:
    """The result for an input the image does not download and reads no other way: the state's class and the
    pre-written `sys_file` attributes stand, no render is produced, and a warning names the size and the bound.
    The MEDIA image reads a file from its ephemeral storage, so a file over INPUT_MAX_DOWNLOAD_BYTES would fail
    the download rather than the extractor, and geospatial containers in particular reach that size. A tar
    archive, plain or compressed, is listed only from a downloaded copy, which the warning says."""
    warning = (f"{file_name}: {file_size} bytes exceeds the {INPUT_MAX_DOWNLOAD_BYTES}-byte input bound; the file "
               "was not downloaded and sys_file attributes alone describe it")
    if file_class == CLASS_OTHER and detected_format in TAR_DETECTED_FORMATS:
        warning += (" (a tar archive, plain or compressed, is listed from a downloaded copy, so one over the bound "
                    "gets no sys_archive listing)")
    return BranchResult(file_class=file_class, render_skipped=RENDER_SKIPPED_SIZE, warnings=[warning])


def detected_format_of(manifest: dict) -> Optional[str]:
    """The format constructPipeline's signature sniff recorded in the pre-written sys_file, or None."""
    sys_file = (manifest.get("attributes") or {}).get("sys_file") or {}
    value = sys_file.get("detectedFormat")
    return str(value) if value else None


def list_zip_by_range(bucket: str, key: str, version_id: Optional[str], file_size: int,
                      ctx: ExtractContext) -> Optional[BranchResult]:
    """The sys_archive listing of a zip container of any size, read by ranged GETs of the input at its version
    (signed for the bucket's Region) instead of downloaded: the end-of-central-directory record, the central
    directory (up to ZIP_CENTRAL_DIRECTORY_MAX_BYTES; a larger one is not read) and, for an OPC package, its two
    bounded docProps parts. None when the bytes carry no end-of-central-directory record (a truncated file, or not
    a zip), so the caller takes the usual path. One log line counts the GETs and the bytes they read."""
    reader = RangedS3Reader(client_for_bucket(bucket, s3_client), bucket, key, version_id, size=file_size or None)
    result = None
    try:
        result = generic.list_zip(reader, reader.size, ctx)
    finally:
        archive = (result.attributes.get("sys_archive") if result is not None else None) or {}
        logger.info({"message": "ZIP container read by range", "fileSize": reader.size,
                     "rangedGets": reader.gets, "bytesRead": reader.bytes_read,
                     "listed": result is not None, "listing": archive.get("listing", "full" if archive else None),
                     "entryCount": archive.get("entryCount")})
    return result


def presign_input(bucket: str, key: str, version_id: Optional[str]) -> str:
    """A presigned GET of the input at the run's version, signed for the bucket's own Region (a presigned URL
    is honoured only there). The URL is a credential: it never reaches a log line, a warning or an error
    unscrubbed (scrub_urls)."""
    params = {"Bucket": bucket, "Key": key}
    if version_id:
        params["VersionId"] = version_id
    return client_for_bucket(bucket, s3_client).generate_presigned_url(
        "get_object", Params=params, ExpiresIn=INPUT_PRESIGN_SECONDS)


def read_by_url(file_class: str, url: str, ctx: ExtractContext, file_name: str, file_size: int) -> BranchResult:
    """A video or audio file over the download bound, read over a presigned URL instead of downloaded: ffmpeg
    reads the container header and the frames it seeks to by HTTP range requests, so a long video still gets
    its stream facts, keyframes and segment plan. Every warning, and the error of a failed read, is scrubbed
    of the URL."""
    try:
        if file_class == CLASS_VIDEO:
            result = video.extract_video(url, ctx)
        else:
            result = audio.extract_audio(url, ctx, read_tags=False)
    except Exception as exc:  # noqa: BLE001 - re-raised scrubbed: the message may quote the presigned URL
        raise MediaTaskError(f"{file_name}: the {file_class} could not be read over its URL: "
                             f"{scrub_urls(str(exc))}") from None
    result.warnings = [scrub_urls(warning) for warning in result.warnings]
    note = (f"{file_name}: {file_size} bytes exceeds the {INPUT_MAX_DOWNLOAD_BYTES}-byte download bound, so the "
            f"{file_class} was read over a presigned URL by range, not downloaded")
    result.warnings.insert(0, note)
    return result


def read_input(extractor: Callable, file_class: str, bucket: str, key: str, version_id: Optional[str],
               file_size: int, detected_format: Optional[str], ctx: ExtractContext) -> BranchResult:
    """The class extractor's result for the input, read within INPUT_MAX_DOWNLOAD_BYTES from a downloaded copy.
    Over the bound, decided from the state's fileSize before any byte is fetched: a video or audio file is read
    over a presigned URL by range (nothing is downloaded; the stream facts, keyframes and segment plan come from the
    ranged reads), and any other class keeps the pre-written sys_file attributes, since the file would not fit
    beside the image's other temporary files."""
    if file_size > INPUT_MAX_DOWNLOAD_BYTES and file_class in URL_READ_CLASSES:
        logger.info({"message": "MEDIA extract by URL: input over the download bound", "fileClass": file_class,
                     "fileSize": file_size, "inputMaxDownloadBytes": INPUT_MAX_DOWNLOAD_BYTES})
        return read_by_url(file_class, presign_input(bucket, key, version_id), ctx, ctx.file_name, file_size)
    if file_size > INPUT_MAX_DOWNLOAD_BYTES:
        logger.info({"message": "MEDIA extract skipped: input over the download bound", "fileClass": file_class,
                     "fileSize": file_size, "inputMaxDownloadBytes": INPUT_MAX_DOWNLOAD_BYTES})
        return oversized_input_result(file_class, ctx.file_name, file_size, detected_format)
    local_path = download_input(bucket, key, version_id, ctx.work_dir, ctx.file_name)
    return extractor(local_path, ctx)


def skip_full_text_for_size(manifest: dict, file_size: int) -> None:
    """Records in the manifest that no full text was captured because the file exceeds
    CONTENT_EMBED_MAX_FILE_BYTES: the skip reason, a zero character count, no text locations, and a warning naming
    the size and the bound."""
    manifest["fullTextSkipped"] = FULL_TEXT_SKIPPED_SIZE
    manifest["fullTextChars"] = 0
    manifest["warnings"].append(
        f"Full text not captured for content embedding: {file_size} bytes exceeds the "
        f"{CONTENT_EMBED_MAX_FILE_BYTES}-byte bound; the whole-file vector alone represents the file")


def write_segment_plan(plan: dict, aux_bucket: str, aux_prefix: str) -> dict:
    """Writes the window plan and, for the Distributed Map's ItemReader, the bare array of windows; returns the
    segment state fields: the items location in both URI and bucket/key form, and the bucket-relative prefix the
    Map's ResultWriter takes as Prefix.$ with the bucket name beside it (the CDK binds the aux bucket itself)."""
    plan_key = f"{aux_prefix}{SEGMENT_PLAN_KEY}"
    items_key = f"{aux_prefix}{SEGMENT_ITEMS_KEY}"
    s3_client.put_object(Bucket=aux_bucket, Key=plan_key, Body=json.dumps(plan).encode("utf-8"),
                         ContentType="application/json")
    s3_client.put_object(Bucket=aux_bucket, Key=items_key, Body=json.dumps(plan["segments"]).encode("utf-8"),
                         ContentType="application/json")
    return {
        "videoSegmentPlanS3Location": f"s3://{aux_bucket}/{plan_key}",
        "videoSegmentItemsS3Location": f"s3://{aux_bucket}/{items_key}",
        "videoSegmentItemsBucket": aux_bucket,
        "videoSegmentItemsKey": items_key,
        "videoSegmentResultsBucket": aux_bucket,
        "videoSegmentResultsPrefix": f"{aux_prefix}{MAP_RESULTS_PREFIX}",
        "videoSegmentCount": plan["count"],
    }


def skip_segments_without_image_input(manifest: dict, plan: dict) -> None:
    """Records that the video's windows were not analysed because the analysis model takes no image input: a
    ``videoSegments`` entry on its sys_media attribute, a manifest warning (it reaches analysis-summary.json) and
    one log warning."""
    sys_media = manifest["attributes"].setdefault("sys_media", {})
    sys_media["videoSegments"] = {"analysed": False, "reason": SEGMENTS_SKIPPED_NO_IMAGE_INPUT,
                                  "videoSegmentSeconds": plan["videoSegmentSeconds"], "plannedCount": plan["count"]}
    manifest["warnings"].append(SEGMENTS_SKIPPED_WARNING)
    logger.warning({"message": SEGMENTS_SKIPPED_WARNING, "plannedCount": plan["count"],
                    "videoSegmentSeconds": plan["videoSegmentSeconds"]})


def lambda_handler(event, context):
    payload = read_event(event)
    # VAMS reads this function's shared log group for the run's execution ids, so they go on every
    # line; the previous invocation's ids are removed first (see executionLogKeys).
    executionLogKeys.bind_execution_ids(logger, payload)
    # Sign each asset bucket's requests for its own Region before any S3 call.
    register_bucket_regions(payload.get("bucketRegions"))
    bucket, key = s3_location(payload, "inputS3AssetFilePath")
    aux_bucket, manifest_key = s3_location(payload, "analysisManifestS3Location")
    manifest_location = payload["analysisManifestS3Location"]
    aux_prefix = render_prefix_for(manifest_key)
    version_id = payload.get("versionId") or None
    file_name = os.path.basename(str(payload.get("relativePath") or key))
    extension = os.path.splitext(file_name)[1].lower()
    extractor, file_class = select_extractor(payload.get("fileClass"), extension)
    extract_geo_location = state_flag(payload.get("extractGeoLocation"), EXTRACT_GEO_LOCATION_DEFAULT)
    vector_search_enabled = state_flag(payload.get("vectorSearchEnabled"), False)
    # The GenAI layer: off, no analysis image is produced (nothing consumes it) and no video window plan is
    # written (a window is described by the analysis model before it is embedded).
    genai_analysis_enabled = state_flag(payload.get("genAiAnalysisEnabled"), True)
    render_images = state_flag(payload.get("renderImages"), genai_analysis_enabled)
    analysis_model_supports_images = state_flag(payload.get("analysisModelSupportsImages"), True)
    content_chunking = state_flag(payload.get("contentChunking"), CONTENT_CHUNKING_DEFAULT)
    segment_seconds = _positive_int(payload.get("videoSegmentSeconds"), VIDEO_SEGMENT_SECONDS_DEFAULT)
    file_size = _positive_int(payload.get("fileSize"), 0)
    content_embed = vector_search_enabled and content_chunking and file_class in FULL_TEXT_CLASSES
    over_size_bound = content_embed and file_size > CONTENT_EMBED_MAX_FILE_BYTES
    capture_full_text = content_embed and not over_size_bound
    logger.info({"message": "MEDIA extract task", "fileClass": file_class, "fileName": file_name,
                 "assetId": payload.get("assetId"), "analysisManifestS3Location": manifest_location,
                 "extractGeoLocation": extract_geo_location, "captureFullText": capture_full_text,
                 "fileSize": file_size, "videoSegmentSeconds": segment_seconds,
                 "genAiAnalysisEnabled": genai_analysis_enabled, "renderImages": render_images})
    existing = fetch_manifest(manifest_location)
    max_text_chars, config_warnings = load_max_text_chars(payload.get("inputConfigurationS3Location"))
    work_dir = tempfile.mkdtemp(prefix="media-")
    segment_state: Dict[str, Any] = {}
    try:
        ctx = ExtractContext(
            file_name=file_name,
            extension=extension,
            content_type=payload.get("contentType") or mimetypes.guess_type(file_name)[0] or _DEFAULT_CONTENT_TYPE,
            max_text_chars=max_text_chars,
            work_dir=work_dir,
            extract_geo_location=extract_geo_location,
            capture_full_text=capture_full_text,
            render_images=render_images,
        )
        detected_format = detected_format_of(existing)
        result = None
        if file_class == CLASS_OTHER and detected_format == ZIP_DETECTED_FORMAT:
            # A zip container of any size is listed from its central directory by ranged reads; one whose bytes
            # carry no end-of-central-directory record takes the usual path.
            result = list_zip_by_range(bucket, key, version_id, file_size, ctx)
        if result is None:
            result = read_input(extractor, file_class, bucket, key, version_id, file_size, detected_format, ctx)
        result.warnings = config_warnings + list(result.warnings)
        render_keys = upload_renders(result.render_images, aux_bucket, aux_prefix)
        manifest = merge_manifest(existing, result, render_keys)
        if capture_full_text and result.file_class in FULL_TEXT_CLASSES and result.full_text:
            manifest.update(upload_full_text(result, aux_bucket, aux_prefix))
        elif over_size_bound and result.file_class in FULL_TEXT_CLASSES:
            skip_full_text_for_size(manifest, file_size)
        if (result.file_class == CLASS_VIDEO and vector_search_enabled and genai_analysis_enabled
                and segment_seconds >= videoSegments.VIDEO_SEGMENT_MIN_SECONDS):
            duration = (result.attributes.get("sys_media") or {}).get("durationSeconds")
            plan = videoSegments.plan_segments(duration, segment_seconds)
            if plan and not analysis_model_supports_images:
                skip_segments_without_image_input(manifest, plan)
            elif plan:
                segment_state = write_segment_plan(plan, aux_bucket, aux_prefix)
            else:
                logger.info({"message": "No video segment plan", "durationSeconds": duration,
                             "videoSegmentSeconds": segment_seconds})
        write_manifest(manifest_location, manifest)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
    logger.info({"message": "MEDIA extract complete", "fileClass": manifest["fileClass"],
                 "renderImageCount": len(render_keys), "renderSkipped": manifest["renderSkipped"],
                 "warningCount": len(manifest["warnings"]), "fullTextChars": manifest.get("fullTextChars", 0),
                 "fullTextSkipped": manifest.get("fullTextSkipped"),
                 "videoSegmentCount": segment_state.get("videoSegmentCount", 0)})
    # The whole state goes back so a LambdaInvoke with outputPath "$.Payload" keeps every hop field
    # (assetId, databaseId, relativePath, versionId, outputS3Asset*Path, ...) for GenerateMetadataTask; the
    # segment fields ride on it only when a plan exists, which is what VideoSegmentChoice routes on.
    return {
        **payload,
        "analysisManifestS3Location": manifest_location,
        "fileClass": manifest["fileClass"],
        "renderBranch": RENDER_BRANCH,
        "renderSkipped": manifest["renderSkipped"],
        "renderImageCount": len(render_keys),
        "warningCount": len(manifest["warnings"]),
        **segment_state,
    }

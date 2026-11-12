#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import datetime
import hashlib
import os
import boto3
from botocore.config import Config
from customLogging.logger import safeLogger
import executionLogKeys
import manifestHelper
import fileClassifier
import analysisCommon as common

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline lambda
# runs against throttling-prone services (Step Functions, Amazon S3, EventBridge) for the length of
# a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = safeLogger(service="SystemGenAiMetadataConstructPipeline")

s3_client = boto3.client('s3', config=retry_config)

VECTOR_SEARCH_ENABLED = os.environ.get("VECTOR_SEARCH_ENABLED", "false").strip().lower() == "true"
# The Bedrock analysis layer. Off, the pipeline is attributes-only: every branch still extracts, no branch
# renders images (they exist only as model input), and generateMetadata makes no Converse call.
GENAI_ANALYSIS_ENABLED = os.environ.get("GENAI_ANALYSIS_ENABLED", "true").strip().lower() == "true"
# Whether the analysis model takes image input. False renders no views: the analysis gets the attributes and
# text only, and a video gets no per-window analysis (each window is described from its frames).
ANALYSIS_MODEL_SUPPORTS_IMAGES = common.env_flag(os.environ, "ANALYSIS_MODEL_SUPPORTS_IMAGES", True)
USE_FARGATE_RENDERER = os.environ.get("USE_FARGATE_RENDERER", "false").strip().lower() == "true"
MAX_INPUT_FILE_SIZE_MB = int(os.environ.get("MAX_INPUT_FILE_SIZE_MB", "2048"))
MAX_POINT_CLOUD_POINTS = int(os.environ.get("MAX_POINT_CLOUD_POINTS", "20000000"))

# sys_file.sha256 is computed by streaming the object; above this size the digest is omitted.
SHA256_MAX_BYTES = 512 * 1024 * 1024
_HASH_CHUNK_BYTES = 8 * 1024 * 1024
# The object's user metadata (x-amz-meta-*) is recorded as sys_file.s3Metadata, bounded per value.
S3_METADATA_MAX_KEYS = 50
S3_METADATA_VALUE_MAX_CHARS = 500

# Template values copied into the state for the branch handlers that read them from their event; the
# defaults match the default template's tag values.
DEFAULT_RENDER_VIEWS = 8
DEFAULT_MAX_TEXT_CHARS = 12000
DEFAULT_VIDEO_SEGMENT_SECONDS = 0
DEFAULT_CONTENT_CHUNKING = True
# Template tag RESOLVE_DEPENDENCIES arrives under this key. A configuration rendered from a template written
# before the tag existed carries INCLUDE_SIBLING_FILES' key instead, which is read when the new one is absent
# so a customised template that turned the walk off keeps it off.
RESOLVE_DEPENDENCIES_KEY = "resolveDependencies"
LEGACY_RESOLVE_DEPENDENCIES_KEY = "includeSiblingFiles"


def _version_kwargs(version_id):
    """``VersionId`` for a read of the input at the run's version. ``"null"`` is the version S3 reports
    for an object written while the bucket's versioning was off; S3 accepts it on a read, and reading it
    keeps a newer upload from replacing the file the run describes. Only ``""`` reads the current object."""
    if version_id:
        return {"VersionId": version_id}
    return {}


def _head(bucket, key, version_id, s3=None):
    return (s3 or s3_client).head_object(Bucket=bucket, Key=key, **_version_kwargs(version_id))


def _sniffer(bucket, key, version_id, s3=None):
    """A header reader for the classifier: one ranged GET, memoised so the classification and the
    point-count check share it. An unreadable header classifies the file as ``other``."""
    cache = {}
    client = s3 or s3_client

    def sniff(nbytes):
        if nbytes in cache:
            return cache[nbytes]
        try:
            response = client.get_object(Bucket=bucket, Key=key, Range=f"bytes=0-{nbytes - 1}",
                                         **_version_kwargs(version_id))
            data = response["Body"].read()
        except Exception as e:
            logger.warning(f"Header read failed for s3://{bucket}/{key}: {e}")
            data = b""
        cache[nbytes] = data
        return data

    return sniff


def _sha256(bucket, key, version_id, s3=None):
    """The object's SHA-256, streamed in chunks; ``None`` when the object cannot be read, so a digest
    that could not be taken is absent rather than a failed run."""
    digest = hashlib.sha256()
    try:
        body = (s3 or s3_client).get_object(Bucket=bucket, Key=key, **_version_kwargs(version_id))["Body"]
        for chunk in body.iter_chunks(_HASH_CHUNK_BYTES):
            digest.update(chunk)
    except Exception as e:
        logger.warning(f"sha256 skipped for s3://{bucket}/{key}: {e}")
        return None
    return digest.hexdigest()


def _input_client(region):
    """The client that reads the input file: the default client for a bucket in the deployment Region
    (an empty Region, or a state from an older Lambda), a client for ``region`` otherwise."""
    return manifestHelper.s3_client_for_region(region) if region else s3_client


def bucket_regions(event) -> dict:
    """Bucket name -> Region for the asset buckets this run reads and writes, from the Regions the
    vamsExecute Lambda resolved from the manifest. The containers sign each listed bucket's requests
    with a client for that Region. A bucket in the deployment Region (an empty Region, or a state from
    an older Lambda) is left out: requests for it use the default client, as do the auxiliary bucket's."""
    regions = {}
    for path_key, region_key in (("inputS3AssetFilePath", "inputBucketRegion"),
                                 ("outputS3AssetFilesPath", "outputBucketRegion")):
        uri, region = event.get(path_key) or "", event.get(region_key) or ""
        if uri.startswith("s3://") and region:
            regions[uri[len("s3://"):].split("/", 1)[0]] = region
    return regions


def _iso_timestamp(value):
    """An ISO-8601 UTC text for a datetime the S3 client returned, or None."""
    if value is None:
        return None
    try:
        return value.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (AttributeError, ValueError):
        return str(value)


def s3_user_metadata(head):
    """The object's user metadata as a bounded, sorted dict (``x-amz-meta-`` keys without the prefix,
    which is how S3 returns them); ``{}`` when the object carries none."""
    metadata = head.get("Metadata") or {}
    recorded = {}
    for key in sorted(metadata)[:S3_METADATA_MAX_KEYS]:
        value = metadata[key]
        if value is None or not str(value).strip():
            continue
        recorded[str(key)] = str(value)[:S3_METADATA_VALUE_MAX_CHARS]
    return recorded


def build_sys_file(name, ext, head, version_id, sha256_hex, detected_format=None):
    """The ``sys_file`` attribute every file class carries: the object's own facts (name, extension, size,
    content type, ETag, version, last-modified time), the user metadata stored on it, the format its
    leading bytes identify, and the digest when it was taken."""
    sys_file = {
        "name": name,
        "ext": ext,
        "sizeBytes": int(head.get("ContentLength", 0) or 0),
        "contentType": head.get("ContentType", "") or "",
        "etag": (head.get("ETag", "") or "").strip('"'),
        "versionId": version_id or "",
    }
    last_modified = _iso_timestamp(head.get("LastModified"))
    if last_modified:
        sys_file["lastModified"] = last_modified
    user_metadata = s3_user_metadata(head)
    if user_metadata:
        sys_file["s3Metadata"] = user_metadata
    if detected_format:
        sys_file["detectedFormat"] = detected_format
    if sha256_hex:
        sys_file["sha256"] = sha256_hex
    return sys_file


def fargate_reads_region(region):
    """Whether the Fargate render job, which runs in the VPC's isolated subnets, can read a bucket in
    ``region``: the deployment Region (``""`` or ``AWS_REGION``) through the S3 gateway endpoint, or a
    Region in ``CROSS_REGION_S3_ENDPOINT_REGIONS`` through the VPC's cross-Region S3 interface endpoint."""
    return (not region or region == os.environ.get("AWS_REGION", "")
            or region in manifestHelper.cross_region_s3_endpoint_regions())


def fargate_is_unreachable(file_class, input_region):
    """True when an over-limit file would go to the Fargate renderer but its bucket's Region is one the
    VPC cannot reach, so the size gate keeps it on NONE instead."""
    return (USE_FARGATE_RENDERER and file_class != fileClassifier.CLASS_OTHER
            and not fargate_reads_region(input_region))


def apply_size_gate(file_class, render_branch, size_bytes, point_count, input_region=""):
    """``(renderBranch, renderSkipped)`` after the Lambda limits. A class that never renders keeps
    NONE with ``unsupported``; a renderable file over a limit goes to Fargate when the operator enabled
    that branch and the job can read the file's bucket Region (``input_region``), else to NONE with
    ``size``: no Lambda branch takes a file over the limits, so the file is described from its attributes.
    An ``other`` file over the limit is never sent to Fargate: the generic probe reads a header the Lambda
    could read as well, so it keeps NONE with ``size``."""
    if render_branch == fileClassifier.BRANCH_NONE:
        return render_branch, common.RENDER_SKIPPED_UNSUPPORTED
    over_size = size_bytes > MAX_INPUT_FILE_SIZE_MB * 1024 * 1024
    over_points = (file_class == fileClassifier.CLASS_POINTCLOUD and point_count is not None
                   and point_count > MAX_POINT_CLOUD_POINTS)
    if not (over_size or over_points):
        return render_branch, None
    if (USE_FARGATE_RENDERER and file_class != fileClassifier.CLASS_OTHER
            and fargate_reads_region(input_region)):
        return fileClassifier.BRANCH_FARGATE, None
    return fileClassifier.BRANCH_NONE, common.RENDER_SKIPPED_SIZE


def resolve_dependencies(config):
    """Whether the render branches load the files a scene references: the configuration's
    ``resolveDependencies``, else its earlier ``includeSiblingFiles``, else true."""
    value = config.get(RESOLVE_DEPENDENCIES_KEY)
    if value is None:
        value = config.get(LEGACY_RESOLVE_DEPENDENCIES_KEY)
    return common.as_bool(value, True)


def lambda_handler(event, context):
    """
    ConstructPipeline
    Classifies the input file, applies the Lambda limits, writes the analysis manifest with sys_file,
    and returns the pipeline state the render branches and the analysis steps read.
    """

    # VAMS reads this function's shared log group for the run's execution ids, so they go on every
    # line; the previous invocation's ids are removed first (see executionLogKeys).
    executionLogKeys.bind_execution_ids(logger, event)

    # Identifiers only: the state carries externalSfnTaskToken, so it is never rendered whole.
    logger.info("Event", jobName=event.get("jobName", ""), assetId=event.get("assetId", ""),
                databaseId=event.get("databaseId", ""),
                workflowExecutionId=event.get("workflowExecutionId", ""),
                inputS3AssetFilePath=event.get("inputS3AssetFilePath", ""), eventKeys=sorted(event))
    logger.info(f"Context: {context}")

    bucket, key = manifestHelper.parse_s3_uri(event["inputS3AssetFilePath"])
    aux_bucket, aux_prefix = manifestHelper.parse_s3_uri(event["inputOutputS3AssetAuxiliaryFilesPath"])
    config = manifestHelper.fetch_input_configuration(s3_client, event.get("inputConfigurationS3Location", ""))
    version_id = event.get("versionId", "") or ""
    input_region = event.get("inputBucketRegion", "") or ""
    input_s3 = _input_client(input_region)
    head = _head(bucket, key, version_id, input_s3)
    version_id = version_id or (head.get("VersionId", "") or "")

    name = key.rsplit("/", 1)[-1]
    ext = os.path.splitext(name)[1].lower()
    file_ext = ext or "none"
    size_bytes = int(head.get("ContentLength", 0) or 0)

    sniff = _sniffer(bucket, key, version_id, input_s3)
    file_class, natural_branch = fileClassifier.classify(ext, sniff)

    point_count = None
    if file_class == fileClassifier.CLASS_POINTCLOUD and natural_branch != fileClassifier.BRANCH_NONE:
        point_count = fileClassifier.point_count_from_header(ext, sniff(fileClassifier.SNIFF_BYTES))

    render_branch, render_skipped = apply_size_gate(file_class, natural_branch, size_bytes, point_count,
                                                    input_region)

    sha256_hex = _sha256(bucket, key, version_id, input_s3) if size_bytes <= SHA256_MAX_BYTES else None
    sys_file = build_sys_file(name, ext, head, version_id, sha256_hex,
                              fileClassifier.detect_format(sniff(fileClassifier.SNIFF_BYTES)))

    manifest = common.new_analysis_manifest(file_class, render_branch, sys_file, render_skipped)
    if render_skipped == common.RENDER_SKIPPED_SIZE:
        manifest["warnings"].append(
            f"render skipped: the file exceeds the Lambda limits (sizeBytes={size_bytes}, "
            f"points={point_count}, maxInputFileSizeMb={MAX_INPUT_FILE_SIZE_MB}, "
            f"maxPointCloudPoints={MAX_POINT_CLOUD_POINTS})")
        if fargate_is_unreachable(file_class, input_region):
            manifest["warnings"].append(
                f"Fargate renderer not used: it runs in the VPC, which has no cross-Region Amazon S3 endpoint "
                f"for {input_region}, where the file's bucket is; the file is described from its attributes")
    manifest_uri = common.uri_join(event["inputOutputS3AssetAuxiliaryFilesPath"],
                                   common.ANALYSIS_MANIFEST_FILENAME)
    common.write_json(s3_client, manifest_uri, manifest)
    logger.info(f"Analysis manifest written: {manifest_uri} ({file_class}/{render_branch})")

    state = dict(event)
    state.update({
        "pipelineExecutionId": manifestHelper.pipeline_execution_id_from_event_prefix(
            event.get("orchestrationEventPrefix", "")),
        "versionId": version_id,
        "etag": sys_file["etag"],
        "fileSize": size_bytes,
        "contentType": sys_file["contentType"],
        "fileClass": file_class,
        "fileExt": file_ext,
        "renderBranch": render_branch,
        "renderSkipped": render_skipped,
        "analysisManifestS3Location": manifest_uri,
        "vectorSearchEnabled": VECTOR_SEARCH_ENABLED,
        "genAiAnalysisEnabled": GENAI_ANALYSIS_ENABLED,
        "analysisModelSupportsImages": ANALYSIS_MODEL_SUPPORTS_IMAGES,
        "status": "STARTING",
        # Split locations and template values for the branch handlers, which receive the whole state
        # as their event (a state machine cannot read S3).
        "inputBucket": bucket,
        "inputKey": key,
        "auxBucket": aux_bucket,
        "auxTempPrefix": aux_prefix,
        # Bucket name -> Region for the asset buckets outside the deployment Region; the branch
        # handlers sign each listed bucket's requests for its Region.
        "bucketRegions": bucket_regions(event),
        "renderViews": common.as_int(config.get("renderViews"), DEFAULT_RENDER_VIEWS),
        "maxTextChars": common.as_int(config.get("maxTextChars"), DEFAULT_MAX_TEXT_CHARS),
        RESOLVE_DEPENDENCIES_KEY: resolve_dependencies(config),
        "extractGeoLocation": common.as_bool(config.get("extractGeoLocation"), True),
        "videoSegmentSeconds": common.as_int(config.get("videoSegmentSeconds"), DEFAULT_VIDEO_SEGMENT_SECONDS),
        "contentChunking": common.as_bool(config.get("contentChunking"), DEFAULT_CONTENT_CHUNKING),
        "maxPointCloudPoints": MAX_POINT_CLOUD_POINTS,
        # Renders (3D views, keyframes, page rasters) exist only as analysis-model input, so the branches
        # produce them only when the GenAI layer is on and its model takes images.
        "renderImages": GENAI_ANALYSIS_ENABLED and ANALYSIS_MODEL_SUPPORTS_IMAGES,
    })
    # The classification outcome by its keys; the state itself carries externalSfnTaskToken.
    logger.info("State", fileClass=state.get("fileClass"), renderBranch=state.get("renderBranch"),
                renderSkipped=state.get("renderSkipped"),
                analysisManifestS3Location=state.get("analysisManifestS3Location"),
                vectorSearchEnabled=state.get("vectorSearchEnabled"),
                genAiAnalysisEnabled=state.get("genAiAnalysisEnabled"),
                renderImages=state.get("renderImages"),
                detectedFormat=sys_file.get("detectedFormat"), stateKeys=sorted(state))
    return state

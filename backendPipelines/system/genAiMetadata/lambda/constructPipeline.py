#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import hashlib
import os
import boto3
from botocore.config import Config
from customLogging.logger import safeLogger
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
USE_FARGATE_RENDERER = os.environ.get("USE_FARGATE_RENDERER", "false").strip().lower() == "true"
MAX_INPUT_FILE_SIZE_MB = int(os.environ.get("MAX_INPUT_FILE_SIZE_MB", "2048"))
MAX_POINT_CLOUD_POINTS = int(os.environ.get("MAX_POINT_CLOUD_POINTS", "20000000"))

# sys_file.sha256 is computed by streaming the object; above this size the digest is omitted.
SHA256_MAX_BYTES = 512 * 1024 * 1024
_HASH_CHUNK_BYTES = 8 * 1024 * 1024

# Template values copied into the state for the branch handlers that read them from their event; the
# defaults match the default template's tag values.
DEFAULT_RENDER_VIEWS = 8
DEFAULT_MAX_TEXT_CHARS = 12000
DEFAULT_VIDEO_SEGMENT_SECONDS = 0
DEFAULT_CONTENT_CHUNKING = True


def _version_kwargs(version_id):
    if version_id and version_id != "null":
        return {"VersionId": version_id}
    return {}


def _head(bucket, key, version_id):
    return s3_client.head_object(Bucket=bucket, Key=key, **_version_kwargs(version_id))


def _sniffer(bucket, key, version_id):
    """A header reader for the classifier: one ranged GET, memoised so the classification and the
    point-count check share it. An unreadable header classifies the file as ``other``."""
    cache = {}

    def sniff(nbytes):
        if nbytes in cache:
            return cache[nbytes]
        try:
            response = s3_client.get_object(Bucket=bucket, Key=key, Range=f"bytes=0-{nbytes - 1}",
                                            **_version_kwargs(version_id))
            data = response["Body"].read()
        except Exception as e:
            logger.warning(f"Header read failed for s3://{bucket}/{key}: {e}")
            data = b""
        cache[nbytes] = data
        return data

    return sniff


def _sha256(bucket, key, version_id):
    """The object's SHA-256, streamed in chunks; ``None`` when the object cannot be read, so a digest
    that could not be taken is absent rather than a failed run."""
    digest = hashlib.sha256()
    try:
        body = s3_client.get_object(Bucket=bucket, Key=key, **_version_kwargs(version_id))["Body"]
        for chunk in body.iter_chunks(_HASH_CHUNK_BYTES):
            digest.update(chunk)
    except Exception as e:
        logger.warning(f"sha256 skipped for s3://{bucket}/{key}: {e}")
        return None
    return digest.hexdigest()


def build_sys_file(name, ext, head, version_id, sha256_hex):
    """The ``sys_file`` attribute every file class carries."""
    sys_file = {
        "name": name,
        "ext": ext,
        "sizeBytes": int(head.get("ContentLength", 0) or 0),
        "contentType": head.get("ContentType", "") or "",
        "etag": (head.get("ETag", "") or "").strip('"'),
        "versionId": version_id or "",
    }
    if sha256_hex:
        sys_file["sha256"] = sha256_hex
    return sys_file


def apply_size_gate(file_class, render_branch, size_bytes, point_count):
    """``(renderBranch, renderSkipped)`` after the Lambda limits. A class that never renders keeps
    NONE with ``unsupported``; a renderable file over a limit goes to Fargate when the operator enabled
    that branch, else to NONE with ``size``."""
    if render_branch == fileClassifier.BRANCH_NONE:
        return render_branch, common.RENDER_SKIPPED_UNSUPPORTED
    over_size = size_bytes > MAX_INPUT_FILE_SIZE_MB * 1024 * 1024
    over_points = (file_class == fileClassifier.CLASS_POINTCLOUD and point_count is not None
                   and point_count > MAX_POINT_CLOUD_POINTS)
    if not (over_size or over_points):
        return render_branch, None
    if USE_FARGATE_RENDERER:
        return fileClassifier.BRANCH_FARGATE, None
    return fileClassifier.BRANCH_NONE, common.RENDER_SKIPPED_SIZE


def lambda_handler(event, context):
    """
    ConstructPipeline
    Classifies the input file, applies the Lambda limits, writes the analysis manifest with sys_file,
    and returns the pipeline state the render branches and the analysis steps read.
    """

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
    head = _head(bucket, key, version_id)
    version_id = version_id or (head.get("VersionId", "") or "")

    name = key.rsplit("/", 1)[-1]
    ext = os.path.splitext(name)[1].lower()
    file_ext = ext or "none"
    size_bytes = int(head.get("ContentLength", 0) or 0)

    sniff = _sniffer(bucket, key, version_id)
    file_class, natural_branch = fileClassifier.classify(ext, sniff)

    point_count = None
    if file_class == fileClassifier.CLASS_POINTCLOUD and natural_branch != fileClassifier.BRANCH_NONE:
        point_count = fileClassifier.point_count_from_header(ext, sniff(fileClassifier.SNIFF_BYTES))

    render_branch, render_skipped = apply_size_gate(file_class, natural_branch, size_bytes, point_count)

    sha256_hex = _sha256(bucket, key, version_id) if size_bytes <= SHA256_MAX_BYTES else None
    sys_file = build_sys_file(name, ext, head, version_id, sha256_hex)

    manifest = common.new_analysis_manifest(file_class, render_branch, sys_file, render_skipped)
    if render_skipped == common.RENDER_SKIPPED_SIZE:
        manifest["warnings"].append(
            f"render skipped: the file exceeds the Lambda limits (sizeBytes={size_bytes}, "
            f"points={point_count}, maxInputFileSizeMb={MAX_INPUT_FILE_SIZE_MB}, "
            f"maxPointCloudPoints={MAX_POINT_CLOUD_POINTS})")
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
        "status": "STARTING",
        # Split locations and template values for the branch handlers, which receive the whole state
        # as their event (a state machine cannot read S3).
        "inputBucket": bucket,
        "inputKey": key,
        "auxBucket": aux_bucket,
        "auxTempPrefix": aux_prefix,
        "renderViews": common.as_int(config.get("renderViews"), DEFAULT_RENDER_VIEWS),
        "maxTextChars": common.as_int(config.get("maxTextChars"), DEFAULT_MAX_TEXT_CHARS),
        "includeSiblingFiles": common.as_bool(config.get("includeSiblingFiles"), True),
        "extractGeoLocation": common.as_bool(config.get("extractGeoLocation"), True),
        "videoSegmentSeconds": common.as_int(config.get("videoSegmentSeconds"), DEFAULT_VIDEO_SEGMENT_SECONDS),
        "contentChunking": common.as_bool(config.get("contentChunking"), DEFAULT_CONTENT_CHUNKING),
        "maxPointCloudPoints": MAX_POINT_CLOUD_POINTS,
        "render": True,
    })
    # The classification outcome by its keys; the state itself carries externalSfnTaskToken.
    logger.info("State", fileClass=state.get("fileClass"), renderBranch=state.get("renderBranch"),
                renderSkipped=state.get("renderSkipped"),
                analysisManifestS3Location=state.get("analysisManifestS3Location"),
                vectorSearchEnabled=state.get("vectorSearchEnabled"), stateKeys=sorted(state))
    return state

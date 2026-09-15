#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Embedding step of the SYSTEM GenAI metadata pipeline.

Composes the text that represents one file version — the asset context, the file identity with its
file-type phrase (``fileClassifier.FILE_CLASS_PHRASES``), the ``genai_*`` rows, the attribute facts,
the file's, asset's and database's existing metadata and the file's non-pipeline attributes
(``analysisCommon.existing_metadata_lines``, always), and the text excerpt last so a model window cuts
it first — embeds it through the vendored adapter, writes the embedding document to the auxiliary
bucket, and publishes ``vector.embedding.ready`` on the orchestration bus for the vector indexer — the
contract any pipeline may fulfil. The whole-file document is one vector per file version and carries
the segment fields at their whole-file defaults. A caught Bedrock failure is recorded through
``execution.status.json`` and the handler returns normally.

The event carries the manifest's ``bucketId`` as given. A manifest built from an earlier workflow step's
outputs carries an empty one, and the indexer then resolves the file's bucket from the asset row, so the
embedding is produced and published regardless.
"""

import datetime
import hashlib
import json
import os
import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from customLogging.logger import safeLogger
import manifestHelper
import analysisCommon as common
import contentChunks
import fileClassifier
import metadataCatalog
from vectorsearch import embeddings

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline lambda
# runs against throttling-prone services (Step Functions, Amazon S3, EventBridge) for the length of
# a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = safeLogger(service="SystemGenAiMetadataGenerateEmbedding")

s3_client = boto3.client('s3', config=retry_config)
events_client = boto3.client('events', region_name=os.environ["AWS_REGION"], config=retry_config)
bedrock_runtime = boto3.client('bedrock-runtime', config=retry_config)

EMBEDDING_MODEL_ID = os.environ["EMBEDDING_MODEL_ID"]
EMBEDDING_DIMENSIONS = int(os.environ["EMBEDDING_DIMENSIONS"])
ORCHESTRATION_BUS_NAME = os.environ.get("ORCHESTRATION_BUS_NAME", "")

EMBEDDING_READY_DETAIL_TYPE = "vector.embedding.ready"
EMBEDDING_DOCUMENT_PREFIX = "embedding/"
SOURCE_TEXT_STORED_MAX_CHARS = 8000
METADATA_FILE_SUFFIX = ".metadata.json"
EMBEDDING_SUMMARY_FILENAME = "summary.json"
# EventBridge accepts at most ten entries per PutEvents call.
PUT_EVENTS_BATCH_SIZE = 10
CONTENT_CHUNK_COUNT_KEY = "genai_content_chunk_count"
TYPE_NUMBER = "number"
# The code the segment child records for a failed PutEvents; the chunk loop records its own the same way,
# and only the whole-file event raises.
ERROR_SEGMENT_PUBLISH = "SegmentPublishError"

# Segment identity of the whole-file document: one vector per file version, no time range, no chunk.
WHOLE_FILE_SEGMENT_FIELDS = {
    "segmentKey": "",
    "segmentKind": "none",
    "segmentLabel": "",
    "segmentStartMs": None,
    "segmentEndMs": None,
    "segmentCount": 0,
}

MODALITY_ASSET_METADATA = "asset-metadata"
MODALITY_FILE_IDENTITY = "file-identity"
MODALITY_GENAI_METADATA = "genai-metadata"
MODALITY_FILE_ATTRIBUTES = "file-attributes"
MODALITY_EXISTING_FILE_METADATA = "existing-file-metadata"
MODALITY_EXISTING_ASSET_METADATA = "existing-asset-metadata"
MODALITY_EXISTING_DATABASE_METADATA = "existing-database-metadata"
MODALITY_EXISTING_FILE_ATTRIBUTES = "existing-file-attributes"
MODALITY_FILE_TEXT = "file-text"
# Modality label per existing-metadata scope of the legacy view (analysisCommon.EXISTING_METADATA_SCOPES).
EXISTING_MODALITY_BY_SCOPE = {
    "fileMetadata": MODALITY_EXISTING_FILE_METADATA,
    "assetMetadata": MODALITY_EXISTING_ASSET_METADATA,
    "databaseMetadata": MODALITY_EXISTING_DATABASE_METADATA,
    "fileAttributes": MODALITY_EXISTING_FILE_ATTRIBUTES,
}

# The genai_* rows that carry prose or lists, in the order they contribute to the source text.
GENAI_TEXT_KEYS = ("genai_title", "genai_description", "genai_keywords", "genai_category", "genai_subcategory",
                   "genai_style", "genai_materials", "genai_colors", "genai_objects", "genai_size_estimate",
                   "genai_orientation", "genai_text_summary")


def file_version_key(relative_path, version_id):
    return f"{relative_path}#{version_id or 'null'}"


# Promoted ext_* items that read as a fact under a plain label (counts, resolution, pages, camera, CRS).
DERIVED_FACT_LABELS = (
    ("ext_vertex_count", "vertices"), ("ext_face_count", "faces"), ("ext_triangle_count", "triangles"),
    ("ext_mesh_count", "meshes"), ("ext_point_count", "points"), ("ext_resolution", "resolution"),
    ("ext_page_count", "pages"), ("ext_camera", "camera"), ("ext_crs", "crs"),
)


def derived_facts(attributes, file_class):
    """Human-readable facts read off the promoted ext_* items — dimensions with units, units, counts,
    duration, resolution, pages, camera, CRS — so the source text carries them even when the branch
    wrote no ``facts``."""
    promoted = {item["metadataKey"]: item["metadataValue"]
                for item in metadataCatalog.promote(attributes, file_class)}
    facts = {}
    units = promoted.get("ext_units")
    if "ext_dimensions" in promoted:
        xyz = json.loads(promoted["ext_dimensions"])
        size = " x ".join(f"{xyz[axis]:g}" for axis in ("x", "y", "z"))
        facts["dimensions"] = f"{size} {units}" if units else size
    if units:
        facts["units"] = units
    for key, label in DERIVED_FACT_LABELS:
        if key in promoted:
            facts[label] = promoted[key]
    if "ext_duration_seconds" in promoted:
        facts["duration"] = f"{promoted['ext_duration_seconds']} s"
    return facts


def embedding_document_key(aux_temp_prefix, relative_path, version_id, segment_key=""):
    """The aux object of a document: the whole-file document hashes the file version key; a segment document
    hashes that key followed by ``#`` and its segment key."""
    identity = file_version_key(relative_path, version_id)
    if segment_key:
        identity = f"{identity}#{segment_key}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return common.join_key(aux_temp_prefix, EMBEDDING_DOCUMENT_PREFIX + digest + ".json")


def genai_values(metadata_file_body):
    """``{metadataKey: metadataValue}`` from the .metadata.json body the analysis step wrote."""
    return {row.get("metadataKey"): row.get("metadataValue")
            for row in (metadata_file_body or {}).get("metadata") or [] if row.get("metadataKey")}


def read_text_object(uri):
    bucket, key = manifestHelper.parse_s3_uri(uri)
    return s3_client.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")


def chunk_failure_code(exc):
    """The execution error code for a caught chunk embedding failure, mapped as the whole-file path maps its
    own: BedrockAccessDenied for AccessDeniedException, BedrockThrottled for a throttle code,
    BedrockEmbeddingError for every other cause. The adapter carries the Bedrock code on
    EmbeddingModelError.code and lets throttling ClientErrors propagate."""
    code = getattr(exc, "code", "") or (
        ((getattr(exc, "response", None) or {}).get("Error") or {}).get("Code", ""))
    if code == "AccessDeniedException":
        return common.ERROR_BEDROCK_ACCESS_DENIED
    if code in common.THROTTLE_ERROR_CODES:
        return common.ERROR_BEDROCK_THROTTLED
    return common.ERROR_BEDROCK_EMBEDDING


def load_chunks(manifest, config):
    """``(chunks, dropped)`` of the captured full text when the template's contentChunking is on and the branch
    captured text; ``([], 0)`` otherwise — chunking off, a manifest whose fullTextSkipped names why the branch
    captured nothing, or no text location."""
    if (not common.as_bool(config.get("contentChunking"), True) or manifest.get("fullTextSkipped")
            or not manifest.get("fullTextS3Location")):
        return [], 0
    full_text = read_text_object(manifest["fullTextS3Location"])
    page_offsets = []
    if manifest.get("pageOffsetsS3Location"):
        loaded = json.loads(read_text_object(manifest["pageOffsetsS3Location"]))
        page_offsets = loaded if isinstance(loaded, list) else []
    return contentChunks.chunk_text(full_text, page_offsets)


def compose_chunk_source_text(asset_name, phrase, relative_path, genai_title, label, chunk_body):
    """``(sourceText, sourceModalities)`` of one chunk: the asset name; the file-type phrase and path; the whole
    file's title; the chunk label and the chunk text — one line per part, each distinct string once, and a
    modality label for each part that contributed."""
    parts = []
    modalities = []
    seen = set()

    def add(part, modality):
        text = " ".join(str(part).split()) if part is not None else ""
        if not text or text.lower() in seen:
            return
        seen.add(text.lower())
        parts.append(text)
        if modality not in modalities:
            modalities.append(modality)

    add(asset_name, MODALITY_ASSET_METADATA)
    add(phrase, MODALITY_FILE_IDENTITY)
    add(relative_path, MODALITY_FILE_IDENTITY)
    add(genai_title, MODALITY_GENAI_METADATA)
    add(label, MODALITY_FILE_TEXT)
    add(chunk_body, MODALITY_FILE_TEXT)
    return "\n".join(parts), modalities


def segment_document(document, *, embedding, source_text, modalities, segment_key, segment_kind, segment_label,
                     segment_start_ms, segment_end_ms, segment_count):
    """A segment document: the whole-file document with its own vector, text and modalities and the six segment
    fields filled in."""
    return {
        **document,
        "embedding": embedding,
        "sourceText": source_text[:SOURCE_TEXT_STORED_MAX_CHARS],
        "sourceModalities": modalities,
        "segmentKey": segment_key,
        "segmentKind": segment_kind,
        "segmentLabel": segment_label,
        "segmentStartMs": segment_start_ms,
        "segmentEndMs": segment_end_ms,
        "segmentCount": segment_count,
    }


def append_metadata_row(metadata_file_uri, row):
    """Appends one row to the .metadata.json the analysis step wrote (replacing an earlier row of the same key);
    a missing file is created with that row alone."""
    try:
        body = common.read_json(s3_client, metadata_file_uri)
    except (ClientError, ValueError):
        body = {"type": "metadata", "updateType": "update", "metadata": []}
    rows = [existing for existing in body.get("metadata") or [] if existing.get("metadataKey") != row["metadataKey"]]
    rows.append(row)
    body["metadata"] = rows
    common.write_json(s3_client, metadata_file_uri, body)


def write_embedding_summary(aux_bucket, aux_prefix, body):
    key = common.join_key(aux_prefix, EMBEDDING_DOCUMENT_PREFIX + EMBEDDING_SUMMARY_FILENAME)
    return common.write_json(s3_client, f"s3://{aux_bucket}/{key}", body)


def compose_source_text(asset_data, database_id, relative_path, file_class, genai, facts, existing_lines,
                        text_excerpt):
    """``(sourceText, sourceModalities)``: the parts in order — asset context, file identity (database id,
    the file-type phrase, path and class), the genai_* rows, the attribute facts, the existing metadata
    scope by scope, and the text excerpt last, so a model window cuts the one unbounded part first —
    whitespace-normalised, each distinct string kept once, one line per part; the modalities list each
    label whose part contributed, in that order."""
    parts = []
    modalities = []
    seen = set()

    def add(part, modality):
        text = " ".join(str(part).split()) if part is not None else ""
        if not text or text.lower() in seen:
            return
        seen.add(text.lower())
        parts.append(text)
        if modality not in modalities:
            modalities.append(modality)

    add(asset_data.get("assetName"), MODALITY_ASSET_METADATA)
    add(asset_data.get("description"), MODALITY_ASSET_METADATA)
    for tag in asset_data.get("tags") or []:
        add(tag, MODALITY_ASSET_METADATA)
    add(database_id, MODALITY_FILE_IDENTITY)
    add(fileClassifier.FILE_CLASS_PHRASES.get(file_class, fileClassifier.FILE_CLASS_PHRASES[fileClassifier.CLASS_OTHER]),
        MODALITY_FILE_IDENTITY)
    add(f"{relative_path} ({file_class})", MODALITY_FILE_IDENTITY)
    for key in GENAI_TEXT_KEYS:
        add(genai.get(key), MODALITY_GENAI_METADATA)
    for key, value in sorted((facts or {}).items()):
        add(f"{key}: {value}", MODALITY_FILE_ATTRIBUTES)
    for scope, line in existing_lines:
        add(line, EXISTING_MODALITY_BY_SCOPE[scope])
    if text_excerpt:
        add(text_excerpt, MODALITY_FILE_TEXT)
    return "\n".join(parts), modalities


def _publish(detail, source):
    if not ORCHESTRATION_BUS_NAME or not source:
        logger.warning("Orchestration bus or event source not configured; the embedding document was "
                       "written but no vector.embedding.ready event is published")
        return False
    response = events_client.put_events(Entries=[{
        "EventBusName": ORCHESTRATION_BUS_NAME,
        "Source": source,
        "DetailType": EMBEDDING_READY_DETAIL_TYPE,
        "Detail": json.dumps(detail),
    }])
    if response.get("FailedEntryCount"):
        raise RuntimeError(f"PutEvents FailedEntryCount={response['FailedEntryCount']}: "
                           f"{response.get('Entries')}")
    return True


class SegmentPublishFailure(Exception):
    """A PutEvents failure while publishing chunk events: ``segment_key`` is the first key of the batch that
    failed and ``published`` the count the call had accepted before it."""

    def __init__(self, message, segment_key, published):
        super().__init__(message)
        self.segment_key = segment_key
        self.published = published


def _publish_entries(details, source):
    """Publishes ``details`` PUT_EVENTS_BATCH_SIZE per call; returns the count published (0 without a bus or
    source). A batch that fails raises SegmentPublishFailure, which the chunk loop records as a caught failure."""
    if not details:
        return 0
    if not ORCHESTRATION_BUS_NAME or not source:
        logger.warning(f"Orchestration bus or event source not configured; {len(details)} segment documents were "
                       "written but no vector.embedding.ready events are published")
        return 0
    published = 0
    for start in range(0, len(details), PUT_EVENTS_BATCH_SIZE):
        batch = details[start:start + PUT_EVENTS_BATCH_SIZE]
        try:
            response = events_client.put_events(Entries=[{
                "EventBusName": ORCHESTRATION_BUS_NAME,
                "Source": source,
                "DetailType": EMBEDDING_READY_DETAIL_TYPE,
                "Detail": json.dumps(detail),
            } for detail in batch])
        except (ClientError, BotoCoreError) as exc:
            raise SegmentPublishFailure(f"PutEvents failed: {exc}", batch[0]["segmentKey"], published)
        if response.get("FailedEntryCount"):
            raise SegmentPublishFailure(f"PutEvents FailedEntryCount={response['FailedEntryCount']}: "
                                        f"{response.get('Entries')}", batch[0]["segmentKey"], published)
        published += len(batch)
    return published


def lambda_handler(event, context):
    """
    GenerateEmbedding
    Embeds the file version's text, writes the embedding document, and publishes the indexing event.
    Skipped when vector search is off, the analysis step recorded a failure, or the run carries no
    bucket registration id for the indexer to resolve.
    """

    # Identifiers only: the state carries externalSfnTaskToken, so it is never rendered whole.
    logger.info("Event", jobName=event.get("jobName", ""), assetId=event.get("assetId", ""),
                analysisStatus=event.get("analysisStatus", ""),
                vectorSearchEnabled=event.get("vectorSearchEnabled"),
                analysisManifestS3Location=event.get("analysisManifestS3Location", ""),
                eventKeys=sorted(event))
    logger.info(f"Context: {context}")

    if not event.get("vectorSearchEnabled") or event.get("analysisStatus") == common.STATUS_FAILED:
        logger.info("Embedding skipped (vector search disabled or analysis FAILED)")
        event["embeddingStatus"] = common.STATUS_SKIPPED
        return event

    relative_path = event.get("relativePath", "") or ""
    version_id = event.get("versionId", "") or ""

    manifest = common.read_json(s3_client, event["analysisManifestS3Location"])
    # The manifest carries the branch's final class (a .json may have been promoted to tiles3d or
    # demoted to other); the state follows it so the document and the summary agree.
    file_class = manifest.get("fileClass") or event.get("fileClass", "") or ""
    event["fileClass"] = file_class
    event["renderBranch"] = manifest.get("renderBranch") or event.get("renderBranch", "") or ""
    config = manifestHelper.fetch_input_configuration(s3_client, event.get("inputConfigurationS3Location", ""))
    metadata_body = manifestHelper.fetch_metadata(s3_client, event.get("inputMetadataS3Location", ""))
    view = manifestHelper.to_legacy_vams_view(metadata_body, event.get("databaseId", ""),
                                              event.get("assetId", ""), relative_path)
    asset_data = (view.get("VAMS") or {}).get("assetData") or {}

    metadata_file_uri = event.get("metadataFileS3Location") or common.uri_join(
        event["outputS3AssetMetadataPath"], relative_path.lstrip("/") + METADATA_FILE_SUFFIX)
    genai = {}
    try:
        genai = genai_values(common.read_json(s3_client, metadata_file_uri))
    except Exception as e:
        logger.warning(f"No genai metadata to embed ({metadata_file_uri}): {e}")

    include_excerpt = common.as_bool(config.get("embeddingIncludeTextExcerpt"), True)
    text_excerpt = (manifest.get("textExcerpt") or "") if include_excerpt else ""
    # Derived facts (dimensions, units, counts, duration, resolution, pages, camera, CRS) first, then
    # the branch's own facts on top.
    facts = derived_facts(manifest.get("attributes") or {}, file_class)
    facts.update(manifest.get("facts") or {})
    # The existing metadata of the four envelope scopes is always embedded; the template's prompt switch
    # does not reach this step.
    existing, dropped = common.existing_metadata_lines(view)
    if dropped:
        logger.warning(f"Existing metadata over the {common.EXISTING_METADATA_MAX_CHARS}-character budget: "
                       f"{len(existing)} lines kept, {dropped} dropped")
    source_text, modalities = compose_source_text(
        asset_data, event.get("databaseId", ""), relative_path, file_class, genai,
        facts, existing, text_excerpt)

    # The captured text is split before the whole-file document is built, so that document carries the true
    # segment count; the chunks themselves are embedded only after the whole-file document is published.
    chunks, chunks_dropped = load_chunks(manifest, config)
    # The branch's reason for capturing nothing (None when it captured, or was never asked to) rides on
    # contentChunks so a reader of the state need not open the manifest.
    chunks_skipped = manifest.get("fullTextSkipped") or None
    if chunks_dropped:
        logger.warning(f"Content chunks over the cap of {contentChunks.CONTENT_CHUNK_MAX}: {len(chunks)} kept, "
                       f"{chunks_dropped} dropped")

    try:
        prepared = embeddings.truncate_for_model(source_text, EMBEDDING_MODEL_ID)
        vector = embeddings.round_vector(embeddings.embed_text(
            prepared, model_id=EMBEDDING_MODEL_ID, dimensions=EMBEDDING_DIMENSIONS, purpose="index",
            client=bedrock_runtime))
    except (ClientError, embeddings.EmbeddingModelError) as e:
        # The adapter carries the Bedrock code on EmbeddingModelError.code and lets throttling
        # ClientErrors propagate; the recorded code names the cause an operator can act on.
        bedrock_code = getattr(e, "code", "") or (
            ((getattr(e, "response", None) or {}).get("Error") or {}).get("Code", ""))
        if bedrock_code == "AccessDeniedException":
            error = common.ERROR_BEDROCK_ACCESS_DENIED
        elif bedrock_code in common.THROTTLE_ERROR_CODES:
            error = common.ERROR_BEDROCK_THROTTLED
        else:
            error = common.ERROR_BEDROCK_EMBEDDING
        logger.error(f"Embedding failed ({error}): {e}")
        common.write_execution_status(s3_client, event["outputS3AssetResultsPath"], error, str(e))
        event["embeddingStatus"] = common.STATUS_FAILED
        event["contentChunks"] = {"count": 0, "dropped": chunks_dropped, "skipped": chunks_skipped}
        return event

    aux_bucket, aux_prefix = manifestHelper.parse_s3_uri(event["inputOutputS3AssetAuxiliaryFilesPath"])
    document_key = embedding_document_key(aux_prefix, relative_path, version_id)
    generated_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    document = {
        "schemaVersion": common.EMBEDDING_DOCUMENT_SCHEMA_VERSION,
        "databaseId": event.get("databaseId", ""),
        "assetId": event.get("assetId", ""),
        "filePath": relative_path,
        "versionId": version_id,
        "contentEtag": event.get("etag", "") or "",
        "bucketId": event.get("bucketId", "") or "",
        "fileClass": file_class,
        # The vector table's form: un-dotted lower-case, "none" when the key has no extension.
        "fileExt": str(event.get("fileExt", "") or "").lstrip(".").lower() or "none",
        "fileSize": int(event.get("fileSize", 0) or 0),
        "contentType": event.get("contentType", "") or "",
        "embeddingModelId": EMBEDDING_MODEL_ID,
        "embeddingDimensions": EMBEDDING_DIMENSIONS,
        "analysisModelId": genai.get("genai_model", "") or "",
        "embedding": vector,
        "sourceText": source_text[:SOURCE_TEXT_STORED_MAX_CHARS],
        "sourceModalities": modalities,
        "pipelineExecutionId": event.get("pipelineExecutionId", "") or "",
        "workflowExecutionId": event.get("workflowExecutionId", "") or "",
        "generatedAt": generated_at,
        **WHOLE_FILE_SEGMENT_FIELDS,
    }
    # One vector per file version plus the segments this run publishes: the chunks below, or the video windows
    # the Map fans out over.
    document["segmentCount"] = len(chunks) or int(event.get("videoSegmentCount") or 0)
    document_uri = common.write_json(s3_client, f"s3://{aux_bucket}/{document_key}", document)
    logger.info(f"Embedding document written: {document_uri}")

    detail = {field: value for field, value in document.items() if field not in ("embedding", "sourceText")}
    detail["documentS3Location"] = document_uri
    source = event.get("orchestrationEventPrefix", "") or ""
    event["embeddingEventPublished"] = _publish(detail, source)
    event["embeddingDocumentS3Location"] = document_uri

    published_chunks = 0
    chunk_error = None
    if chunks:
        phrase = fileClassifier.FILE_CLASS_PHRASES.get(file_class, fileClassifier.FILE_CLASS_PHRASES[fileClassifier.CLASS_OTHER])
        details = []
        segment_key = ""
        try:
            try:
                for chunk in chunks:
                    segment_key = contentChunks.build_text_chunk_key(chunk.index)
                    label = contentChunks.chunk_label(chunk.index, len(chunks), chunk.page)
                    chunk_text, chunk_modalities = compose_chunk_source_text(
                        asset_data.get("assetName"), phrase, relative_path, genai.get("genai_title"), label, chunk.text)
                    chunk_vector = embeddings.round_vector(embeddings.embed_text(
                        embeddings.truncate_for_model(chunk_text, EMBEDDING_MODEL_ID), model_id=EMBEDDING_MODEL_ID,
                        dimensions=EMBEDDING_DIMENSIONS, purpose="index", client=bedrock_runtime))
                    chunk_document = segment_document(
                        document, embedding=chunk_vector, source_text=chunk_text, modalities=chunk_modalities,
                        segment_key=segment_key, segment_kind=contentChunks.SEGMENT_KIND, segment_label=label,
                        segment_start_ms=None, segment_end_ms=None, segment_count=len(chunks))
                    chunk_key = embedding_document_key(aux_prefix, relative_path, version_id, segment_key)
                    chunk_uri = common.write_json(s3_client, f"s3://{aux_bucket}/{chunk_key}", chunk_document)
                    chunk_detail = {field: value for field, value in chunk_document.items()
                                    if field not in ("embedding", "sourceText")}
                    chunk_detail["documentS3Location"] = chunk_uri
                    details.append(chunk_detail)
                    if len(details) == PUT_EVENTS_BATCH_SIZE:
                        published_chunks += _publish_entries(details, source)
                        details = []
            except (ClientError, embeddings.EmbeddingModelError) as e:
                error = chunk_failure_code(e)
                chunk_error = f"chunk {segment_key}: {e}"
                logger.error(f"Content chunk embedding failed ({error}): {chunk_error}")
                common.write_execution_status(s3_client, event["outputS3AssetResultsPath"], error, chunk_error)
            # The chunk documents already written are published; every vector already published stays indexed.
            published_chunks += _publish_entries(details, source)
        except SegmentPublishFailure as e:
            # A failed chunk batch is recorded like a chunk embedding failure, never raised: the whole-file vector
            # and every accepted batch stay indexed, and the status file names where the run ended.
            published_chunks += e.published
            chunk_error = f"chunk {e.segment_key}: {e}"
            logger.error(f"Content chunk events not published ({ERROR_SEGMENT_PUBLISH}): {chunk_error}")
            common.write_execution_status(s3_client, event["outputS3AssetResultsPath"], ERROR_SEGMENT_PUBLISH, chunk_error)
        logger.info(f"Content chunks: {published_chunks} published, {chunks_dropped} dropped")

    event["contentChunks"] = {"count": published_chunks, "dropped": chunks_dropped, "skipped": chunks_skipped}
    write_embedding_summary(aux_bucket, aux_prefix, {
        "schemaVersion": common.EMBEDDING_DOCUMENT_SCHEMA_VERSION,
        "wholeFileDocument": document_uri,
        "contentChunks": event["contentChunks"],
        "videoSegmentCount": int(event.get("videoSegmentCount") or 0),
        "embeddingModelId": EMBEDDING_MODEL_ID,
        "generatedAt": generated_at,
    })
    if published_chunks:
        append_metadata_row(metadata_file_uri, {"metadataKey": CONTENT_CHUNK_COUNT_KEY,
                                                "metadataValue": str(published_chunks), "metadataValueType": TYPE_NUMBER})
    event["embeddingStatus"] = common.STATUS_FAILED if chunk_error else common.STATUS_SUCCEEDED
    return event

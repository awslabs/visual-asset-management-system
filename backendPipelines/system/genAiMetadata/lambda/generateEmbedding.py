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
from botocore.exceptions import ClientError
from customLogging.logger import safeLogger
import manifestHelper
import analysisCommon as common
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


def embedding_document_key(aux_temp_prefix, relative_path, version_id):
    digest = hashlib.sha256(file_version_key(relative_path, version_id).encode("utf-8")).hexdigest()
    return common.join_key(aux_temp_prefix, EMBEDDING_DOCUMENT_PREFIX + digest + ".json")


def genai_values(metadata_file_body):
    """``{metadataKey: metadataValue}`` from the .metadata.json body the analysis step wrote."""
    return {row.get("metadataKey"): row.get("metadataValue")
            for row in (metadata_file_body or {}).get("metadata") or [] if row.get("metadataKey")}


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


def lambda_handler(event, context):
    """
    GenerateEmbedding
    Embeds the file version's text, writes the embedding document, and publishes the indexing event.
    Skipped when vector search is off, the analysis step recorded a failure, or the run carries no
    bucket registration id for the indexer to resolve.
    """

    logger.info(f"Event: {event}")
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
    document_uri = common.write_json(s3_client, f"s3://{aux_bucket}/{document_key}", document)
    logger.info(f"Embedding document written: {document_uri}")

    detail = {field: value for field, value in document.items() if field not in ("embedding", "sourceText")}
    detail["documentS3Location"] = document_uri
    event["embeddingEventPublished"] = _publish(detail, event.get("orchestrationEventPrefix", "") or "")
    event["embeddingStatus"] = common.STATUS_SUCCEEDED
    event["embeddingDocumentS3Location"] = document_uri
    return event

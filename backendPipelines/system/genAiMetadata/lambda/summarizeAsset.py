#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Summary step of the SYSTEM GenAI asset summary workflow: one description of a whole asset or a folder of it,
from what the SYSTEM GenAI metadata pipeline wrote on each of its files.

The workflow asks for ``metadataInputs.assetFileMetadata``, so the input envelope carries one record per current
file under the selection (at most 500 per selection, with a ``fileExpansion`` block saying how many it left out).
From each record the step reads the file's ``genai_title``, ``genai_description``, ``genai_keywords`` and
``genai_category`` metadata (each, when empty, from the key the file's ``genai_targets`` attribute says the
per-file run wrote that field to under key reuse) and its ``sys_dependencies`` attribute. Scene files that load
other files and that no other file loads are listed first; a file a scene loads is marked as that scene's part.
One Converse call on the text model (``BEDROCK_TEXT_MODEL_ID``, empty = the analysis model), with the asset's
name, description and tags, its existing metadata and the file lines in a ``guardContent`` block, describes the
asset as a whole.

The result is the asset's ``asset.metadata.json``: the ``genai_asset_*`` rows (through key reuse when the template's
``reuseExistingKeys`` is on, typed by the asset's metadata schema when the envelope carries it), then
``genai_asset_source`` (``aggregate:<selection>``), an empty ``genai_asset_source_version``,
``genai_asset_file_count`` (the files the description drew on) and ``genai_asset_generated_at``. The results prefix
receives ``analysis-summary.json`` and, when any file had no description or the selection had more files than the
envelope carries, ``files-without-description.json``.

A Bedrock failure, or a selection none of whose files carries a description yet, is recorded through
``execution.status.json`` and the handler returns normally, so the process-output step records the execution FAILED.
"""

import datetime
import json
import os

import boto3
from botocore.config import Config
from customLogging.logger import safeLogger

import analysisCommon as common
import bedrockGuardrail
import classificationVocabulary as vocabulary
import executionLogKeys
import generateMetadata as gm
import keyReuse
import manifestHelper
import structuredOutput

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = safeLogger(service="SystemGenAiAssetSummary")

s3_client = boto3.client('s3', config=retry_config)
bedrock_runtime = boto3.client('bedrock-runtime', config=retry_config)

# The model of the summary's one text request: the text model, else the analysis model.
BEDROCK_TEXT_MODEL_ID = (os.environ.get("BEDROCK_TEXT_MODEL_ID", "").strip()
                         or os.environ.get("BEDROCK_ANALYSIS_MODEL_ID", "").strip())

GUARDRAIL_CONFIG = bedrockGuardrail.guardrail_config_from_env(os.environ)
if GUARDRAIL_CONFIG is None:
    logger.warning(bedrockGuardrail.GUARDRAIL_UNCONFIGURED_WARNING)

ANALYSIS_MODE = "asset-summary"
FILES_WITHOUT_DESCRIPTION_FILENAME = "files-without-description.json"
RESULTS_SCHEMA_VERSION = 1
ERROR_NO_FILE_DESCRIPTIONS = "AssetSummaryNoFileDescriptions"

# The per-file metadata the summary reads (the SYSTEM GenAI metadata pipeline's own keys) and the dependency report.
# With key reuse the per-file run may have written a field to a key the file already carried instead (a schema
# ``Description``, a ``tags`` key) and cleared the genai_* key; the file's targets record names that key.
FILE_TITLE_KEY = "genai_title"
FILE_DESCRIPTION_KEY = "genai_description"
FILE_KEYWORDS_KEY = "genai_keywords"
FILE_CATEGORY_KEY = "genai_category"
FILE_FIELD_KEYS = (("title", FILE_TITLE_KEY), ("description", FILE_DESCRIPTION_KEY),
                   ("keywords", FILE_KEYWORDS_KEY), ("category", FILE_CATEGORY_KEY))
# Bounds of the file section of the prompt: per file, and for all files together. Files past the budget are counted.
FILE_DESCRIPTION_MAX_CHARS = 400
FILE_TITLE_MAX_CHARS = 80
FILE_KEYWORDS_MAX_CHARS = 160
FILE_SECTION_MAX_CHARS = 40000
# At most this many paths are listed in files-without-description.json; the count names the rest.
UNDESCRIBED_LIST_MAX = 200
PATH_MAX_CHARS = 1024

# The asset fields the summary produces: the per-file analysis's asset fields.
SUMMARY_FIELD_NAMES = tuple(spec.name for spec in gm.ASSET_FIELDS)
ASSET_FILE_COUNT_KEY = "genai_asset_file_count"
ASSET_SOURCE_AGGREGATE_PREFIX = "aggregate:"

SYSTEM_PROMPT = (
    "You are a digital asset librarian describing one asset of an asset management system, a set of files that "
    "belong together such as a 3D scene and the parts and textures it loads, so it can be searched and classified. "
    "You receive the asset's name, description and tags, optionally its existing metadata, the classification "
    "vocabulary to choose from, and one line per file with the description an earlier analysis gave that file; "
    "scene files that load other files come first and a file a scene loads is marked as its part. Describe the "
    "asset as a whole rather than file by file. "
    "Return ONLY a JSON object with exactly these keys: "
    '"title" (string, at most 80 characters naming the asset), '
    '"description" (string, at most 1200 characters of plain prose describing what the asset is and contains), '
    '"keywords" (array of 8 to 15 lowercase strings of one to three words each), '
    '"category" (one category from CATEGORY OPTIONS), '
    '"subcategory" (one of that category\'s subcategories, or null), '
    '"style" (one of STYLE OPTIONS, or null), '
    '"materials" (array of strings from MATERIAL OPTIONS, empty when none is described), '
    '"colors" (array of strings from COLOR OPTIONS, the dominant color first, empty when none applies). '
    "Do not wrap the JSON in markdown fences and do not add any other text."
)
PREVIOUS_HEADER = "Previous summary of this asset:"
FILES_HEADER = "Files of the asset:"


def normalized_selection(relative_path):
    """The selection as an asset-relative key: ``/`` for the whole asset, ``/folder/`` for a folder."""
    text = "/" + str(relative_path or "").lstrip("/")
    return text if text.endswith("/") else text + "/"


def _asset_group(metadata_body, database_id, asset_id):
    for group in (metadata_body or {}).get("assets", []) or []:
        if group.get("databaseId") == database_id and group.get("assetId") == asset_id:
            return group
    return {}


def _parse_dependencies(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _clip(value, limit):
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "\u2026"


def _present(value):
    return value is not None and str(value).strip() != ""


def file_field_value(metadata, targets, field, default_key):
    """One description field of a file as it reads now: its ``genai_*`` key when that holds a value, else the key
    the file's targets record says the per-file run wrote it to (a value a person edited there since is read as
    it is), else empty."""
    value = metadata.get(default_key)
    if not _present(value) and targets.get(field):
        value = metadata.get(targets[field])
    return value if _present(value) else ""


def file_entries(group, selection):
    """One entry per file record of the asset group under ``selection``, in file-key order: its key, its
    description fields (``file_field_value``) and the asset-relative paths its dependency report loaded. Folder
    records and the asset-level record are not files."""
    entries = []
    seen = set()
    for record in group.get("files", []) or []:
        key = str((record or {}).get("fileKey") or "")
        if not key or key == "/" or key.endswith("/") or key in seen or not key.startswith(selection):
            continue
        seen.add(key)
        metadata = record.get("metadata") or {}
        attributes = record.get("attributes") or {}
        dependencies = _parse_dependencies(attributes.get("sys_dependencies")) or {}
        targets = keyReuse.parse_targets_record(attributes.get(keyReuse.FILE_TARGETS_KEY))
        loads = sorted("/" + str(rel).lstrip("/") for rel in (dependencies.get("versions") or {})
                       if str(rel).strip())
        entry = {"key": key[:PATH_MAX_CHARS]}
        for field, default_key in FILE_FIELD_KEYS:
            entry[field] = file_field_value(metadata, targets, field, default_key)
        entry["loads"] = [rel for rel in loads if rel != key]
        entry["resolved"] = dependencies.get("resolved") if isinstance(dependencies.get("resolved"), int) else 0
        entries.append(entry)
    return sorted(entries, key=lambda entry: entry["key"])


def ordered_entries(entries):
    """The entries with the top-level scenes first: files whose dependency report loaded other files and that no
    other file of the selection loads, the scene that loads the most first; then the other scenes, then every other
    file, each group in file-key order. Each entry gains ``partOf``, the scenes that load it."""
    loaded_by = {}
    for entry in entries:
        for rel in entry["loads"]:
            loaded_by.setdefault(rel, []).append(entry["key"])
    for entry in entries:
        entry["partOf"] = sorted(loaded_by.get(entry["key"], []))

    def is_scene(entry):
        return bool(entry["loads"]) or entry["resolved"] > 0

    top = sorted((e for e in entries if is_scene(e) and not e["partOf"]),
                 key=lambda e: (-max(len(e["loads"]), e["resolved"]), e["key"]))
    scenes = [e for e in entries if is_scene(e) and e["partOf"]]
    others = [e for e in entries if not is_scene(e)]
    return top + scenes + others


def file_line(entry):
    """One prompt line for a described file: its key, its role, then the earlier analysis of it."""
    role = ""
    if entry["loads"] or entry["resolved"]:
        role = f" (scene, loads {max(len(entry['loads']), entry['resolved'])} files)"
    elif entry["partOf"]:
        role = " (part of " + ", ".join(entry["partOf"][:3]) + ")"
    parts = [f"- {entry['key']}{role}: {_clip(entry['title'], FILE_TITLE_MAX_CHARS)}".rstrip(": ")]
    parts.append(_clip(entry["description"], FILE_DESCRIPTION_MAX_CHARS))
    if entry["keywords"]:
        parts.append("keywords: " + _clip(entry["keywords"], FILE_KEYWORDS_MAX_CHARS))
    if entry["category"]:
        parts.append("category: " + _clip(entry["category"], FILE_TITLE_MAX_CHARS))
    return " | ".join(parts)


def file_section(described):
    """``(lines, listed)``: the file lines within FILE_SECTION_MAX_CHARS, whole lines only, in order."""
    lines = []
    used = 0
    for entry in described:
        line = file_line(entry)
        if used + len(line) > FILE_SECTION_MAX_CHARS:
            break
        lines.append(line)
        used += len(line)
    return lines, len(lines)


def build_user_content(asset_data, database_id, asset_id, selection, vocabulary_parts, existing_lines,
                       previous_lines, target_lines, file_lines, unlisted, omitted, omitted_exact):
    """The user message's content blocks: the pipeline's own instructions plain; the asset's identity and metadata,
    the vocabulary's options, the schema's lists and the file lines guarded."""
    vocabulary_opening, vocabulary_options, vocabulary_closing = vocabulary_parts
    context = []
    if asset_data.get("assetName"):
        context.append(f"Asset name: {asset_data['assetName']}")
    if asset_data.get("description"):
        context.append(f"Asset description: {asset_data['description']}")
    tags = [str(tag) for tag in (asset_data.get("tags") or []) if str(tag).strip()]
    if tags:
        context.append("Asset tags: " + ", ".join(tags))
    scope = "the whole asset" if selection == "/" else f"the folder {selection}"
    identity = [f"Database: {database_id}", f"Asset: {asset_id}", f"Summarise: {scope}"]
    guarded = context + identity + list(vocabulary_options)
    if target_lines:
        guarded.append(gm.TARGETS_HEADER + "\n" + "\n".join(f"- {line}" for line in target_lines))
    guarded.extend(gm.existing_metadata_sections(existing_lines))
    if previous_lines:
        guarded.append(PREVIOUS_HEADER + "\n" + "\n".join(f"- {line}" for line in previous_lines))
    guarded.append(FILES_HEADER + "\n" + "\n".join(file_lines))
    plain = ["Describe and classify this asset for search indexing from the descriptions of its files.",
             vocabulary_opening, vocabulary_closing]
    if unlisted:
        plain.append(f"{unlisted} more described file(s) are not listed here.")
    if omitted:
        plain.append(f"The selection has {'at least ' if not omitted_exact else ''}{omitted} more file(s) that are "
                     "not listed here.")
    if target_lines:
        plain.append(gm.TARGETS_INSTRUCTION)
    if previous_lines:
        plain.append("A previous summary of this asset is included; keep its wording where it still describes the "
                     "asset.")
    return bedrockGuardrail.user_content_blocks("\n".join(plain), "\n".join(guarded), GUARDRAIL_CONFIG)


def provenance_rows(selection, file_count, generated_at):
    return [
        gm._row(gm.ASSET_SOURCE_KEY, ASSET_SOURCE_AGGREGATE_PREFIX + selection, gm.TYPE_STRING),
        gm._row(gm.ASSET_SOURCE_VERSION_KEY, "", gm.TYPE_STRING),
        gm._row(ASSET_FILE_COUNT_KEY, str(int(file_count)), gm.TYPE_NUMBER),
        gm._row(gm.ASSET_GENERATED_AT_KEY, generated_at, gm.TYPE_DATE),
    ]


def undescribed_report(selection, undescribed, expansion):
    omitted = int(expansion.get("omittedFileCount") or 0)
    return {
        "schemaVersion": RESULTS_SCHEMA_VERSION,
        "selection": selection,
        "count": len(undescribed),
        "files": undescribed[:UNDESCRIBED_LIST_MAX],
        "moreNotListed": max(0, len(undescribed) - UNDESCRIBED_LIST_MAX),
        # The selection had more files than the envelope carries; they were neither summarised nor checked.
        "selectionCapped": omitted > 0 or expansion.get("omittedFileCountExact") is False,
        "omittedFileCount": omitted,
        "omittedFileCountExact": bool(expansion.get("omittedFileCountExact", True)),
    }


def lambda_handler(event, context):
    """
    SummarizeAsset
    Describes the selected asset or folder from its files' earlier analysis and writes the asset's genai_asset_*
    metadata; lists the files without a description on the results prefix.
    """
    executionLogKeys.bind_execution_ids(logger, event)
    logger.info("Event", jobName=event.get("jobName", ""), assetId=event.get("assetId", ""),
                relativePath=event.get("relativePath", ""), eventKeys=sorted(event))

    database_id = event.get("databaseId", "")
    asset_id = event.get("assetId", "")
    selection = normalized_selection(event.get("relativePath", ""))
    results_prefix = event["outputS3AssetResultsPath"]
    warnings = []

    config = manifestHelper.fetch_input_configuration(s3_client, event.get("inputConfigurationS3Location", ""))
    metadata_body = manifestHelper.fetch_metadata(s3_client, event.get("inputMetadataS3Location", ""))
    view = manifestHelper.to_legacy_vams_view(metadata_body, database_id, asset_id, "/")
    vams = view.get("VAMS") or {}
    asset_data = vams.get("assetData") or {}
    asset_metadata = vams.get("assetMetadata") or {}
    group = _asset_group(metadata_body, database_id, asset_id)
    expansion = group.get("fileExpansion") or {}

    seed_on = common.as_bool(config.get("seedWithExistingMetadata"), True)
    reuse_on = common.as_bool(config.get("reuseExistingKeys"), True)
    field_mapping, mapping_warnings = keyReuse.normalize_field_mapping(config.get("fieldMapping"))
    if reuse_on:
        warnings.extend(mapping_warnings)
    raw_vocabulary = config.get("classificationVocabulary")
    vocab = vocabulary.normalize_vocabulary(raw_vocabulary)
    if vocabulary.exceeds_cap(raw_vocabulary):
        warnings.append(f"classificationVocabulary over {vocabulary.VOCABULARY_MAX_BYTES} bytes; the default "
                        "vocabulary was used")

    entries = ordered_entries(file_entries(group, selection))
    described = [entry for entry in entries if str(entry["description"]).strip()]
    undescribed = [entry["key"] for entry in entries if not str(entry["description"]).strip()]
    omitted = int(expansion.get("omittedFileCount") or 0)
    omitted_exact = bool(expansion.get("omittedFileCountExact", True))
    if not expansion:
        warnings.append("The run's input carries no per-file records for the selection (the workflow does not ask "
                        "for assetFileMetadata); only explicitly selected files were summarised.")
    elif omitted or not omitted_exact:
        warnings.append(f"The selection has {'at least ' if not omitted_exact else ''}{omitted} more file(s) than "
                        "the run's input carries; the summary covers the files listed.")

    generated_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    summary = {
        "schemaVersion": common.ANALYSIS_SUMMARY_SCHEMA_VERSION,
        "status": None,
        "error": None,
        "analysisMode": ANALYSIS_MODE,
        "analysisModelId": BEDROCK_TEXT_MODEL_ID,
        "selection": selection,
        "fileCount": len(entries),
        "describedCount": len(described),
        "undescribedCount": len(undescribed),
        "filesListed": 0,
        "fileExpansion": {"expandedFileCount": int(expansion.get("expandedFileCount") or 0),
                          "omittedFileCount": omitted, "omittedFileCountExact": omitted_exact},
        "usage": {},
        "guardrailMasked": False,
        "guardrailMaskedTypes": [],
        "warnings": warnings,
        "vocabularyCorrections": [],
        "generatedAt": generated_at,
        "writeTarget": gm.WRITE_TARGET_ASSET,
        "assetMetadataFile": None,
        "undescribedFile": None,
        "keyReuse": {"enabled": reuse_on, "schemaRead": False, "targets": {}, "cleared": [], "previousValues": 0},
        "keyConflicts": [],
        "replyFormat": None,
    }

    if undescribed or omitted or not omitted_exact:
        summary["undescribedFile"] = common.write_json(
            s3_client, common.uri_join(results_prefix, FILES_WITHOUT_DESCRIPTION_FILENAME),
            undescribed_report(selection, undescribed, expansion))

    def finish():
        event["analysisSummaryS3Location"] = common.write_json(
            s3_client, common.uri_join(results_prefix, common.ANALYSIS_SUMMARY_RESULTS_FILENAME), summary)
        return event

    if not described:
        cause = (f"No file under {selection} carries a description yet; run the SYSTEM GenAI metadata pipeline on "
                 "the asset's files first.")
        logger.error(cause)
        common.write_execution_status(s3_client, results_prefix, ERROR_NO_FILE_DESCRIPTIONS, cause)
        summary["status"] = common.STATUS_FAILED
        summary["error"] = ERROR_NO_FILE_DESCRIPTIONS
        event["analysisStatus"] = common.STATUS_FAILED
        return finish()

    asset_digests = keyReuse.parse_digest_record(asset_metadata.get(keyReuse.ASSET_DIGEST_KEY))
    asset_schema = None
    constraints = {}
    if reuse_on:
        asset_schema = gm.metadata_schema_fields(metadata_body, "assetMetadata", database_id)
        if asset_schema is None:
            warnings.append(gm.SCHEMA_ABSENT_WARNING.format(database=database_id))
        constraints = keyReuse.controlled_targets(gm.ASSET_FIELDS, asset_metadata, field_mapping, asset_digests,
                                                  asset_schema, aliases=gm.ASSET_FIELD_ALIASES)
    summary["keyReuse"]["schemaRead"] = asset_schema is not None

    existing, _dropped = common.existing_metadata_lines(view)
    previous_lines = (gm.previous_analysis_lines(gm.ASSET_FIELDS, asset_metadata, gm.ASSET_FIELD_ALIASES)
                      if reuse_on and seed_on else [])
    summary["keyReuse"]["previousValues"] = len(previous_lines)
    lines, listed = file_section(described)
    summary["filesListed"] = listed
    user_blocks = build_user_content(asset_data, database_id, asset_id, selection,
                                     vocabulary.vocabulary_prompt_parts(vocab), existing if seed_on else [],
                                     previous_lines, structuredOutput.controlled_list_lines(constraints), lines,
                                     len(described) - listed, omitted, omitted_exact)
    tool = gm.analysis_tool(constraints, names=SUMMARY_FIELD_NAMES) if asset_schema is not None else None
    try:
        result, usage, masked_types, reply_format = gm.analyze(
            user_blocks, [], BEDROCK_TEXT_MODEL_ID, tool=tool, system_prompt=SYSTEM_PROMPT, client=bedrock_runtime)
        result, corrections = vocabulary.validate_against_vocabulary(result, vocab)
        plan = keyReuse.plan_entity(gm.ASSET_FIELDS, gm.result_values(result), asset_metadata, field_mapping,
                                    asset_digests, reuse=reuse_on, aliases=gm.ASSET_FIELD_ALIASES,
                                    schema_fields=asset_schema)
        rows = plan.rows + provenance_rows(selection, listed, generated_at)
        digest = gm.digest_row(keyReuse.ASSET_DIGEST_KEY, plan) if reuse_on else None
        if digest:
            rows.append(digest)
        summary["assetMetadataFile"] = common.write_json(
            s3_client, common.uri_join(event["outputS3AssetMetadataPath"], gm.ASSET_METADATA_FILENAME),
            gm.asset_metadata_body(rows))
        summary["keyReuse"]["targets"] = plan.targets
        summary["keyReuse"]["cleared"] = list(plan.cleared)
        summary["keyConflicts"] = [dict(conflict, entity="asset") for conflict in plan.conflicts]
        summary["usage"] = {"inputTokens": int(usage.get("inputTokens", 0) or 0),
                            "outputTokens": int(usage.get("outputTokens", 0) or 0)}
        summary["guardrailMasked"] = bool(masked_types)
        summary["guardrailMaskedTypes"] = masked_types
        summary["vocabularyCorrections"] = corrections
        summary["replyFormat"] = reply_format
        summary["status"] = common.STATUS_SUCCEEDED
        event["analysisStatus"] = common.STATUS_SUCCEEDED
        logger.info(f"Asset summary written from {listed} of {len(entries)} files ({len(undescribed)} without a "
                    "description)")
    except gm.BedrockAnalysisFailure as failure:
        logger.error(f"Bedrock summary failed ({failure.code}): {failure.cause}")
        common.write_execution_status(s3_client, results_prefix, failure.code, failure.cause)
        summary["status"] = common.STATUS_FAILED
        summary["error"] = failure.code
        event["analysisStatus"] = common.STATUS_FAILED
    return finish()

#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Analysis step of the SYSTEM GenAI metadata pipeline: two metadata layers per file.

Writes the file's ``sys_*`` attributes first (the complete raw record), then the typed ``ext_*``
promotion of those attributes and a GeoJSON ``location`` (both deterministic, ``metadataCatalog``), then
asks the analysis model (Bedrock Converse) for a title, description, keywords, a category and
subcategory from the template's classification vocabulary, style, materials, colors, objects, complexity,
orientation, a size estimate and a text summary, and writes them as ``genai_*`` file metadata. When the
template's ``seedWithExistingMetadata`` is on, the prompt also carries the file's, the asset's and the
database's existing metadata and the file's non-pipeline attributes, rendered by the shared
``analysisCommon.existing_metadata_lines`` helper; the summary counts those lines either way. A caught
Bedrock failure is recorded through ``execution.status.json`` on the results prefix and the handler
returns normally, so the attributes and the deterministic metadata already written reach the asset and
the process-output step records the execution FAILED. Only an unexpected fault raises.

When a Bedrock guardrail is configured (``BEDROCK_GUARDRAIL_IDENTIFIER`` and ``BEDROCK_GUARDRAIL_VERSION``,
always together), every Converse call carries it and the parts of the prompt that come from the file and
its metadata — the asset's name, description and tags, the file identity and attributes, the existing
metadata and the text excerpt — travel in a ``guardContent`` block so the guardrail's input filters
evaluate them; the instruction, the vocabulary and the render note stay in a plain text block. A
guardrail intervention — a filter that blocked the prompt or the response — is a caught failure recorded as
``BedrockGuardrailIntervened``. A response the sensitive-information filter only masked is a success: its
text, with the filter's type tokens in place of the entities, is parsed as any other answer, and the
analysis summary records ``guardrailMasked`` with the masked entity types.
"""

import datetime
import json
import os
import time
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from customLogging.logger import safeLogger
import manifestHelper
import analysisCommon as common
import bedrockGuardrail
import metadataCatalog
import classificationVocabulary as vocabulary

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline lambda
# runs against throttling-prone services (Step Functions, Amazon S3, EventBridge) for the length of
# a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = safeLogger(service="SystemGenAiMetadataGenerateMetadata")

s3_client = boto3.client('s3', config=retry_config)
bedrock_runtime = boto3.client('bedrock-runtime', config=retry_config)

BEDROCK_ANALYSIS_MODEL_ID = os.environ["BEDROCK_ANALYSIS_MODEL_ID"]

# The guardrail applied to every Converse call (bedrockGuardrail: both variables or neither). Without one the
# calls run without prompt-attack filters; the one warning at cold start is the operator's signal.
GUARDRAIL_CONFIG = bedrockGuardrail.guardrail_config_from_env(os.environ)
if GUARDRAIL_CONFIG is None:
    logger.warning(bedrockGuardrail.GUARDRAIL_UNCONFIGURED_WARNING)
GUARDRAIL_STOP_REASON = bedrockGuardrail.GUARDRAIL_STOP_REASON
GUARD_CONTENT_QUALIFIERS = bedrockGuardrail.GUARD_CONTENT_QUALIFIERS

MAX_IMAGES = 8
MAX_IMAGE_BYTES = 3_750_000
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = (2, 4)
MAX_TOKENS = 2048
TEMPERATURE = 0.2
DEFAULT_MAX_TEXT_CHARS = 12000
MAX_ATTRIBUTES_CHARS = 6000
MAX_TITLE_CHARS = 80
MAX_DESCRIPTION_CHARS = 1200
MAX_TEXT_SUMMARY_CHARS = 2000
MAX_KEYWORDS = 25
MAX_LIST_ITEMS = 25
COMPLEXITY_VALUES = ("low", "medium", "high")

ATTRIBUTE_FILE_SUFFIX = ".attribute.json"
METADATA_FILE_SUFFIX = ".metadata.json"
ASSET_METADATA_FILENAME = "asset.metadata.json"
ASSET_KEYWORDS_KEY = "genai_asset_keywords"
ASSET_CATEGORIES_KEY = "genai_asset_categories"
LIST_SEPARATOR = ", "

TYPE_STRING = "string"
TYPE_MULTILINE_STRING = "multiline_string"
TYPE_DATE = "date"
TYPE_NUMBER = "number"
# Written from the media branch's video window plan when one exists.
SEGMENT_COUNT_KEY = "genai_segment_count"
SEGMENT_INTERVAL_KEY = "genai_segment_interval_seconds"

# (model result key, metadata key, metadataValueType) in the order the rows are written. List-valued
# results are ", "-joined strings: the web's inline_controlled_list widget holds one value.
GENAI_KEY_TYPES = (
    ("title", "genai_title", TYPE_STRING),
    ("description", "genai_description", TYPE_MULTILINE_STRING),
    ("keywords", "genai_keywords", TYPE_STRING),
    ("category", "genai_category", TYPE_STRING),
    ("subcategory", "genai_subcategory", TYPE_STRING),
    ("style", "genai_style", TYPE_STRING),
    ("materials", "genai_materials", TYPE_STRING),
    ("colors", "genai_colors", TYPE_STRING),
    ("primaryColor", "genai_primary_color", TYPE_STRING),
    ("objects", "genai_objects", TYPE_STRING),
    ("complexity", "genai_complexity", TYPE_STRING),
    ("orientation", "genai_orientation", TYPE_STRING),
    ("sizeEstimate", "genai_size_estimate", TYPE_STRING),
    ("textSummary", "genai_text_summary", TYPE_MULTILINE_STRING),
)

MODALITY_FILE_ATTRIBUTES = "file-attributes"
MODALITY_ASSET_METADATA = "asset-metadata"
MODALITY_SEED_METADATA = "seed-metadata"
MODALITY_FILE_TEXT = "file-text"
MODALITY_RENDERS = "renders"
MODALITY_KEYFRAMES = "keyframes"
MODALITY_IMAGE = "image"
MODALITY_PAGES = "pages"
_IMAGE_MODALITY_BY_CLASS = {"video": MODALITY_KEYFRAMES, "image": MODALITY_IMAGE, "document": MODALITY_PAGES}

# Prompt section header per existing-metadata scope (analysisCommon.EXISTING_METADATA_SCOPES order).
EXISTING_SECTION_HEADERS = {
    "fileMetadata": "Existing file metadata:",
    "assetMetadata": "Existing asset metadata:",
    "databaseMetadata": "Existing database metadata:",
    "fileAttributes": "Existing file attributes:",
}

SYSTEM_PROMPT = (
    "You are a digital asset librarian describing one file from an asset management system so it can be "
    "searched and classified. You receive the asset's name, description and tags, the file's path and class, "
    "the file's technical attributes as JSON, the classification vocabulary to choose from, optionally existing "
    "metadata, optionally an excerpt of the file's text, and optionally rendered views or frames of the file. "
    "Return ONLY a JSON object with exactly these keys: "
    '"title" (string, at most 80 characters naming the content), '
    '"description" (string, at most 1200 characters of plain prose describing what the file contains and depicts), '
    '"keywords" (array of 8 to 15 lowercase strings of one to three words each), '
    '"category" (one category from CATEGORY OPTIONS), '
    '"subcategory" (one of that category\'s subcategories, or null), '
    '"style" (one of STYLE OPTIONS, or null), '
    '"materials" (array of strings from MATERIAL OPTIONS, empty when none is visible), '
    '"colors" (array of strings from COLOR OPTIONS, the dominant color first, empty when none applies), '
    '"objects" (array of strings naming the distinct objects, parts or entities visible or described; empty when none), '
    '"complexity" (one of "low", "medium", "high"), '
    '"orientation" (for 3D content the up axis and facing direction, e.g. "Z-up, front faces -Y"; otherwise null), '
    '"sizeEstimate" (the real-world size in words when it can be judged, otherwise null), '
    '"textSummary" (string, at most 2000 characters summarising any text content, or null when the file carries no text). '
    "Do not wrap the JSON in markdown fences and do not add any other text."
)


class ModelResponseError(Exception):
    """A model reply that carries no usable JSON object."""


class BedrockAnalysisFailure(Exception):
    """A Bedrock failure the run records through execution.status.json rather than raising."""

    def __init__(self, code, cause):
        super().__init__(cause)
        self.code = code
        self.cause = cause


def relative_key(relative_path):
    return (relative_path or "").lstrip("/")


def _row(key, value, value_type):
    return {"metadataKey": key, "metadataValue": value, "metadataValueType": value_type}


def attribute_file_body(attributes):
    """The ``.attribute.json`` body: one string-typed row per ``sys_*`` key, structured values
    JSON-encoded (file attributes accept only the string type); a null or blank group produces no row."""
    rows = []
    for key, value in sorted((attributes or {}).items()):
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, sort_keys=True, default=str)
        else:
            if value is None or not str(value).strip():
                continue
            rendered = str(value)
        rows.append(_row(key, rendered, TYPE_STRING))
    return {"type": "attribute", "updateType": "update", "metadata": rows}


def write_attribute_file(manifest, metadata_prefix_uri, relative_path):
    uri = common.uri_join(metadata_prefix_uri, relative_key(relative_path) + ATTRIBUTE_FILE_SUFFIX)
    common.write_json(s3_client, uri, attribute_file_body(manifest.get("attributes") or {}))
    return uri


def has_location_key(metadata):
    """Whether the file's existing metadata already carries a non-empty ``location`` (any case) — the
    same lookup the indexer's geoLocation helper applies."""
    for key, value in (metadata or {}).items():
        if str(key).lower() == metadataCatalog.LOCATION_KEY and value not in (None, ""):
            return True
    return False


def location_item(attributes, file_class, existing_file_metadata):
    """The ``location`` metadata item, or ``None`` when the file already has one or no source yields a
    valid geometry."""
    if has_location_key(existing_file_metadata):
        return None
    geometry = metadataCatalog.location_geojson(attributes, file_class)
    if geometry is None:
        return None
    return _row(metadataCatalog.LOCATION_KEY, json.dumps(geometry, separators=(",", ":")),
                metadataCatalog.TYPE_GEOJSON)


def image_modality(file_class):
    return _IMAGE_MODALITY_BY_CLASS.get(file_class, MODALITY_RENDERS)


def load_images(manifest, aux_bucket, warnings):
    """Converse image blocks for the manifest's render images, at most MAX_IMAGES, each within the
    per-image byte limit; an unreadable or oversized image is skipped with a warning."""
    blocks = []
    for key in (manifest.get("renderImages") or [])[:MAX_IMAGES]:
        try:
            data = s3_client.get_object(Bucket=aux_bucket, Key=key)["Body"].read()
        except Exception as e:
            warnings.append(f"render image unreadable: {key}: {e}")
            continue
        if len(data) > MAX_IMAGE_BYTES:
            warnings.append(f"render image skipped (over {MAX_IMAGE_BYTES} bytes): {key}")
            continue
        blocks.append({"image": {"format": "png", "source": {"bytes": data}}})
    return blocks


def existing_metadata_sections(existing_lines):
    """The prompt's existing-metadata sections: each scope that contributed, under its header, one
    ``- key: value`` line per entry, in scope order."""
    sections = []
    for scope in common.EXISTING_METADATA_SCOPES:
        lines = [line for line_scope, line in existing_lines if line_scope == scope]
        if lines:
            sections.append(EXISTING_SECTION_HEADERS[scope] + "\n" + "\n".join(f"- {line}" for line in lines))
    return sections


def _user_text_parts(asset_data, database_id, relative_path, file_class, file_ext, manifest, vocabulary_parts,
                     existing_lines, text_excerpt, image_count):
    """The prompt's parts by name. ``intro``, ``vocabularyOpening``, ``vocabularyClosing`` and ``closing`` are the
    pipeline's own words; ``context``, ``identity``, ``attributes``, ``facts``, ``existing`` and ``excerpt`` come
    from the file and its metadata, and ``vocabularyOptions`` from the template's operator-edited configuration.
    ``vocabulary_parts`` is the ``(opening, options, closing)`` tuple ``vocabulary_prompt_parts`` returns."""
    vocabulary_opening, vocabulary_options, vocabulary_closing = vocabulary_parts
    context = []
    if asset_data.get("assetName"):
        context.append(f"Asset name: {asset_data['assetName']}")
    if asset_data.get("description"):
        context.append(f"Asset description: {asset_data['description']}")
    tags = [str(tag) for tag in (asset_data.get("tags") or []) if str(tag).strip()]
    if tags:
        context.append("Asset tags: " + ", ".join(tags))
    attributes_json = json.dumps(manifest.get("attributes") or {}, separators=(",", ":"), sort_keys=True,
                                 default=str)
    facts = manifest.get("facts") or {}
    closing = ""
    if manifest.get("renderSkipped"):
        closing = (f"No rendered views are available (render skipped: {manifest['renderSkipped']}); "
                   "describe the file from its name, attributes and context.")
    elif image_count:
        closing = f"{image_count} rendered view(s) or frame(s) of the file follow."
    return {
        "intro": ["Describe and classify this file for search indexing."],
        "context": context,
        "identity": [f"Database: {database_id}", f"File: {relative_path} (class: {file_class}, extension: {file_ext})"],
        "attributes": ["File attributes (JSON): " + attributes_json[:MAX_ATTRIBUTES_CHARS]],
        "facts": ["File facts: " + "; ".join(f"{key}: {value}" for key, value in sorted(facts.items()))] if facts else [],
        "vocabularyOpening": [vocabulary_opening],
        "vocabularyOptions": vocabulary_options,
        "vocabularyClosing": [vocabulary_closing],
        "existing": existing_metadata_sections(existing_lines),
        "excerpt": ["File text excerpt:\n" + text_excerpt] if text_excerpt else [],
        "closing": [closing] if closing else [],
    }


_USER_TEXT_ORDER = ("intro", "context", "identity", "attributes", "facts", "vocabularyOpening", "vocabularyOptions",
                    "vocabularyClosing", "existing", "excerpt", "closing")
# Every part that is not the pipeline's own words is guarded: the file-derived parts and the vocabulary's option
# lines, which an operator edits on the template without a deploy. The vocabulary's opening and closing sentences
# are instructions the pipeline wrote, so they stay plain: an imperative sentence inside guardContent is what a
# prompt-attack filter is built to flag, and a hit there would fail every analysis in the deployment.
_GUARDED_PARTS = ("context", "identity", "attributes", "facts", "vocabularyOptions", "existing", "excerpt")
_UNGUARDED_PARTS = ("intro", "vocabularyOpening", "vocabularyClosing", "closing")


def build_user_text(asset_data, database_id, relative_path, file_class, file_ext, manifest, vocabulary_parts,
                    existing_lines, text_excerpt, image_count):
    parts = _user_text_parts(asset_data, database_id, relative_path, file_class, file_ext, manifest,
                             vocabulary_parts, existing_lines, text_excerpt, image_count)
    return "\n".join(line for name in _USER_TEXT_ORDER for line in parts[name])


def build_user_content(asset_data, database_id, relative_path, file_class, file_ext, manifest, vocabulary_parts,
                       existing_lines, text_excerpt, image_count, guarded):
    """The user message's text content blocks. Without a guardrail, one text block in prompt order. With one,
    a text block carrying the pipeline's own instructions (the intro, the vocabulary's opening and closing
    sentences, the render note), then a ``guardContent`` block carrying everything that comes from the file, its
    metadata and the template's vocabulary options, so the guardrail evaluates that input and not the prompt."""
    if not guarded:
        return [{"text": build_user_text(asset_data, database_id, relative_path, file_class, file_ext, manifest,
                                         vocabulary_parts, existing_lines, text_excerpt, image_count)}]
    parts = _user_text_parts(asset_data, database_id, relative_path, file_class, file_ext, manifest,
                             vocabulary_parts, existing_lines, text_excerpt, image_count)
    plain = "\n".join(line for name in _UNGUARDED_PARTS for line in parts[name])
    guarded_text = "\n".join(line for name in _GUARDED_PARTS for line in parts[name])
    return bedrockGuardrail.user_content_blocks(plain, guarded_text, GUARDRAIL_CONFIG)


def _strip_fences(text):
    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.find("\n")
        cleaned = cleaned[first_newline + 1:] if first_newline >= 0 else cleaned[3:]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    return cleaned


def _clean_text(value, limit=None):
    """Whitespace-normalised text, bounded to ``limit`` characters; ``None`` for a non-string or blank."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text:
        return None
    return text[:limit] if limit else text


def _string_list(values, limit=None):
    out = []
    seen = set()
    for value in values if isinstance(values, list) else []:
        item = _clean_text(value)
        if not item or item.lower() in seen:
            continue
        seen.add(item.lower())
        out.append(item)
        if limit and len(out) >= limit:
            break
    return out


def parse_model_json(text):
    """The model reply as a normalised dict: fences stripped, the outermost JSON object extracted,
    description and keywords required, lengths bounded, lists deduplicated, complexity constrained."""
    cleaned = _strip_fences(text or "")
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ModelResponseError("no JSON object in the model reply")
    try:
        parsed = json.loads(cleaned[start:end + 1])
    except ValueError as e:
        raise ModelResponseError(f"model reply is not valid JSON: {e}")
    if not isinstance(parsed, dict):
        raise ModelResponseError("model reply is not a JSON object")
    description = _clean_text(parsed.get("description"), MAX_DESCRIPTION_CHARS)
    keywords = parsed.get("keywords")
    if description is None:
        raise ModelResponseError("model reply has no description")
    if not isinstance(keywords, list):
        raise ModelResponseError("model reply has no keywords list")
    complexity = _clean_text(parsed.get("complexity"))
    complexity = complexity.lower() if complexity and complexity.lower() in COMPLEXITY_VALUES else None
    return {
        "title": _clean_text(parsed.get("title"), MAX_TITLE_CHARS),
        "description": description,
        "keywords": [keyword.lower() for keyword in _string_list(keywords, MAX_KEYWORDS)],
        "category": _clean_text(parsed.get("category")) or "",
        "subcategory": _clean_text(parsed.get("subcategory")),
        "style": _clean_text(parsed.get("style")),
        "materials": _string_list(parsed.get("materials"), MAX_LIST_ITEMS),
        "colors": _string_list(parsed.get("colors"), MAX_LIST_ITEMS),
        "objects": _string_list(parsed.get("objects"), MAX_LIST_ITEMS),
        "complexity": complexity,
        "orientation": _clean_text(parsed.get("orientation")),
        "sizeEstimate": _clean_text(parsed.get("sizeEstimate")),
        "textSummary": _clean_text(parsed.get("textSummary"), MAX_TEXT_SUMMARY_CHARS),
    }


def analyze(user_blocks, image_blocks):
    """``(result, usage, masked_types)`` after at most MAX_ATTEMPTS Converse calls. A throttle backs off and
    retries, an unparsable reply is re-asked; every other Bedrock error, a guardrail intervention and an
    exhausted attempt budget are a BedrockAnalysisFailure the caller records. With a guardrail configured the
    images travel in ``guardContent`` blocks, like the file-derived text. ``masked_types`` names the entity
    types the guardrail anonymized in the prompt or the reply (empty when none): a masked reply is a complete
    answer and is parsed like any other."""
    content = list(user_blocks) + bedrockGuardrail.user_image_blocks(image_blocks, GUARDRAIL_CONFIG)
    request = {
        "modelId": BEDROCK_ANALYSIS_MODEL_ID,
        "system": [{"text": SYSTEM_PROMPT}],
        "messages": [{"role": "user", "content": content}],
        "inferenceConfig": {"maxTokens": MAX_TOKENS, "temperature": TEMPERATURE},
    }
    if GUARDRAIL_CONFIG:
        request["guardrailConfig"] = dict(GUARDRAIL_CONFIG)
    last_parse_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = bedrock_runtime.converse(**request)
        except ClientError as e:
            code = common.bedrock_error_code(e)
            if code == common.ERROR_BEDROCK_THROTTLED and attempt < MAX_ATTEMPTS:
                logger.warning(f"Bedrock throttled on attempt {attempt}; retrying")
                time.sleep(RETRY_BACKOFF_SECONDS[attempt - 1])
                continue
            raise BedrockAnalysisFailure(code, str(e))
        if bedrockGuardrail.intervened(response):
            raise BedrockAnalysisFailure(common.ERROR_BEDROCK_GUARDRAIL_INTERVENED,
                                         bedrockGuardrail.guardrail_cause(response))
        masked_types = bedrockGuardrail.masked_entity_types(response)
        message = ((response.get("output") or {}).get("message") or {})
        text = "".join(block.get("text", "") for block in (message.get("content") or []))
        usage = response.get("usage") or {}
        try:
            return parse_model_json(text), usage, masked_types
        except ModelResponseError as e:
            last_parse_error = e
            logger.warning(f"Attempt {attempt}: {e}")
    raise BedrockAnalysisFailure(
        common.ERROR_BEDROCK_MODEL,
        f"model returned no parsable JSON after {MAX_ATTEMPTS} attempts: {last_parse_error}")


def genai_rows(result, modalities, generated_at):
    """The ``genai_*`` rows in GENAI_KEY_TYPES order plus the model, timestamp and modalities rows;
    a null or blank value produces no row, lists are ", "-joined."""
    values = dict(result)
    values["primaryColor"] = (result.get("colors") or [None])[0]
    rows = []
    for result_key, metadata_key, value_type in GENAI_KEY_TYPES:
        value = values.get(result_key)
        if isinstance(value, list):
            value = LIST_SEPARATOR.join(item for item in value if item)
        if value is None or str(value).strip() == "":
            continue
        rows.append(_row(metadata_key, str(value), value_type))
    rows.append(_row("genai_model", BEDROCK_ANALYSIS_MODEL_ID, TYPE_STRING))
    rows.append(_row("genai_generated_at", generated_at, TYPE_DATE))
    rows.append(_row("genai_source_modalities", LIST_SEPARATOR.join(modalities), TYPE_STRING))
    return rows


def load_segment_plan(event, warnings):
    """The video window plan the media branch wrote, or None when the state names none or it cannot be read."""
    uri = event.get("videoSegmentPlanS3Location")
    if not uri:
        return None
    try:
        return common.read_json(s3_client, uri)
    except Exception as e:  # noqa: BLE001 - the rows are descriptive; the Map runs from the state fields regardless
        warnings.append(f"video segment plan unreadable ({uri}): {e}")
        return None


def segment_rows(plan):
    """The window rows: the plan's count and its effective interval, both ``number``; none without a plan."""
    if not plan or not plan.get("count"):
        return []
    rows = [_row(SEGMENT_COUNT_KEY, str(int(plan["count"])), TYPE_NUMBER)]
    interval = plan.get("effectiveIntervalSeconds")
    if isinstance(interval, (int, float)) and not isinstance(interval, bool):
        rows.append(_row(SEGMENT_INTERVAL_KEY, f"{interval:g}", TYPE_NUMBER))
    return rows


def metadata_file_body(promoted_items, location_row, genai):
    """The ``.metadata.json`` body: the ext_* items, then location, then the genai_* rows."""
    rows = list(promoted_items or [])
    if location_row:
        rows.append(location_row)
    rows.extend(genai or [])
    return {"type": "metadata", "updateType": "update", "metadata": rows}


def _merge_list_value(existing_value, additions):
    existing = [item.strip() for item in str(existing_value or "").split(",")]
    return LIST_SEPARATOR.join(_string_list([*existing, *additions]))


def asset_metadata_body(existing_keywords, keywords, existing_categories, categories):
    """``asset.metadata.json`` carrying the union of the asset's existing genai_asset_keywords /
    genai_asset_categories and this file's keywords / category."""
    rows = []
    if keywords:
        rows.append(_row(ASSET_KEYWORDS_KEY, _merge_list_value(existing_keywords, keywords), TYPE_STRING))
    if categories:
        rows.append(_row(ASSET_CATEGORIES_KEY, _merge_list_value(existing_categories, categories), TYPE_STRING))
    return {"type": "metadata", "updateType": "update", "metadata": rows}


def lambda_handler(event, context):
    """
    GenerateMetadata
    Writes the file's attributes, promotes the typed ext_* metadata and location, analyses the file with
    the Bedrock model against the template's vocabulary, writes the genai_* metadata, and records the
    outcome on the results prefix.
    """

    # Identifiers only: the state carries externalSfnTaskToken, so it is never rendered whole.
    logger.info("Event", jobName=event.get("jobName", ""), assetId=event.get("assetId", ""),
                fileClass=event.get("fileClass", ""), renderBranch=event.get("renderBranch", ""),
                analysisManifestS3Location=event.get("analysisManifestS3Location", ""),
                eventKeys=sorted(event))
    logger.info(f"Context: {context}")

    manifest_uri = event["analysisManifestS3Location"]
    aux_bucket, _key = manifestHelper.parse_s3_uri(manifest_uri)
    manifest = common.read_json(s3_client, manifest_uri)
    warnings = list(manifest.get("warnings") or [])

    # The branch task may have reclassified the file (a text .json promoted to tiles3d or data, an
    # undecodable one demoted to other) and wrote the final class to the manifest; the state follows it.
    file_class = manifest.get("fileClass") or event.get("fileClass", "") or ""
    render_branch = manifest.get("renderBranch") or event.get("renderBranch", "") or ""
    event["fileClass"] = file_class
    event["renderBranch"] = render_branch

    if event.get("renderError"):
        # The render task failed and the machine routed here through RenderDegradePass; the manifest
        # records that the renders are absent so the record is durable.
        manifest["renderSkipped"] = common.RENDER_SKIPPED_ERROR
        manifest["renderImages"] = []
        warnings.append("render failed: " + json.dumps(event["renderError"], default=str)[:512])
        manifest["warnings"] = warnings
        common.write_json(s3_client, manifest_uri, manifest)

    config = manifestHelper.fetch_input_configuration(s3_client, event.get("inputConfigurationS3Location", ""))
    metadata_body = manifestHelper.fetch_metadata(s3_client, event.get("inputMetadataS3Location", ""))
    view = manifestHelper.to_legacy_vams_view(metadata_body, event.get("databaseId", ""),
                                              event.get("assetId", ""), event.get("relativePath", ""))
    vams = view.get("VAMS") or {}
    asset_data = vams.get("assetData") or {}
    attributes = manifest.get("attributes") or {}

    attribute_uri = write_attribute_file(manifest, event["outputS3AssetMetadataPath"], event["relativePath"])
    event["attributeFileS3Location"] = attribute_uri
    logger.info(f"Attributes written: {attribute_uri}")

    seed_on = common.as_bool(config.get("seedWithExistingMetadata"), True)
    max_text_chars = common.as_int(config.get("maxTextChars"), DEFAULT_MAX_TEXT_CHARS)
    write_asset_keywords = common.as_bool(config.get("writeAssetKeywords"), False)
    write_extracted = common.as_bool(config.get("writeExtractedMetadata"), True)
    extract_geo = common.as_bool(config.get("extractGeoLocation"), True)
    raw_vocabulary = config.get("classificationVocabulary")
    vocab = vocabulary.normalize_vocabulary(raw_vocabulary)
    if vocabulary.exceeds_cap(raw_vocabulary):
        warnings.append(f"classificationVocabulary over {vocabulary.VOCABULARY_MAX_BYTES} bytes; the default "
                        "vocabulary was used")

    # The deterministic layer: typed ext_* items and the location, independent of the model.
    promoted = metadataCatalog.promote(attributes, file_class) if write_extracted else []
    location_row = location_item(attributes, file_class, vams.get("fileMetadata") or {}) if extract_geo else None
    promoted_count = len(promoted) + (1 if location_row else 0)
    segments = segment_rows(load_segment_plan(event, warnings))
    metadata_uri = common.uri_join(event["outputS3AssetMetadataPath"],
                                   relative_key(event["relativePath"]) + METADATA_FILE_SUFFIX)

    # The existing metadata of the four envelope scopes, rendered once by the shared helper; the switch
    # decides only whether the prompt sees it — the summary counts it either way.
    existing, dropped = common.existing_metadata_lines(view)
    if dropped:
        logger.warning(f"Existing metadata over the {common.EXISTING_METADATA_MAX_CHARS}-character budget: "
                       f"{len(existing)} lines kept, {dropped} dropped")
    prompt_existing = existing if seed_on else []
    text_excerpt = (manifest.get("textExcerpt") or "")[:max_text_chars]
    image_blocks = load_images(manifest, aux_bucket, warnings)

    modalities = [MODALITY_FILE_ATTRIBUTES]
    if asset_data.get("assetName") or asset_data.get("description") or asset_data.get("tags"):
        modalities.append(MODALITY_ASSET_METADATA)
    if prompt_existing:
        modalities.append(MODALITY_SEED_METADATA)
    if text_excerpt:
        modalities.append(MODALITY_FILE_TEXT)
    if image_blocks:
        modalities.append(image_modality(file_class))

    generated_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    summary = {
        "schemaVersion": common.ANALYSIS_SUMMARY_SCHEMA_VERSION,
        "status": None,
        "error": None,
        "analysisModelId": BEDROCK_ANALYSIS_MODEL_ID,
        "fileClass": file_class,
        "renderBranch": render_branch,
        "renderSkipped": manifest.get("renderSkipped"),
        "imagesSent": len(image_blocks),
        "usage": {},
        # Whether the guardrail's sensitive-information filter masked entities in the prompt or the reply, and
        # the types it masked (never the values); the genai_* rows then carry the filter's type tokens.
        "guardrailMasked": False,
        "guardrailMaskedTypes": [],
        "sourceModalities": modalities,
        "warnings": warnings,
        "vocabularyCorrections": [],
        "promotedFieldCount": promoted_count,
        "existingMetadata": {"lines": len(existing), "dropped": dropped, "promptSeeded": bool(prompt_existing)},
        "generatedAt": generated_at,
        "attributeFile": attribute_uri,
        "metadataFile": None,
    }

    user_blocks = build_user_content(asset_data, event.get("databaseId", ""), event.get("relativePath", ""),
                                     file_class, event.get("fileExt", ""), manifest,
                                     vocabulary.vocabulary_prompt_parts(vocab), prompt_existing,
                                     text_excerpt, len(image_blocks), guarded=GUARDRAIL_CONFIG is not None)
    try:
        result, usage, masked_types = analyze(user_blocks, image_blocks)
        result, corrections = vocabulary.validate_against_vocabulary(result, vocab)
        common.write_json(s3_client, metadata_uri,
                          metadata_file_body(promoted, location_row,
                                             genai_rows(result, modalities, generated_at) + segments))
        event["metadataFileS3Location"] = metadata_uri
        if write_asset_keywords and (result["keywords"] or result["category"]):
            asset_metadata = vams.get("assetMetadata") or {}
            common.write_json(
                s3_client, common.uri_join(event["outputS3AssetMetadataPath"], ASSET_METADATA_FILENAME),
                asset_metadata_body(asset_metadata.get(ASSET_KEYWORDS_KEY, ""), result["keywords"],
                                    asset_metadata.get(ASSET_CATEGORIES_KEY, ""),
                                    [result["category"]] if result["category"] else []))
        summary["usage"] = {"inputTokens": int(usage.get("inputTokens", 0) or 0),
                            "outputTokens": int(usage.get("outputTokens", 0) or 0)}
        summary["guardrailMasked"] = bool(masked_types)
        summary["guardrailMaskedTypes"] = masked_types
        summary["vocabularyCorrections"] = corrections
        summary["status"] = common.STATUS_SUCCEEDED
        summary["metadataFile"] = metadata_uri
        event["analysisStatus"] = common.STATUS_SUCCEEDED
        if masked_types:
            logger.info(f"Guardrail masked {', '.join(masked_types)}; the analysis carries the mask tokens")
        logger.info(f"Metadata written: {metadata_uri} ({promoted_count} promoted, {len(corrections)} corrections)")
    except BedrockAnalysisFailure as failure:
        logger.error(f"Bedrock analysis failed ({failure.code}): {failure.cause}")
        if promoted or location_row or segments:
            # The deterministic layer does not depend on the model; like the attributes, it lands.
            common.write_json(s3_client, metadata_uri, metadata_file_body(promoted, location_row, segments))
            event["metadataFileS3Location"] = metadata_uri
            summary["metadataFile"] = metadata_uri
        common.write_execution_status(s3_client, event["outputS3AssetResultsPath"], failure.code, failure.cause)
        summary["status"] = common.STATUS_FAILED
        summary["error"] = failure.code
        event["analysisStatus"] = common.STATUS_FAILED

    event["analysisSummaryS3Location"] = common.write_json(
        s3_client, common.uri_join(event["outputS3AssetResultsPath"], common.ANALYSIS_SUMMARY_RESULTS_FILENAME),
        summary)
    return event

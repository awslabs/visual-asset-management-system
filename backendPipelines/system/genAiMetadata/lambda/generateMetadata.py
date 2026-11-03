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
``analysisCommon.existing_metadata_lines`` helper; the summary counts those lines either way.

The template's ``metadataWriteTarget`` decides where the descriptive layer lands: ``file`` (the default) writes
the file's ``genai_*`` rows, ``asset`` writes the asset's ``genai_asset_*`` rows with the source file and
version, ``both`` writes both. With ``reuseExistingKeys`` on (the default), a field lands in a key the entity
already carries when the template's ``fieldMapping`` names it, a value a person edited is never overwritten,
a pipeline-owned key the run no longer produces is cleared, and the prompt carries the pipeline's earlier
result (``keyReuse``). Off, the output is the plain ``genai_*`` layer. The normalised result is also written to
the auxiliary prefix (``analysis-result.json``) for the embedding step.

The models and inference settings are deployment settings: a request with images goes to the analysis model,
one without to the text model (``BEDROCK_TEXT_MODEL_ID``, empty = the analysis model); ``BEDROCK_MAX_TOKENS``
and ``BEDROCK_TEMPERATURE`` (sent only when set) shape it; ``ANALYSIS_MODEL_SUPPORTS_IMAGES`` false sends the
attributes and text only. A caught
Bedrock failure is recorded through ``execution.status.json`` on the results prefix and the handler
returns normally, so the attributes and the deterministic metadata already written reach the asset and
the process-output step records the execution FAILED. Only an unexpected fault raises.

With the GenAI layer off (the state's ``genAiAnalysisEnabled``, from the deployment's
``useGenAiAnalysis``), the step ends after the deterministic layer: the attributes, the typed ``ext_*``
promotion and the location are written, no ``genai_*`` row is produced, no Converse call is made, and the
analysis summary records ``analysisMode: attributes-only`` with a SUCCEEDED status so the embedding step
(when vector search is on) still runs from the extracted text and attributes.

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
import executionLogKeys
import manifestHelper
import analysisCommon as common
import bedrockGuardrail
import metadataCatalog
import classificationVocabulary as vocabulary
import keyReuse

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline lambda
# runs against throttling-prone services (Step Functions, Amazon S3, EventBridge) for the length of
# a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = safeLogger(service="SystemGenAiMetadataGenerateMetadata")

s3_client = boto3.client('s3', config=retry_config)
bedrock_runtime = boto3.client('bedrock-runtime', config=retry_config)

# Empty in an attributes-only deployment, which sets no analysis model and never reaches ``analyze``.
BEDROCK_ANALYSIS_MODEL_ID = os.environ.get("BEDROCK_ANALYSIS_MODEL_ID", "")
# The model of a request that carries no image: a text, data or document file, a render that was skipped or
# failed, or every request when the analysis model takes no image input. Empty or absent is the analysis model.
BEDROCK_TEXT_MODEL_ID = os.environ.get("BEDROCK_TEXT_MODEL_ID", "").strip() or BEDROCK_ANALYSIS_MODEL_ID
# Whether the analysis model takes image input. False sends the attributes and text only, never a render.
ANALYSIS_MODEL_SUPPORTS_IMAGES = common.env_flag(os.environ, "ANALYSIS_MODEL_SUPPORTS_IMAGES", True)
GENAI_ANALYSIS_ENABLED = os.environ.get("GENAI_ANALYSIS_ENABLED", "true").strip().lower() == "true"
ANALYSIS_MODE_GENAI = "genai"
ANALYSIS_MODE_ATTRIBUTES_ONLY = "attributes-only"

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
# The deployment's inference settings (bedrockModels.inference): maxTokens defaults to 2048, and a temperature
# the deployment leaves unset is not sent, so the model's own default applies. A malformed value is the
# default, with one warning at cold start.
MAX_TOKENS, _MAX_TOKENS_WARNING = common.max_tokens_from_env(os.environ)
TEMPERATURE, _TEMPERATURE_WARNING = common.temperature_from_env(os.environ)
for _setting_warning in (_MAX_TOKENS_WARNING, _TEMPERATURE_WARNING):
    if _setting_warning:
        logger.warning(_setting_warning)
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
# The normalised analysis result the embedding step composes from, whichever keys its values landed in.
ANALYSIS_RESULT_FILENAME = "analysis-result.json"
ANALYSIS_RESULT_SCHEMA_VERSION = 1
LIST_SEPARATOR = ", "

# Where the template's METADATA_WRITE_TARGET sends the descriptive layer: the file's genai_* rows, the asset's
# genai_asset_* rows, or both. The attributes and the deterministic rows (location, video windows) always go to
# the file.
WRITE_TARGET_FILE = "file"
WRITE_TARGET_ASSET = "asset"
WRITE_TARGET_BOTH = "both"
WRITE_TARGETS = (WRITE_TARGET_FILE, WRITE_TARGET_ASSET, WRITE_TARGET_BOTH)
# The asset's provenance rows: which file the description came from, its version, and when.
ASSET_SOURCE_KEY = "genai_asset_source"
ASSET_SOURCE_VERSION_KEY = "genai_asset_source_version"
ASSET_GENERATED_AT_KEY = "genai_asset_generated_at"
ASSET_SOURCE_FILE_PREFIX = "file:"

TYPE_STRING = "string"
TYPE_MULTILINE_STRING = "multiline_string"
TYPE_DATE = "date"
TYPE_NUMBER = "number"
# Written from the media branch's video window plan when one exists.
SEGMENT_COUNT_KEY = "genai_segment_count"
SEGMENT_INTERVAL_KEY = "genai_segment_interval_seconds"

# (model result key, metadata key, metadataValueType) in the order the rows are written. List-valued
# results are ", "-joined strings: the web's inline_controlled_list widget holds one value. These are
# the descriptive rows and land as file METADATA.
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
    ("orientation", "genai_orientation", TYPE_STRING),
    ("sizeEstimate", "genai_size_estimate", TYPE_STRING),
    ("textSummary", "genai_text_summary", TYPE_MULTILINE_STRING),
)
FILE_FIELDS = tuple(keyReuse.FieldSpec(name, key, value_type) for name, key, value_type in GENAI_KEY_TYPES)

# The asset's descriptive rows for the asset and both write targets, replaced by each run that writes them.
ASSET_FIELDS = (
    keyReuse.FieldSpec("title", "genai_asset_title", TYPE_STRING),
    keyReuse.FieldSpec("description", "genai_asset_description", TYPE_MULTILINE_STRING),
    keyReuse.FieldSpec("keywords", "genai_asset_keywords", TYPE_STRING),
    keyReuse.FieldSpec("category", "genai_asset_category", TYPE_STRING),
    keyReuse.FieldSpec("subcategory", "genai_asset_subcategory", TYPE_STRING),
    keyReuse.FieldSpec("style", "genai_asset_style", TYPE_STRING),
    keyReuse.FieldSpec("materials", "genai_asset_materials", TYPE_STRING),
    keyReuse.FieldSpec("colors", "genai_asset_colors", TYPE_STRING),
)
# On an asset a 2.6 deployment's labeling pipeline described, its keywords key is the asset's keywords key and
# is treated as the pipeline's own.
ASSET_FIELD_ALIASES = {"keywords": keyReuse.LEGACY_ASSET_KEYWORDS_KEY}

# The genai_* rows that describe the run rather than the file — which model, when, from what, and the
# model's complexity grade — land as file ATTRIBUTES beside the ext_* facts. Attributes hold strings.
GENAI_ATTRIBUTE_MODEL_KEY = "genai_model"
GENAI_ATTRIBUTE_GENERATED_AT_KEY = "genai_generated_at"
GENAI_ATTRIBUTE_MODALITIES_KEY = "genai_source_modalities"
GENAI_ATTRIBUTE_COMPLEXITY_KEY = "genai_complexity"
GENAI_ATTRIBUTE_KEYS = (GENAI_ATTRIBUTE_MODEL_KEY, GENAI_ATTRIBUTE_GENERATED_AT_KEY,
                        GENAI_ATTRIBUTE_MODALITIES_KEY, GENAI_ATTRIBUTE_COMPLEXITY_KEY)

MODALITY_FILE_ATTRIBUTES = "file-attributes"
MODALITY_ASSET_METADATA = "asset-metadata"
MODALITY_SEED_METADATA = "seed-metadata"
MODALITY_PREVIOUS_ANALYSIS = "previous-analysis"
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


def attribute_file_body(attributes, promoted_items=(), genai_attribute_rows=()):
    """The ``.attribute.json`` body: one string-typed row per ``sys_*`` key, structured values
    JSON-encoded (file attributes accept only the string type), then the ``ext_*`` items promoted from
    them and the run's ``genai_*`` attribute rows, both re-typed as strings; a null or blank group
    produces no row."""
    rows = []
    for key, value in sorted((attributes or {}).items()):
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, sort_keys=True, default=str)
        else:
            if value is None or not str(value).strip():
                continue
            rendered = str(value)
        rows.append(_row(key, rendered, TYPE_STRING))
    for item in promoted_items or ():
        rows.append(_row(item["metadataKey"], str(item["metadataValue"]), TYPE_STRING))
    for row in genai_attribute_rows or ():
        rows.append(_row(row["metadataKey"], str(row["metadataValue"]), TYPE_STRING))
    return {"type": "attribute", "updateType": "update", "metadata": rows}


def write_attribute_file(manifest, metadata_prefix_uri, relative_path, promoted_items=(), genai_attribute_rows=()):
    uri = common.uri_join(metadata_prefix_uri, relative_key(relative_path) + ATTRIBUTE_FILE_SUFFIX)
    common.write_json(s3_client, uri,
                      attribute_file_body(manifest.get("attributes") or {}, promoted_items, genai_attribute_rows))
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
                     existing_lines, text_excerpt, image_count, images_withheld=False, previous_lines=()):
    """The prompt's parts by name. ``intro``, ``vocabularyOpening``, ``vocabularyClosing`` and ``closing`` are the
    pipeline's own words; ``context``, ``identity``, ``attributes``, ``facts``, ``existing``, ``previous`` and
    ``excerpt`` come from the file and its metadata, and ``vocabularyOptions`` from the template's operator-edited
    configuration. ``vocabulary_parts`` is the ``(opening, options, closing)`` tuple ``vocabulary_prompt_parts``
    returns. ``images_withheld`` says the file has rendered views the analysis model cannot take, so none follows.
    ``previous_lines`` are the ``field: value`` lines of the pipeline's earlier result for this subject."""
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
    elif images_withheld:
        closing = ("No rendered views are sent (this analysis model takes no image input); describe the file "
                   "from its name, attributes and context.")
    closing_lines = [closing] if closing else []
    if previous_lines:
        closing_lines.append("A previous analysis of this file is included; keep its wording where it still "
                             "describes the file.")
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
        "previous": ([PREVIOUS_ANALYSIS_HEADER + "\n" + "\n".join(f"- {line}" for line in previous_lines)]
                     if previous_lines else []),
        "excerpt": ["File text excerpt:\n" + text_excerpt] if text_excerpt else [],
        "closing": closing_lines,
    }


PREVIOUS_ANALYSIS_HEADER = "Previous analysis of this file:"
_USER_TEXT_ORDER = ("intro", "context", "identity", "attributes", "facts", "vocabularyOpening", "vocabularyOptions",
                    "vocabularyClosing", "existing", "previous", "excerpt", "closing")
# Every part that is not the pipeline's own words is guarded: the file-derived parts and the vocabulary's option
# lines, which an operator edits on the template without a deploy. The vocabulary's opening and closing sentences
# are instructions the pipeline wrote, so they stay plain: an imperative sentence inside guardContent is what a
# prompt-attack filter is built to flag, and a hit there would fail every analysis in the deployment.
_GUARDED_PARTS = ("context", "identity", "attributes", "facts", "vocabularyOptions", "existing", "previous",
                  "excerpt")
_UNGUARDED_PARTS = ("intro", "vocabularyOpening", "vocabularyClosing", "closing")


def build_user_text(asset_data, database_id, relative_path, file_class, file_ext, manifest, vocabulary_parts,
                    existing_lines, text_excerpt, image_count, images_withheld=False, previous_lines=()):
    parts = _user_text_parts(asset_data, database_id, relative_path, file_class, file_ext, manifest,
                             vocabulary_parts, existing_lines, text_excerpt, image_count, images_withheld,
                             previous_lines)
    return "\n".join(line for name in _USER_TEXT_ORDER for line in parts[name])


def build_user_content(asset_data, database_id, relative_path, file_class, file_ext, manifest, vocabulary_parts,
                       existing_lines, text_excerpt, image_count, guarded, images_withheld=False,
                       previous_lines=()):
    """The user message's text content blocks. Without a guardrail, one text block in prompt order. With one,
    a text block carrying the pipeline's own instructions (the intro, the vocabulary's opening and closing
    sentences, the render note), then a ``guardContent`` block carrying everything that comes from the file, its
    metadata and the template's vocabulary options, so the guardrail evaluates that input and not the prompt."""
    if not guarded:
        return [{"text": build_user_text(asset_data, database_id, relative_path, file_class, file_ext, manifest,
                                         vocabulary_parts, existing_lines, text_excerpt, image_count,
                                         images_withheld, previous_lines)}]
    parts = _user_text_parts(asset_data, database_id, relative_path, file_class, file_ext, manifest,
                             vocabulary_parts, existing_lines, text_excerpt, image_count, images_withheld,
                             previous_lines)
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


def request_model_id(image_blocks):
    """The model a request goes to: the analysis model when it carries an image, the text model otherwise."""
    return BEDROCK_ANALYSIS_MODEL_ID if image_blocks else BEDROCK_TEXT_MODEL_ID


def analyze(user_blocks, image_blocks, model_id=None):
    """``(result, usage, masked_types)`` after at most MAX_ATTEMPTS Converse calls. A throttle backs off and
    retries, an unparsable reply is re-asked; every other Bedrock error, a guardrail intervention and an
    exhausted attempt budget are a BedrockAnalysisFailure the caller records. With a guardrail configured the
    images travel in ``guardContent`` blocks, like the file-derived text. ``masked_types`` names the entity
    types the guardrail anonymized in the prompt or the reply (empty when none): a masked reply is a complete
    answer and is parsed like any other. ``model_id`` defaults to ``request_model_id(image_blocks)``."""
    content = list(user_blocks) + bedrockGuardrail.user_image_blocks(image_blocks, GUARDRAIL_CONFIG)
    request = {
        "modelId": model_id or request_model_id(image_blocks),
        "system": [{"text": SYSTEM_PROMPT}],
        "messages": [{"role": "user", "content": content}],
        "inferenceConfig": common.inference_config(MAX_TOKENS, TEMPERATURE),
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
            result = parse_model_json(text)
        except ModelResponseError as e:
            last_parse_error = e
            logger.warning(f"Attempt {attempt}: {e}")
            continue
        if GUARDRAIL_CONFIG:
            # The one line that makes the screening auditable: which filters acted on the prompt and on the reply,
            # by policy, type and action — never the text.
            logger.info(f"Guardrail assessment: {bedrockGuardrail.assessment_line(response)}")
        return result, usage, masked_types
    raise BedrockAnalysisFailure(
        common.ERROR_BEDROCK_MODEL,
        f"model returned no parsable JSON after {MAX_ATTEMPTS} attempts: {last_parse_error}")


def result_values(result):
    """``{field: value}`` for every GENAI_KEY_TYPES field as it is written: lists ", "-joined, the primary color
    the first color, ``None`` for a null or blank value."""
    values = dict(result)
    values["primaryColor"] = (result.get("colors") or [None])[0]
    rendered = {}
    for result_key, _metadata_key, _value_type in GENAI_KEY_TYPES:
        value = values.get(result_key)
        if isinstance(value, list):
            value = LIST_SEPARATOR.join(item for item in value if item)
        rendered[result_key] = None if value is None or str(value).strip() == "" else str(value)
    return rendered


def genai_rows(result):
    """The descriptive ``genai_*`` metadata rows in GENAI_KEY_TYPES order; a null or blank value
    produces no row, lists are ", "-joined."""
    values = result_values(result)
    return [_row(metadata_key, values[result_key], value_type)
            for result_key, metadata_key, value_type in GENAI_KEY_TYPES if values[result_key] is not None]


def canonical_genai_values(result):
    """``{genai_* file key: value}`` of the produced fields: the analysis in the pipeline's own key names,
    whichever keys its values landed in on the entity."""
    values = result_values(result)
    return {metadata_key: values[result_key] for result_key, metadata_key, _value_type in GENAI_KEY_TYPES
            if values[result_key] is not None}


def normalize_write_target(raw, warnings):
    """The template's metadataWriteTarget: file (the default), asset or both; any other value is file, warned."""
    text = str(raw or "").strip().lower()
    if text in WRITE_TARGETS:
        return text
    if text:
        warnings.append(f"metadataWriteTarget {str(raw)[:32]!r} is not one of {', '.join(WRITE_TARGETS)}; "
                        f"{WRITE_TARGET_FILE} was used")
    return WRITE_TARGET_FILE


def metadata_schema_fields(metadata_body, scope):
    """The metadata schema's field types for one entity scope (``fileMetadata`` or ``assetMetadata``) as
    ``{key: {"type", "controlledList"}}``, or ``None`` when the run's metadata envelope carries no schema section.

    The seam where schema-aware key reuse plugs in: the schema section reaches the envelope with the core change
    that adds it, and until then no envelope carries one, so key reuse holds back every empty person-named key
    (keyReuse module docstring)."""
    return None


def previous_analysis_lines(fields, existing, aliases=None):
    """``field: value`` lines of the pipeline's earlier result on an entity, in field order; the primary color
    repeats the first color and is left out."""
    return [f"{name}: {common.render_existing_value(value)}"
            for name, value in keyReuse.previous_values(fields, existing, aliases) if name != "primaryColor"]


def asset_provenance_rows(relative_path, version_id, generated_at):
    """Which file the asset's description came from (``file:<relativePath>``), its version (empty when the
    bucket is unversioned) and when; written with every asset-target run."""
    return [
        _row(ASSET_SOURCE_KEY, ASSET_SOURCE_FILE_PREFIX + (relative_path or ""), TYPE_STRING),
        _row(ASSET_SOURCE_VERSION_KEY, version_id or "", TYPE_STRING),
        _row(ASSET_GENERATED_AT_KEY, generated_at, TYPE_DATE),
    ]


def digest_row(key, plan):
    """The entity's digest record row when the run changed it, else ``None``."""
    if plan is None or not plan.digests_changed:
        return None
    return _row(key, keyReuse.encode_digest_record(plan.digests), TYPE_STRING)


def genai_attribute_rows(modalities, generated_at, result=None, model_id=None):
    """The run's ``genai_*`` attribute rows: the model the request went to (the analysis model by default),
    timestamp (ISO-8601 text), modalities, and — once the analysis has produced one — the complexity grade.
    All string-typed, as file attributes are."""
    rows = [
        _row(GENAI_ATTRIBUTE_MODEL_KEY, model_id or BEDROCK_ANALYSIS_MODEL_ID, TYPE_STRING),
        _row(GENAI_ATTRIBUTE_GENERATED_AT_KEY, generated_at, TYPE_STRING),
        _row(GENAI_ATTRIBUTE_MODALITIES_KEY, LIST_SEPARATOR.join(modalities), TYPE_STRING),
    ]
    complexity = (result or {}).get("complexity")
    if complexity is not None and str(complexity).strip():
        rows.append(_row(GENAI_ATTRIBUTE_COMPLEXITY_KEY, str(complexity), TYPE_STRING))
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


def metadata_file_body(location_row, genai):
    """The ``.metadata.json`` body: the location item, then the descriptive genai_* rows (and the
    video window rows). The ext_* facts and the run's genai_* provenance are attributes, not metadata."""
    rows = []
    if location_row:
        rows.append(location_row)
    rows.extend(genai or [])
    return {"type": "metadata", "updateType": "update", "metadata": rows}


def asset_metadata_body(rows):
    """``asset.metadata.json``: the asset's descriptive rows, replaced by this run (``updateType: update``
    leaves the asset's other keys alone)."""
    return {"type": "metadata", "updateType": "update", "metadata": list(rows)}


def lambda_handler(event, context):
    """
    GenerateMetadata
    Writes the file's attributes (the sys_* groups, the ext_* facts promoted from them, and the run's
    genai_* provenance), promotes the location, analyses the file with the Bedrock model against the
    template's vocabulary, writes the descriptive genai_* metadata, and records the outcome on the
    results prefix.
    """

    # VAMS reads this function's shared log group for the run's execution ids, so they go on every
    # line; the previous invocation's ids are removed first (see executionLogKeys).
    executionLogKeys.bind_execution_ids(logger, event)

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

    seed_on = common.as_bool(config.get("seedWithExistingMetadata"), True)
    max_text_chars = common.as_int(config.get("maxTextChars"), DEFAULT_MAX_TEXT_CHARS)
    write_target = normalize_write_target(config.get("metadataWriteTarget"), warnings)
    reuse_on = common.as_bool(config.get("reuseExistingKeys"), True)
    field_mapping, mapping_warnings = keyReuse.normalize_field_mapping(config.get("fieldMapping"))
    if reuse_on:
        warnings.extend(mapping_warnings)
    file_metadata = vams.get("fileMetadata") or {}
    asset_metadata = vams.get("assetMetadata") or {}
    writes_file = write_target in (WRITE_TARGET_FILE, WRITE_TARGET_BOTH)
    writes_asset = write_target in (WRITE_TARGET_ASSET, WRITE_TARGET_BOTH)
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
    # With key reuse on, the model also sees the pipeline's own earlier result where this run writes (the file's
    # genai_* rows, or the asset's for the asset target), so titles and keywords stay stable between runs. The
    # seeding switch governs it like the other existing metadata.
    previous_lines = []
    if reuse_on and seed_on:
        previous_lines = (previous_analysis_lines(ASSET_FIELDS, asset_metadata, ASSET_FIELD_ALIASES)
                          if write_target == WRITE_TARGET_ASSET else previous_analysis_lines(FILE_FIELDS, file_metadata))
    text_excerpt = (manifest.get("textExcerpt") or "")[:max_text_chars]
    # An analysis model without image input gets the attributes and text only: the renders are not read and the
    # prompt says why none follows.
    images_withheld = 0 if ANALYSIS_MODEL_SUPPORTS_IMAGES else len((manifest.get("renderImages") or [])[:MAX_IMAGES])
    image_blocks = load_images(manifest, aux_bucket, warnings) if ANALYSIS_MODEL_SUPPORTS_IMAGES else []
    model_id = request_model_id(image_blocks)

    modalities = [MODALITY_FILE_ATTRIBUTES]
    if asset_data.get("assetName") or asset_data.get("description") or asset_data.get("tags"):
        modalities.append(MODALITY_ASSET_METADATA)
    if prompt_existing:
        modalities.append(MODALITY_SEED_METADATA)
    if previous_lines:
        modalities.append(MODALITY_PREVIOUS_ANALYSIS)
    if text_excerpt:
        modalities.append(MODALITY_FILE_TEXT)
    if image_blocks:
        modalities.append(image_modality(file_class))

    generated_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    genai_on = common.as_bool(event.get("genAiAnalysisEnabled"), GENAI_ANALYSIS_ENABLED)

    # The deterministic attributes land before the model runs, so they are durable whatever it does. The
    # genai_* provenance rows describe a model run, so an attributes-only run writes none.
    provenance_rows = genai_attribute_rows(modalities, generated_at, model_id=model_id) if genai_on else ()
    attribute_uri = write_attribute_file(manifest, event["outputS3AssetMetadataPath"], event["relativePath"],
                                         promoted, provenance_rows)
    event["attributeFileS3Location"] = attribute_uri
    logger.info(f"Attributes written: {attribute_uri} ({len(promoted)} promoted)")

    summary = {
        "schemaVersion": common.ANALYSIS_SUMMARY_SCHEMA_VERSION,
        "status": None,
        "error": None,
        "analysisMode": ANALYSIS_MODE_GENAI if genai_on else ANALYSIS_MODE_ATTRIBUTES_ONLY,
        "analysisModelId": model_id if genai_on else None,
        "fileClass": file_class,
        "renderBranch": render_branch,
        "renderSkipped": manifest.get("renderSkipped"),
        "imagesSent": len(image_blocks),
        # Render images the analysis model could not take (ANALYSIS_MODEL_SUPPORTS_IMAGES false); 0 otherwise.
        "imagesWithheld": images_withheld,
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
        "writeTarget": write_target,
        "assetMetadataFile": None,
        # Key reuse: which key each field landed in per entity, the keys the run emptied, and how many lines of
        # the pipeline's earlier result the prompt carried.
        "keyReuse": {"enabled": reuse_on, "targets": {}, "cleared": [], "previousValues": len(previous_lines)},
        # Keys the run left alone: a value a person edited, or an empty key whose type is not known yet.
        "keyConflicts": [],
    }

    if not genai_on:
        # Attributes-only: the deterministic metadata is the whole metadata layer, and the step succeeds
        # without touching Bedrock.
        if location_row or segments:
            common.write_json(s3_client, metadata_uri, metadata_file_body(location_row, segments))
            event["metadataFileS3Location"] = metadata_uri
            summary["metadataFile"] = metadata_uri
        summary["status"] = common.STATUS_SUCCEEDED
        event["analysisStatus"] = common.STATUS_SUCCEEDED
        event["analysisSummaryS3Location"] = common.write_json(
            s3_client, common.uri_join(event["outputS3AssetResultsPath"], common.ANALYSIS_SUMMARY_RESULTS_FILENAME),
            summary)
        logger.info(f"Attributes-only analysis complete ({promoted_count} promoted, no model call)")
        return event

    user_blocks = build_user_content(asset_data, event.get("databaseId", ""), event.get("relativePath", ""),
                                     file_class, event.get("fileExt", ""), manifest,
                                     vocabulary.vocabulary_prompt_parts(vocab), prompt_existing,
                                     text_excerpt, len(image_blocks), guarded=GUARDRAIL_CONFIG is not None,
                                     images_withheld=bool(images_withheld), previous_lines=previous_lines)
    try:
        result, usage, masked_types = analyze(user_blocks, image_blocks, model_id)
        result, corrections = vocabulary.validate_against_vocabulary(result, vocab)
        values = result_values(result)
        file_plan = asset_plan = None
        file_rows = []
        if writes_file:
            file_plan = keyReuse.plan_entity(
                FILE_FIELDS, values, file_metadata, field_mapping,
                keyReuse.parse_digest_record((vams.get("fileAttributes") or {}).get(keyReuse.FILE_DIGEST_KEY)),
                reuse=reuse_on, schema_fields=metadata_schema_fields(metadata_body, "fileMetadata"))
            file_rows = file_plan.rows
        metadata_rows = metadata_file_body(location_row, file_rows + segments)
        if metadata_rows["metadata"]:
            # The asset target writes no descriptive row on the file; its location and video window rows still
            # describe the file and land there.
            common.write_json(s3_client, metadata_uri, metadata_rows)
            event["metadataFileS3Location"] = metadata_uri
            summary["metadataFile"] = metadata_uri
        # The complexity grade is the one attribute the model supplies: rewrite the file with it, and with the
        # record of the person-named keys this run wrote.
        attribute_rows = genai_attribute_rows(modalities, generated_at, result, model_id)
        file_digest = digest_row(keyReuse.FILE_DIGEST_KEY, file_plan) if reuse_on else None
        if file_digest:
            attribute_rows.append(file_digest)
        write_attribute_file(manifest, event["outputS3AssetMetadataPath"], event["relativePath"],
                             promoted, attribute_rows)
        if writes_asset:
            asset_plan = keyReuse.plan_entity(
                ASSET_FIELDS, values, asset_metadata, field_mapping,
                keyReuse.parse_digest_record(asset_metadata.get(keyReuse.ASSET_DIGEST_KEY)),
                reuse=reuse_on, aliases=ASSET_FIELD_ALIASES,
                schema_fields=metadata_schema_fields(metadata_body, "assetMetadata"))
            asset_rows = asset_plan.rows + asset_provenance_rows(event.get("relativePath", ""),
                                                                 event.get("versionId", ""), generated_at)
            asset_digest = digest_row(keyReuse.ASSET_DIGEST_KEY, asset_plan) if reuse_on else None
            if asset_digest:
                asset_rows.append(asset_digest)
            summary["assetMetadataFile"] = common.write_json(
                s3_client, common.uri_join(event["outputS3AssetMetadataPath"], ASSET_METADATA_FILENAME),
                asset_metadata_body(asset_rows))
        # The analysis in the pipeline's own key names, for the embedding step: the same text whichever keys the
        # values landed in, and present when the asset target wrote no descriptive row on the file.
        event["analysisResultS3Location"] = common.write_json(
            s3_client, common.uri_join(event["inputOutputS3AssetAuxiliaryFilesPath"], ANALYSIS_RESULT_FILENAME),
            {"schemaVersion": ANALYSIS_RESULT_SCHEMA_VERSION, "writeTarget": write_target,
             "values": canonical_genai_values(result)})
        for entity, plan in (("file", file_plan), ("asset", asset_plan)):
            if plan is None:
                continue
            summary["keyReuse"]["targets"][entity] = plan.targets
            summary["keyReuse"]["cleared"].extend(plan.cleared)
            summary["keyConflicts"].extend(dict(conflict, entity=entity) for conflict in plan.conflicts)
        summary["usage"] = {"inputTokens": int(usage.get("inputTokens", 0) or 0),
                            "outputTokens": int(usage.get("outputTokens", 0) or 0)}
        summary["guardrailMasked"] = bool(masked_types)
        summary["guardrailMaskedTypes"] = masked_types
        summary["vocabularyCorrections"] = corrections
        summary["status"] = common.STATUS_SUCCEEDED
        event["analysisStatus"] = common.STATUS_SUCCEEDED
        if masked_types:
            logger.info(f"Guardrail masked {', '.join(masked_types)}; the analysis carries the mask tokens")
        logger.info(f"Metadata written: {metadata_uri} ({promoted_count} promoted, {len(corrections)} corrections)")
    except BedrockAnalysisFailure as failure:
        logger.error(f"Bedrock analysis failed ({failure.code}): {failure.cause}")
        if location_row or segments:
            # The deterministic layer does not depend on the model; like the attributes, it lands.
            common.write_json(s3_client, metadata_uri, metadata_file_body(location_row, segments))
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

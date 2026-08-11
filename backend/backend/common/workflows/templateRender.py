# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Template-tag replacement for per-pipeline input configuration + selected execution fields.

A pipeline's input configuration (today the ``inputParameters`` JSON string; later the upgraded
pipeline input-configuration field) may contain ``{{tagName}}`` template tags that are replaced,
per pipeline task, with values drawn from that task's resolved manifest + execution context. This
lets a pipeline ship a ready-made configuration file with placeholders instead of reconstructing it
field-by-field in its ``vamsExecute`` lambda.

Rendering runs on the execution side (``executeWorkflow`` at launch for pipeline 1, the interim
tracking lambda for pipelines 2+), so each task's tags reflect ITS manifest (with shadowed inputs).
The pipeline receives an already-rendered configuration file; it never renders itself.

Design:
  - **Format-agnostic text substitution.** The renderer operates on the raw configuration TEXT, so
    it works no matter the configuration format (JSON today; YAML / OpenJD later).
  - **Two substitution kinds.** A ``scalar`` tag substitutes a JSON-string-escaped bare value meant
    to sit inside existing quotes (``"databaseId": "{{firstAssetFileDatabaseId}}"``); a ``json`` tag
    substitutes a JSON literal (object / array / number) meant to sit WITHOUT surrounding quotes
    (``"files": {{assetFileKeyArray}}``). Each tag's kind is fixed and documented.
  - **Strict.** An unknown ``{{tag}}`` (one not in the catalog below) raises
    ``MissingTemplateTagError`` rather than being left in place or blanked. This surfaces typos and
    reserves the space for the future dynamic tags (``{{metadata_<key>}}`` and user-defined
    per-pipeline tags) — which are NOT implemented yet and therefore error today.
  - **Empty-not-error for absent sources.** A defined tag whose underlying value is absent (e.g.
    ``{{firstAssetFileAssetId}}`` when the manifest carries no input files) resolves to an empty
    string / ``[]`` / ``0`` rather than raising, so no-input-files executions render cleanly.
  - **Metadata content is loaded lazily.** Metadata-content tags (``{{inputMetadataObject}}`` etc.)
    trigger a single metadata read only when such a tag is actually present in the text.
"""

import json
import os
import re
from datetime import datetime, timezone
# Output escaping only — no XML is parsed here, so there is no XXE/entity-expansion surface for
# defusedxml to harden. saxutils.escape encodes values INTO a document; defusedxml has no equivalent.
# nosemgrep: python.lang.security.use-defused-xml.use-defused-xml
from xml.sax.saxutils import escape as _xml_escape

from common.workflows import templateTags as tags
from common.workflows.templateTagSchema import normalize_tag_type

# A template tag: {{ tagName }} — the name is alphanumeric + underscore. Whitespace inside the
# braces is tolerated. This is intentionally strict so it does not accidentally match JSON/Jinja
# constructs that are not VAMS tags.
_TAG_PATTERN = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")


class MissingTemplateTagError(Exception):
    """Raised when the configuration text uses a ``{{tag}}`` that is not in the catalog.

    Carries the sorted list of unknown tag names so the caller can surface exactly which tags are
    undefined."""

    def __init__(self, unknown_tags):
        self.unknown_tags = sorted(set(unknown_tags))
        super().__init__(
            "Unknown template tag(s) in input configuration: "
            + ", ".join("{{" + t + "}}" for t in self.unknown_tags)
            + ". Only the documented VAMS template tags are supported "
            "(dynamic {{metadata_<key>}} and user-defined per-pipeline tags are not yet available)."
        )


# The rendered-output ceiling. Substitution is expansive: a metadata-content tag emits the whole
# metadata payload at EVERY occurrence, so a small body repeating one tag renders to that payload
# times the occurrence count. The limit is well above any legitimate configuration — several whole
# metadata payloads plus the largest body a caller may submit (models.executions.
# MAX_CUSTOM_TEMPLATE_OVERRIDE_LENGTH) — and is checked as the output accumulates, so an amplifying
# body is refused rather than materialized.
MAX_RENDERED_CONFIG_LENGTH = 16 * 1024 * 1024


class RenderedConfigTooLargeError(Exception):
    """Raised when substitution would render more text than MAX_RENDERED_CONFIG_LENGTH.

    Carries the limit so the caller can name it; the rendered text itself is never carried (it is
    caller content, and the point of the error is that it is too large to hold)."""

    def __init__(self, limit=MAX_RENDERED_CONFIG_LENGTH):
        self.limit = limit
        super().__init__(
            f"The input configuration renders to more than {limit} characters. A template tag that "
            f"substitutes metadata content emits the whole payload at every occurrence, so a body "
            f"repeating such a tag renders far larger than the body itself."
        )


# ---------------------------------------------------------------------------
# Value helpers
# ---------------------------------------------------------------------------

def _s(value):
    """A scalar tag value: coerce to string, empty string for None."""
    return "" if value is None else str(value)


# The config format whose quoted scalars are escaped with XML character references rather than JSON
# string escapes (mirrors the 'xml' member of models.pipelines.TEMPLATE_CONFIG_FORMATS).
CONFIG_FORMAT_XML = "xml"

# The format a text with no declared one is escaped as; also the escape json / yaml / openjd / raw
# quoted scalars share.
CONFIG_FORMAT_JSON = "json"


def escape_scalar(value, config_format=CONFIG_FORMAT_JSON):
    """Escape a scalar tag value for substitution inside the template's own quoting, per config
    format.

    ``xml`` gets XML character references (``&``, ``<``, ``>``, and both quote styles, so the value is
    safe in element text and in an attribute). Every other format gets JSON string escapes with the
    surrounding quotes stripped: that is the quoted-scalar syntax of ``json`` and of the
    double-quoted scalars of ``yaml`` / ``openjd``, and it is also what ``raw`` receives — a raw body
    carries no declared syntax, so the JSON escape keeps a control character or quote from
    terminating the value."""
    text = _s(value)
    if (config_format or "").strip().lower() == CONFIG_FORMAT_XML:
        return _xml_escape(text, {'"': "&quot;", "'": "&apos;"})
    return json.dumps(text)[1:-1]


def _join_s3(bucket, key):
    """Reconstruct ``s3://bucket/key`` from a bucket + bucket-relative key; ``""`` when no bucket."""
    if not bucket:
        return ""
    return f"s3://{bucket}/{key or ''}"


def _file_name(key):
    """Basename of an S3 key (``xid/test/pump.e57`` -> ``pump.e57``); ``""`` when empty."""
    return (key or "").rstrip("/").split("/")[-1]


def _split_ext(key):
    """(stem, ext) of a key's basename: ``pump.e57`` -> (``pump``, ``.e57``)."""
    name = _file_name(key)
    stem, ext = os.path.splitext(name)
    return stem, ext


# ---------------------------------------------------------------------------
# Context construction
# ---------------------------------------------------------------------------

# Metadata-content tag names -> path into the metadata payload (VAMS envelope). A None path means
# the whole payload. These are the only tags that require a metadata read.
_METADATA_TAGS = {
    tags.INPUT_METADATA_OBJECT: None,
    tags.ASSET_METADATA_OBJECT: ("VAMS", "assetMetadata"),
    tags.FILE_METADATA_OBJECT: ("VAMS", "fileMetadata"),
    tags.FILE_ATTRIBUTES_OBJECT: ("VAMS", "fileAttributes"),
    tags.ASSET_DATA_OBJECT: ("VAMS", "assetData"),
    tags.DATABASE_METADATA_OBJECT: ("VAMS", "databaseMetadata"),
}


def render_timestamps(now=None):
    """The job-start timestamp trio, computed at render time. Exposed so callers can build a
    consistent context; ``now`` may be injected for testing."""
    now = now or datetime.now(timezone.utc)
    return {
        "jobStartTimestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "jobStartTimestampUnix": str(int(now.timestamp())),
        "jobStartDate": now.strftime("%Y-%m-%d"),
    }


def build_template_context(manifest, execution, now=None):
    """Build the base (non-metadata) template context: ``{tagName: (kind, value)}`` where kind is
    ``"scalar"`` or ``"json"``.

    ``manifest`` is the per-pipeline manifest envelope for THIS task (see executionRecords.
    build_manifest_envelope). ``execution`` is a dict of the pure-execution scalars the manifest
    does not carry: executionId, workflowId, workflowDatabaseId, pipelineExecutionId, pipelineId,
    pipelineName, pipelineDatabaseId, jobName, triggerType, executingUserName,
    executionStartTimestamp. Any missing key resolves to an empty string (no-input / partial-context
    safe)."""
    manifest = manifest or {}
    execution = execution or {}

    input_files = manifest.get("inputFiles") or []
    first = input_files[0] if input_files else {}

    outputs = manifest.get("outputs") or {}
    output_bucket = outputs.get("bucket", "")
    output_target = manifest.get("outputTarget") or {}
    aux_bucket = manifest.get("auxBucket", "")
    aux_temp_prefix = manifest.get("auxTempPrefix", "")
    aux_preview_suffix = manifest.get("auxPreviewPipelineSuffix", "") or ""
    system_config = manifest.get("systemConfig") or {}

    # First-input-file reconstructed locations (empty string throughout when there are no inputs).
    first_key = first.get("key", "")
    first_bucket = first.get("bucket", "")
    first_aux_preview_prefix = first.get("auxPreviewPrefix", "")
    first_aux_preview_key = first_aux_preview_prefix.rstrip("/") if first_aux_preview_prefix else ""
    if first_aux_preview_key and aux_preview_suffix:
        first_aux_preview_key = f"{first_aux_preview_key}/{aux_preview_suffix.strip('/')}"
    first_stem, first_ext = _split_ext(first_key)

    # Input-file collections.
    key_array = [f.get("key", "") for f in input_files]
    rel_array = [f.get("relativePath", "") for f in input_files]
    s3uri_array = [_join_s3(f.get("bucket", ""), f.get("key", "")) for f in input_files]
    version_array = [f.get("versionId", "") for f in input_files]
    asset_id_array = [f.get("assetId", "") for f in input_files]
    database_id_array = [f.get("databaseId", "") for f in input_files]

    def _unique(seq):
        seen, out = set(), []
        for v in seq:
            if v and v not in seen:
                seen.add(v)
                out.append(v)
        return out

    context = {}

    def scalar(name, value):
        context[name] = ("scalar", _s(value))

    def as_json(name, value):
        context[name] = ("json", value)

    # --- A. Execution & workflow identity ---
    scalar(tags.EXECUTION_ID, execution.get("executionId", ""))
    scalar(tags.WORKFLOW_ID, execution.get("workflowId", ""))
    scalar(tags.WORKFLOW_DATABASE_ID, execution.get("workflowDatabaseId", ""))
    scalar(tags.TRIGGER_TYPE, execution.get("triggerType", ""))
    scalar(tags.EXECUTING_USER_NAME, execution.get("executingUserName", ""))

    # --- B. Pipeline-task identity ---
    scalar(tags.PIPELINE_EXECUTION_ID, execution.get("pipelineExecutionId", ""))
    scalar(tags.PIPELINE_ID, execution.get("pipelineId", ""))
    # The pipeline's display name; its id when the context carries no name.
    scalar(tags.PIPELINE_NAME, execution.get("pipelineName") or execution.get("pipelineId", ""))
    scalar(tags.PIPELINE_DATABASE_ID, execution.get("pipelineDatabaseId", ""))
    scalar(tags.JOB_NAME, execution.get("jobName", ""))

    # --- C. Timestamps ---
    ts = render_timestamps(now)
    scalar(tags.JOB_START_TIMESTAMP, ts["jobStartTimestamp"])
    scalar(tags.JOB_START_TIMESTAMP_UNIX, ts["jobStartTimestampUnix"])
    scalar(tags.JOB_START_DATE, ts["jobStartDate"])
    scalar(tags.EXECUTION_START_TIMESTAMP, execution.get("executionStartTimestamp", ""))

    # --- D. First input file (empty strings when no inputs) ---
    scalar(tags.FIRST_ASSET_FILE_DATABASE_ID, first.get("databaseId", ""))
    scalar(tags.FIRST_ASSET_FILE_ASSET_ID, first.get("assetId", ""))
    scalar(tags.FIRST_ASSET_FILE_ASSET_BUCKET, first_bucket)
    scalar(tags.FIRST_ASSET_FILE_ASSET_ROOT_S3_KEY, first.get("assetRootS3Key", ""))
    scalar(tags.FIRST_ASSET_FILE_RELATIVE_PATH, first.get("relativePath", ""))
    scalar(tags.FIRST_ASSET_FILE_KEY, first_key)
    scalar(tags.FIRST_ASSET_FILE_VERSION_ID, first.get("versionId", ""))
    scalar(tags.FIRST_ASSET_FILE_AUX_PREVIEW_PREFIX, first_aux_preview_prefix)
    scalar(tags.FIRST_ASSET_FILE_S3_URI, _join_s3(first_bucket, first_key))
    scalar(tags.FIRST_ASSET_FILE_AUX_PREVIEW_S3_URI, _join_s3(aux_bucket, first_aux_preview_key))
    scalar(tags.FIRST_ASSET_FILE_FILE_NAME, _file_name(first_key))
    scalar(tags.FIRST_ASSET_FILE_FILE_NAME_NO_EXT, first_stem)
    scalar(tags.FIRST_ASSET_FILE_FILE_EXTENSION, first_ext)

    # --- E. Input-file collections (JSON literals; count is a bare number) ---
    as_json(tags.ASSET_FILE_KEY_ARRAY, key_array)
    as_json(tags.ASSET_FILE_RELATIVE_PATH_ARRAY, rel_array)
    as_json(tags.ASSET_FILE_S3_URI_ARRAY, s3uri_array)
    as_json(tags.ASSET_FILE_VERSION_ID_ARRAY, version_array)
    as_json(tags.ASSET_FILE_OBJECT_ARRAY, input_files)
    as_json(tags.ASSET_FILE_ASSET_ID_ARRAY, asset_id_array)
    as_json(tags.ASSET_FILE_UNIQUE_ASSET_ID_ARRAY, _unique(asset_id_array))
    as_json(tags.ASSET_FILE_DATABASE_ID_ARRAY, database_id_array)
    as_json(tags.ASSET_FILE_UNIQUE_DATABASE_ID_ARRAY, _unique(database_id_array))
    as_json(tags.ASSET_FILE_COUNT, len(input_files))

    # --- F. Output locations ---
    scalar(tags.OUTPUT_BUCKET, output_bucket)
    scalar(tags.OUTPUT_FILES_PREFIX, outputs.get("files", ""))
    scalar(tags.OUTPUT_FILES_S3_URI, _join_s3(output_bucket, outputs.get("files", "")))
    scalar(tags.OUTPUT_PREVIEWS_PREFIX, outputs.get("previews", ""))
    scalar(tags.OUTPUT_PREVIEWS_S3_URI, _join_s3(output_bucket, outputs.get("previews", "")))
    scalar(tags.OUTPUT_METADATA_PREFIX, outputs.get("metadata", ""))
    scalar(tags.OUTPUT_METADATA_S3_URI, _join_s3(output_bucket, outputs.get("metadata", "")))
    scalar(tags.OUTPUT_RESULTS_PREFIX, outputs.get("results", ""))
    scalar(tags.OUTPUT_RESULTS_S3_URI, _join_s3(output_bucket, outputs.get("results", "")))
    scalar(tags.OUTPUT_TARGET_ASSET_ID, output_target.get("assetId", ""))
    scalar(tags.OUTPUT_TARGET_DATABASE_ID, output_target.get("databaseId", ""))
    scalar(tags.OUTPUT_TARGET_LOCATION_TYPE, output_target.get("locationType", ""))
    scalar(tags.OUTPUT_TARGET_ASSET_ROOT_S3_KEY, output_target.get("assetRootS3Key", ""))
    scalar(tags.OUTPUT_FILE_BASE_EXECUTION_PATH_EXTENSION,
           output_target.get("fileBaseExecutionPathExtension", "/"))

    # --- G. Auxiliary locations ---
    scalar(tags.AUX_BUCKET, aux_bucket)
    scalar(tags.AUX_TEMP_PREFIX, aux_temp_prefix)
    scalar(tags.AUX_TEMP_S3_URI, _join_s3(aux_bucket, aux_temp_prefix))
    scalar(tags.AUX_PREVIEW_PIPELINE_SUFFIX, aux_preview_suffix)

    # --- H. Metadata / configuration locations ---
    scalar(tags.INPUT_METADATA_S3_LOCATION, manifest.get("inputMetadataS3Location", ""))
    scalar(tags.INPUT_CONFIGURATION_S3_LOCATION, execution.get("inputConfigurationS3Location", ""))

    # --- I. System / orchestration ---
    scalar(tags.ORCHESTRATION_BUS_ARN, system_config.get("orchestrationBusArn", ""))
    scalar(tags.ORCHESTRATION_EVENT_PREFIX, system_config.get("orchestrationEventPrefix", ""))

    # --- K. Deadline Cloud (empty until the pipeline configuration supplies them) ---
    # Sourced from the execution context when present (a future pipeline-configuration overhaul
    # will populate them); default to empty so a Deadline OpenJD template referencing them renders
    # today without tripping the strict unknown-tag check.
    for deadline_tag in tags.DEADLINE_TAGS:
        scalar(deadline_tag, execution.get(deadline_tag, ""))

    return context


def _metadata_context(metadata_payload):
    """Build the metadata-content tag context from a loaded metadata payload dict. Each tag is a
    ``json`` (object) substitution; a missing path resolves to an empty object."""
    payload = metadata_payload if isinstance(metadata_payload, dict) else {}
    context = {}
    for tag, path in _METADATA_TAGS.items():
        if path is None:
            value = payload
        else:
            value = payload
            for seg in path:
                value = value.get(seg, {}) if isinstance(value, dict) else {}
        context[tag] = ("json", value if value is not None else {})
    return context


def _substitute(text, context, config_format=CONFIG_FORMAT_JSON, limit=MAX_RENDERED_CONFIG_LENGTH):
    """Replace every ``{{tag}}`` in ``text`` using ``context`` ({tag: (kind, value)}). Raises
    MissingTemplateTagError listing any tags not in the context. Scalars are escaped for
    ``config_format`` (see escape_scalar) so they sit safely inside the template's own quotes; json
    values are emitted as JSON literals.

    Raises RenderedConfigTooLargeError once the output passes ``limit``. The pieces are accumulated
    and measured as they are produced, so an amplifying body stops at the limit rather than building
    the whole result first and being told afterwards."""
    found = set(_TAG_PATTERN.findall(text))
    unknown = found - set(context.keys())
    if unknown:
        raise MissingTemplateTagError(unknown)

    # Each tag's substitution is rendered once and reused: the same value repeated N times costs one
    # serialization rather than N, which matters most for a metadata-content payload.
    rendered_tags = {}

    def _rendered(name):
        if name not in rendered_tags:
            kind, value = context[name]
            rendered_tags[name] = (json.dumps(value) if kind == "json"
                                   else escape_scalar(value, config_format))
        return rendered_tags[name]

    pieces = []
    total = 0
    position = 0
    for match in _TAG_PATTERN.finditer(text):
        pieces.append(text[position:match.start()])
        pieces.append(_rendered(match.group(1)))
        total += (match.start() - position) + len(pieces[-1])
        if total > limit:
            raise RenderedConfigTooLargeError(limit)
        position = match.end()
    pieces.append(text[position:])
    if total + len(pieces[-1]) > limit:
        raise RenderedConfigTooLargeError(limit)

    return "".join(pieces)


def uses_template_tags(text):
    """True if the text contains at least one ``{{tag}}``."""
    return bool(text) and bool(_TAG_PATTERN.search(text))


# ---------------------------------------------------------------------------
# Save-time JSON shape validation
# ---------------------------------------------------------------------------

def _json_literal_tag_shapes():
    """{tagName: JSON literal text} for every tag whose substitution kind is ``json``, taken from the
    renderer's own context so each tag is classified by the kind it is registered with."""
    context = build_template_context({}, {})
    context.update(_metadata_context({}))
    shapes = {}
    for name, (kind, value) in context.items():
        if kind != "json":
            continue
        if isinstance(value, dict):
            shapes[name] = "{}"
        elif isinstance(value, (list, tuple)):
            shapes[name] = "[]"
        else:
            shapes[name] = "0"
    return shapes


JSON_LITERAL_TAG_SHAPES = _json_literal_tag_shapes()

# The tags that render a JSON object or array — the ones that must stand alone as an unquoted value.
STRUCTURED_TAG_NAMES = frozenset(
    name for name, shape in JSON_LITERAL_TAG_SHAPES.items() if shape in ("{}", "[]"))

# The stand-in for a scalar tag: bare text, valid inside the template's own quotes (where a scalar tag
# sits) and invalid as a value on its own (where it renders escaped text that needs those quotes).
SCALAR_TAG_PLACEHOLDER = "vams-tag"

# The JSON literal each USER tag type renders as, keyed by the declared type from a template's tagSchema.
# templateResolution.substitute_user_tags emits a str value as escaped-and-unquoted text and every other
# value through json.dumps, so a declared type predicts the rendered SHAPE: an integer renders `5`, a
# boolean `true`, a string-list `["a"]`. string and enum are absent on purpose — they render text, which
# is what the scalar fallback already covers.
USER_TAG_TYPE_SHAPES = {
    "integer": "0",
    "number": "0.0",
    "boolean": "true",
    "string-list": "[]",
}

# Every typed user tag renders a non-text JSON value, so for all four the placeholder is the whole value
# and takes no quotes. Quoting one is not a syntax error at save — `{"k": "0"}` parses — it is a TYPE
# error that only shows up at run time, when the pipeline reads the string "5" where its schema promised
# 5. The structured-as-string pass makes that detectable: emitting a QUOTED stand-in is valid in a value
# position and breaks a string it sits inside, so the two positions produce different verdicts.
STRUCTURED_USER_TAG_TYPES = frozenset(USER_TAG_TYPE_SHAPES)


def user_tag_shapes(tag_schema):
    """{tagKey: JSON literal text} for the typed user tags in a template's ``tagSchema``.

    Only the types that render a NON-text value appear. A declared type is normalized the same way
    ``validate_tag_schema`` normalizes it, so a schema stored as "INTEGER" is classified as the integer
    it is accepted as rather than falling through to the text stand-in. A malformed or partial schema
    entry is skipped rather than raising: this feeds a structural parse check, and the schema's own
    shape is validated separately by ``validate_tag_schema``.
    """
    shapes = {}
    for field in tag_schema or []:
        if not isinstance(field, dict):
            continue
        key = field.get("tagKey")
        shape = USER_TAG_TYPE_SHAPES.get(normalize_tag_type(field.get("type")))
        if key and shape:
            shapes[key] = shape
    return shapes


def json_body_placeholder_text(text, structured_as_string=False, tag_schema=None):
    """The text with every ``{{tag}}`` replaced by a JSON-valid stand-in for what that tag renders, so
    a json-format body can be parse-checked while its tags are unresolved.

    A json-kind tag becomes a literal of its own shape (``{}`` / ``[]`` / ``0``); a USER tag declared
    with a non-text type in ``tag_schema`` becomes a literal of that type's shape; every other tag
    becomes bare text, which parses inside the template's quotes and fails outside them, matching where
    each kind renders.

    ``tag_schema`` is the template's own declared tags, and it arrives in the same request as the body —
    so a typed user tag is classified by its declaration rather than defaulting to text. Without it the
    four non-text types fall back to the scalar stand-in, which accepts them only inside quotes: exactly
    where they render a string and the pipeline receives the wrong type.

    ``structured_as_string`` swaps the object/array literals for a quoted string, which parses in a
    value position and breaks a string it sits inside. Parsing both forms therefore tells a tag standing
    alone as a value apart from one written inside quotes, where the rendered object would break the
    surrounding JSON."""
    if not text:
        return text or ""

    declared = user_tag_shapes(tag_schema)
    structured_user_tags = {
        field.get("tagKey")
        for field in (tag_schema or [])
        if isinstance(field, dict)
        and normalize_tag_type(field.get("type")) in STRUCTURED_USER_TAG_TYPES
    }

    def _replace(match):
        name = match.group(1)
        # A reserved system name wins over a same-named user tag: the renderer resolves system tags from
        # its own context, so the system shape is what would actually be substituted.
        shape = JSON_LITERAL_TAG_SHAPES.get(name)
        if shape is not None:
            if structured_as_string and name in STRUCTURED_TAG_NAMES:
                return json.dumps(SCALAR_TAG_PLACEHOLDER)
            return shape
        shape = declared.get(name)
        if shape is not None:
            if structured_as_string and name in structured_user_tags:
                return json.dumps(SCALAR_TAG_PLACEHOLDER)
            return shape
        return SCALAR_TAG_PLACEHOLDER

    return _TAG_PATTERN.sub(_replace, text)


def render_config(text, manifest, execution, metadata_loader=None, now=None,
                  config_format=CONFIG_FORMAT_JSON):
    """Render an input-configuration text (or any templated field) against a task's manifest +
    execution context. Returns the rendered text unchanged when it contains no tags.

    ``metadata_loader`` is an optional zero-arg callable returning the metadata payload dict; it is
    invoked at most once, and only when a metadata-content tag is actually present (lazy read).

    ``config_format`` is the format of THIS text, and it decides how a scalar tag value is escaped
    (see escape_scalar). A caller rendering a template's configuration body must pass that template's
    declared configFormat: the default JSON string escape is the quoted-scalar syntax of json / yaml /
    openjd / raw, but it leaves ``&``, ``<`` and ``>`` intact, so applying it to an ``xml`` body emits
    a value that its parser rejects or reads as markup. The default suits a plain templated field (an
    output path, a name), which carries no declared format.

    Raises MissingTemplateTagError on an unknown tag (strict) and RenderedConfigTooLargeError when the
    text renders past MAX_RENDERED_CONFIG_LENGTH."""
    if not uses_template_tags(text):
        return text

    context = build_template_context(manifest, execution, now=now)

    # Only load metadata when a metadata-content tag is present.
    if any(tag in text for tag in _METADATA_TAGS):
        payload = metadata_loader() if callable(metadata_loader) else {}
        context.update(_metadata_context(payload))
    else:
        # Metadata tags are still DEFINED (empty) so their absence-from-text is the only reason
        # they are not populated; they never trip the strict unknown-tag check.
        context.update(_metadata_context({}))

    return _substitute(text, context, config_format)

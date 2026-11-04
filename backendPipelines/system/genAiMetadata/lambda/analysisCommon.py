#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Constants and S3 helpers shared by the SYSTEM GenAI metadata pipeline's Lambdas.

The results-channel file names mirror the backend constants the process-output step reads
(``backend/backend/common/s3PathPatterns.py``); a pipeline code asset cannot import the backend
package, so the values are restated here and pinned equal by the test suite.

The existing-metadata helpers render the execution envelope's four scopes — the file's, the asset's
and the database's metadata and the file's non-pipeline attributes — into the ``key: value`` lines the
analysis prompt and the embedding text both consume, under one whole-line budget.

The model-settings helpers read the deployment's inference settings from the environment the CDK sets
(``BEDROCK_MAX_TOKENS``, ``BEDROCK_TEMPERATURE``, ``ANALYSIS_MODEL_SUPPORTS_IMAGES``); an absent setting
reproduces the request the pipeline sent before these settings existed. ``model_key_paths`` finds a key
that names a model anywhere in a rendered template configuration outside its operator data (the
classification vocabulary and the field mapping), which the entry Lambda refuses: the models are deployment
settings whose IAM grants are scoped to them at synth.
"""

import json
import math
import re

import manifestHelper

EXECUTION_STATUS_RESULTS_FILENAME = "execution.status.json"
ANALYSIS_SUMMARY_RESULTS_FILENAME = "analysis-summary.json"
ANALYSIS_MANIFEST_FILENAME = "analysis.json"
ANALYSIS_MANIFEST_SCHEMA_VERSION = 1
ANALYSIS_SUMMARY_SCHEMA_VERSION = 1
EMBEDDING_DOCUMENT_SCHEMA_VERSION = 1
STATUS_CAUSE_MAX_CHARS = 1024

STATUS_SUCCEEDED = "SUCCEEDED"
STATUS_FAILED = "FAILED"
STATUS_SKIPPED = "SKIPPED"

ERROR_BEDROCK_ACCESS_DENIED = "BedrockAccessDenied"
ERROR_BEDROCK_MODEL = "BedrockModelError"
ERROR_BEDROCK_THROTTLED = "BedrockThrottled"
ERROR_BEDROCK_EMBEDDING = "BedrockEmbeddingError"
ERROR_BEDROCK_GUARDRAIL_INTERVENED = "BedrockGuardrailIntervened"

# Inference settings of the generative calls (the deployment's bedrockModels.inference block). The default
# and the bounds are the ones the deployment configuration applies; an absent setting keeps today's request.
MAX_TOKENS_ENV = "BEDROCK_MAX_TOKENS"
TEMPERATURE_ENV = "BEDROCK_TEMPERATURE"
DEFAULT_MAX_TOKENS = 2048
MAX_TOKENS_MIN = 256
MAX_TOKENS_MAX = 8192
# The deployment setting that chooses this pipeline's models, named in the refusal of a template model key.
MODEL_SETTING = "app.pipelines.useSystemGenAiMetadata.bedrockModels"

RENDER_SKIPPED_SIZE = "size"
RENDER_SKIPPED_UNSUPPORTED = "unsupported"
RENDER_SKIPPED_ERROR = "error"

# Bedrock error codes that are retried before the run is recorded FAILED.
THROTTLE_ERROR_CODES = frozenset({
    "ThrottlingException", "TooManyRequestsException", "ServiceUnavailableException",
    "ModelNotReadyException", "InternalServerException",
})
# Substrings that identify an Anthropic use-case form rejection; Bedrock reports it under
# ValidationException or AccessDeniedException depending on the account state.
_ACCESS_DENIED_MARKERS = ("FTUFormNotFilled", "use case")

# The legacy-view scopes whose existing entries feed the analysis prompt and the embedding text, most
# specific first; each entry is one "key: value" line.
EXISTING_METADATA_SCOPES = ("fileMetadata", "assetMetadata", "databaseMetadata", "fileAttributes")
# Keys this pipeline writes itself (the promoted ext_* items, the genai_* block, the sys_* attribute
# groups); the current run contributes them fresh, so an earlier run's copy is never repeated.
EXISTING_METADATA_EXCLUDED_PREFIXES = ("ext_", "genai_", "sys_")
# Whole-line budget for the existing metadata: a fixed share of the embedding model's input window.
EXISTING_METADATA_MAX_CHARS = 12000
# Longest rendering of one value; a longer value is cut here and ends with an ellipsis.
EXISTING_VALUE_MAX_CHARS = 400
# The nine GeoJSON type names (RFC 7946 §1.4), the same set fileClassifier.GEOJSON_ROOT_TYPES holds; a
# value of one of these types renders as "<type> geometry" — coordinates carry no semantic signal.
GEOJSON_TYPE_NAMES = frozenset({"Point", "MultiPoint", "LineString", "MultiLineString", "Polygon",
                                "MultiPolygon", "GeometryCollection", "Feature", "FeatureCollection"})
# The member a GeoJSON object carries beside its type (RFC 7946 §3); a bare type word is not GeoJSON.
_GEOJSON_MEMBER_KEYS = {"FeatureCollection": "features", "Feature": "geometry", "GeometryCollection": "geometries"}
_ELLIPSIS = "\u2026"


def join_key(prefix, name):
    """``prefix`` + ``name`` with exactly one slash between them; a blank prefix yields ``name``."""
    prefix = prefix or ""
    if not prefix:
        return name.lstrip("/")
    return prefix.rstrip("/") + "/" + name.lstrip("/")


def uri_join(base_uri, name):
    """``s3://bucket/prefix/name`` for a prefix URI and a file name."""
    bucket, key = manifestHelper.parse_s3_uri(base_uri)
    return f"s3://{bucket}/{join_key(key, name)}"


def read_json(s3_client, uri):
    """The JSON object at ``uri``. Raises ``ValueError`` when the body is not an object."""
    bucket, key = manifestHelper.parse_s3_uri(uri)
    body = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise ValueError(f"{uri} does not hold a JSON object")
    return parsed


def write_json(s3_client, uri, body):
    """Write ``body`` as indented JSON to ``uri``; returns the URI."""
    bucket, key = manifestHelper.parse_s3_uri(uri)
    s3_client.put_object(Bucket=bucket, Key=key, Body=json.dumps(body, indent=2).encode("utf-8"),
                         ContentType="application/json")
    return uri


def write_execution_status(s3_client, results_prefix_uri, error, cause):
    """The reserved results file the process-output step reads to record the execution FAILED."""
    body = {"status": STATUS_FAILED, "error": error, "cause": str(cause)[:STATUS_CAUSE_MAX_CHARS]}
    return write_json(s3_client, uri_join(results_prefix_uri, EXECUTION_STATUS_RESULTS_FILENAME), body)


def bedrock_error_code(exc):
    """The execution error code for a caught Bedrock exception."""
    error = (getattr(exc, "response", None) or {}).get("Error") or {}
    code = str(error.get("Code") or "")
    message = str(error.get("Message") or "")
    if code == "AccessDeniedException" or any(marker in message or marker in code
                                             for marker in _ACCESS_DENIED_MARKERS):
        return ERROR_BEDROCK_ACCESS_DENIED
    if code in THROTTLE_ERROR_CODES:
        return ERROR_BEDROCK_THROTTLED
    return ERROR_BEDROCK_MODEL


def as_bool(value, default):
    """A boolean from a rendered template value: a JSON boolean, or the strings true/false/1/0/yes/no."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in ("true", "1", "yes"):
        return True
    if text in ("false", "0", "no"):
        return False
    return default


def as_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def env_flag(environ, name, default):
    """A ``"true"``/``"false"`` deployment flag; absent or blank is ``default``, any other text is
    ``default`` too."""
    value = str((environ or {}).get(name, "") or "").strip().lower()
    if value == "true":
        return True
    if value == "false":
        return False
    return default


def max_tokens_from_env(environ):
    """``(maxTokens, warning)`` from ``BEDROCK_MAX_TOKENS``: absent or blank is DEFAULT_MAX_TOKENS with no
    warning; a value that is not an integer within MAX_TOKENS_MIN..MAX_TOKENS_MAX (the bounds the deployment
    configuration validates at synth) is DEFAULT_MAX_TOKENS with a warning naming it."""
    raw = str((environ or {}).get(MAX_TOKENS_ENV, "") or "").strip()
    if not raw:
        return DEFAULT_MAX_TOKENS, None
    try:
        value = int(raw)
    except ValueError:
        value = None
    if value is None or not MAX_TOKENS_MIN <= value <= MAX_TOKENS_MAX:
        return DEFAULT_MAX_TOKENS, (f"{MAX_TOKENS_ENV}={raw!r} is not an integer from {MAX_TOKENS_MIN} to "
                                    f"{MAX_TOKENS_MAX}; {DEFAULT_MAX_TOKENS} is used")
    return value, None


def temperature_from_env(environ):
    """``(temperature, warning)`` from ``BEDROCK_TEMPERATURE``: absent or blank is ``None`` (the request
    carries no temperature, so the model's own default applies) with no warning; a value that is not a
    number from 0 to 1 is ``None`` with a warning naming it."""
    raw = str((environ or {}).get(TEMPERATURE_ENV, "") or "").strip()
    if not raw:
        return None, None
    try:
        value = float(raw)
    except ValueError:
        value = None
    if value is None or math.isnan(value) or not 0.0 <= value <= 1.0:
        return None, f"{TEMPERATURE_ENV}={raw!r} is not a number from 0 to 1; the request carries no temperature"
    return value, None


def inference_config(max_tokens, temperature):
    """The Converse ``inferenceConfig``: ``maxTokens`` always, ``temperature`` only when one is set."""
    config = {"maxTokens": max_tokens}
    if temperature is not None:
        config["temperature"] = temperature
    return config


def _model_key_name(key):
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


# Configuration objects whose keys are operator data rather than settings: the classification vocabulary's
# category names and the field mapping's field names. A category named "Product Model ID" names no model, and
# neither object is read for a model, so model_key_paths does not look inside them.
OPERATOR_DATA_KEYS = frozenset({"classificationVocabulary", "fieldMapping"})


def model_key_paths(node, path=""):
    """Dotted paths of every configuration key, at any depth, that names a model: ``modelId`` or any key ending
    in ``ModelId`` in any spelling (``model_id``, ``bedrockModelId``, ``textModelID``). The keys inside an
    OPERATOR_DATA_KEYS object are data and are not checked."""
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            if _model_key_name(key).endswith("modelid"):
                found.append(child)
            if key in OPERATOR_DATA_KEYS:
                continue
            found.extend(model_key_paths(value, child))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(model_key_paths(value, f"{path}[{index}]"))
    return found


def model_key_refusal(config):
    """The refusal message for a rendered template configuration or override that names a model, or
    ``None`` when it names none. The message names every offending key and the deployment setting that
    chooses the models instead."""
    paths = model_key_paths(config)
    if not paths:
        return None
    return (f"The template configuration sets {', '.join(paths[:10])}: this pipeline's Amazon Bedrock models "
            f"are deployment settings ({MODEL_SETTING}), so a template or an override may not name one.")


def new_analysis_manifest(file_class, render_branch, sys_file, render_skipped=None):
    """The analysis manifest as constructPipeline first writes it: ``sys_file`` only, no renders."""
    return {
        "schemaVersion": ANALYSIS_MANIFEST_SCHEMA_VERSION,
        "fileClass": file_class,
        "renderBranch": render_branch,
        "attributes": {"sys_file": sys_file},
        "renderImages": [],
        "textExcerpt": "",
        "facts": {},
        "warnings": [],
        "renderSkipped": render_skipped,
    }


def _geojson_type(value):
    """The type name of a GeoJSON-shaped value — an object, or a JSON string holding one, whose ``type``
    is a GeoJSON type name and that carries the type's member key — else ``None``."""
    parsed = value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped.startswith("{"):
            return None
        try:
            parsed = json.loads(stripped)
        except ValueError:
            return None
    if not isinstance(parsed, dict):
        return None
    geojson_type = parsed.get("type")
    if geojson_type in GEOJSON_TYPE_NAMES and _GEOJSON_MEMBER_KEYS.get(geojson_type, "coordinates") in parsed:
        return geojson_type
    return None


def render_existing_value(value):
    """One existing metadata value as a line of text: whitespace-normalised; a GeoJSON-shaped value
    renders as ``"<type> geometry"``; any other rendering longer than EXISTING_VALUE_MAX_CHARS is cut
    there and ends with an ellipsis."""
    geojson_type = _geojson_type(value)
    if geojson_type:
        return f"{geojson_type} geometry"
    if isinstance(value, (dict, list)):
        rendered = json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)
    else:
        rendered = str(value)
    rendered = " ".join(rendered.split())
    if len(rendered) > EXISTING_VALUE_MAX_CHARS:
        return rendered[:EXISTING_VALUE_MAX_CHARS] + _ELLIPSIS
    return rendered


def existing_metadata_lines(view, max_chars=EXISTING_METADATA_MAX_CHARS):
    """``(kept, dropped)`` for the existing metadata of a legacy ``{"VAMS": {...}}`` view.

    ``kept`` holds ``(scope, "key: value")`` pairs in EXISTING_METADATA_SCOPES order, key-sorted within a
    scope, without blank values and without the keys under EXISTING_METADATA_EXCLUDED_PREFIXES. Lines are
    kept whole while their combined length stays within ``max_chars``: the first line that would cross
    the budget and every line after it are dropped, and ``dropped`` counts them."""
    vams = (view or {}).get("VAMS") or {}
    kept = []
    dropped = 0
    used = 0
    exhausted = False
    for scope in EXISTING_METADATA_SCOPES:
        entries = vams.get(scope) or {}
        if not isinstance(entries, dict):
            continue
        for key in sorted(entries, key=str):
            name = str(key).strip()
            value = entries[key]
            if not name or name.startswith(EXISTING_METADATA_EXCLUDED_PREFIXES):
                continue
            if value is None or not str(value).strip():
                continue
            line = f"{name}: {render_existing_value(value)}"
            if exhausted or used + len(line) > max_chars:
                exhausted = True
                dropped += 1
                continue
            kept.append((scope, line))
            used += len(line)
    return kept, dropped

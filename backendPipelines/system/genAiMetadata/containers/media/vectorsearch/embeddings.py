# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Text embeddings through Amazon Bedrock, one adapter per model family.

Self-contained: standard library and boto3 only, no ``common.*`` or ``customLogging`` imports, because
the same file is vendored byte-identically into the system GenAI metadata pipeline. The NLP search
route embeds queries and the pipeline embeds documents through the same ``embed_text``, so a query and
the vectors it is compared against always come from the same request shape.

The supported families, the model ids each one matches, the vector widths it produces and its text
limit are read from ``embeddingModels.json`` beside this file, which the deployment's configuration
validation reads too, so a model or width the adapter cannot serve is refused at synthesis. The file is
vendored with this module. Truncation is a per-family limit. Titan Text Embeddings V2 accepts 8,192
tokens or 50,000 characters, whichever comes first; 30,000 characters (about 6,400 tokens) leaves
headroom for dense attribute JSON and non-English text. Nova Multimodal Embeddings caps inline text at
8,192 characters and Cohere Embed v3 at 2,048.
"""

import json
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

FAMILY_TITAN_V2 = "titan-v2"
FAMILY_NOVA_MME = "nova-mme"
FAMILY_COHERE_V3 = "cohere-v3"

MODEL_TABLE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "embeddingModels.json")

with open(MODEL_TABLE_PATH, encoding="utf-8") as _table:
    # In table order: the first family whose pattern matches a model id is its family.
    _FAMILIES: Tuple[Dict[str, Any], ...] = tuple(json.load(_table)["families"])

_PATTERNS: Tuple[Tuple[str, re.Pattern], ...] = tuple(
    (entry["family"], re.compile(entry["modelIdPattern"])) for entry in _FAMILIES
)
_MAX_CHARS: Dict[str, int] = {entry["family"]: int(entry["maxInputChars"]) for entry in _FAMILIES}
_DIMENSIONS: Dict[str, Tuple[int, ...]] = {
    entry["family"]: tuple(int(width) for width in entry["dimensions"]) for entry in _FAMILIES
}

TITAN_V2_MAX_INPUT_CHARS = _MAX_CHARS[FAMILY_TITAN_V2]
NOVA_MME_TEXT_MAX_CHARS = _MAX_CHARS[FAMILY_NOVA_MME]
COHERE_V3_MAX_CHARS = _MAX_CHARS[FAMILY_COHERE_V3]

PURPOSES = ("index", "query")

# Bedrock error codes no retry can fix: the request, the model id or the caller's access is wrong.
NON_RETRYABLE_ERROR_CODES = ("ValidationException", "AccessDeniedException", "ResourceNotFoundException")

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

# The Bedrock Runtime client, created on first use so importing the module has no side effect.
_default_client: Optional[Any] = None


class EmbeddingModelError(Exception):
    """A non-retryable embedding failure: unsupported model or dimensions, a rejected request, or a
    response that carries no vector of the configured length. ``code`` names the cause."""

    def __init__(self, message: str, code: str = "EmbeddingModelError"):
        super().__init__(message)
        self.code = code


def slug_model_id(model_id: str) -> str:
    """Lower-case model id with every run of characters outside [a-z0-9] replaced by ``-`` and the ends trimmed."""
    return re.sub(r"[^a-z0-9]+", "-", model_id.lower()).strip("-")


def model_family(model_id: str) -> str:
    """The adapter family of a Bedrock embedding model id."""
    for family, pattern in _PATTERNS:
        if pattern.search(model_id):
            return family
    raise EmbeddingModelError(f"Unsupported embedding model: {model_id!r}", code="UnsupportedModel")


def truncate_for_model(text: str, model_id: str) -> str:
    """``text`` cut to the model family's character limit."""
    return text[: _MAX_CHARS[model_family(model_id)]]


def round_vector(values: List[float], sig: int = 9) -> List[float]:
    """Each value rounded to ``sig`` significant digits (nine round-trips an f32 exactly)."""
    return [float(format(float(value), f".{sig}g")) for value in values]


def _titan_v2_request(text: str, dimensions: int, purpose: str) -> Dict[str, Any]:
    return {"inputText": text, "dimensions": dimensions, "normalize": True}


def _titan_v2_vector(body: Dict[str, Any]) -> List[float]:
    return body["embedding"]


def _nova_mme_request(text: str, dimensions: int, purpose: str) -> Dict[str, Any]:
    return {
        "schemaVersion": "nova-multimodal-embed-v1",
        "taskType": "SINGLE_EMBEDDING",
        "singleEmbeddingParams": {
            "embeddingPurpose": "GENERIC_INDEX" if purpose == "index" else "TEXT_RETRIEVAL",
            "embeddingDimension": dimensions,
            "text": {"truncationMode": "END", "value": text},
        },
    }


def _nova_mme_vector(body: Dict[str, Any]) -> List[float]:
    return body["embeddings"][0]["embedding"]


def _cohere_v3_request(text: str, dimensions: int, purpose: str) -> Dict[str, Any]:
    return {
        "texts": [text],
        "input_type": "search_document" if purpose == "index" else "search_query",
        "truncate": "END",
    }


def _cohere_v3_vector(body: Dict[str, Any]) -> List[float]:
    return body["embeddings"][0]


_ADAPTERS: Dict[str, Tuple[Callable[[str, int, str], Dict[str, Any]], Callable[[Dict[str, Any]], List[float]]]] = {
    FAMILY_TITAN_V2: (_titan_v2_request, _titan_v2_vector),
    FAMILY_NOVA_MME: (_nova_mme_request, _nova_mme_vector),
    FAMILY_COHERE_V3: (_cohere_v3_request, _cohere_v3_vector),
}

# A family in the table without a request adapter would pass the deployment's validation and fail
# every embedding call, so it is refused at import.
if set(_MAX_CHARS) != set(_ADAPTERS):
    raise RuntimeError(
        f"{MODEL_TABLE_PATH} names the families {sorted(_MAX_CHARS)}; this module has adapters for "
        f"{sorted(_ADAPTERS)}"
    )


def build_request_body(text: str, *, model_id: str, dimensions: int, purpose: str = "index") -> Dict[str, Any]:
    """The InvokeModel body for ``model_id``: text truncated to the family limit, dimensions checked
    against what the family supports, and the purpose mapped for asymmetric-role models."""
    if purpose not in PURPOSES:
        raise ValueError(f"purpose must be one of {PURPOSES}, got {purpose!r}")
    family = model_family(model_id)
    if dimensions not in _DIMENSIONS[family]:
        raise EmbeddingModelError(
            f"{model_id} does not produce {dimensions}-dimensional embeddings (supported: {_DIMENSIONS[family]})",
            code="UnsupportedDimensions",
        )
    if not text or not text.strip():
        raise EmbeddingModelError("Cannot embed empty text", code="EmptyInput")
    return _ADAPTERS[family][0](truncate_for_model(text, model_id), dimensions, purpose)


def _bedrock_client():
    global _default_client
    if _default_client is None:
        _default_client = boto3.client("bedrock-runtime", config=retry_config)
    return _default_client


def embed_text(text: str, *, model_id: str, dimensions: int, purpose: str = "index", client=None) -> List[float]:
    """The embedding of ``text`` from ``model_id`` as a list of floats of length ``dimensions``.

    ``purpose`` is ``"index"`` for documents and ``"query"`` for search queries (asymmetric-role models
    embed the two differently; Titan ignores it). ``client`` overrides the module's Bedrock Runtime
    client. Non-retryable Bedrock errors are raised as EmbeddingModelError; other ClientErrors
    (throttling after the adaptive retries) propagate.
    """
    body = build_request_body(text, model_id=model_id, dimensions=dimensions, purpose=purpose)
    bedrock = client if client is not None else _bedrock_client()
    try:
        response = bedrock.invoke_model(
            modelId=model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(body),
        )
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in NON_RETRYABLE_ERROR_CODES:
            raise EmbeddingModelError(f"Bedrock rejected the embedding request for {model_id}: {e}", code=code) from e
        raise

    payload = json.loads(response["body"].read())
    try:
        vector = _ADAPTERS[model_family(model_id)][1](payload)
    except (KeyError, IndexError, TypeError) as e:
        raise EmbeddingModelError(f"Bedrock response for {model_id} carries no embedding", code="BadResponse") from e
    if not isinstance(vector, list) or len(vector) != dimensions:
        got = len(vector) if isinstance(vector, list) else "no"
        raise EmbeddingModelError(
            f"Bedrock returned {got} values for {model_id}; expected {dimensions}", code="DimensionMismatch"
        )
    return [float(value) for value in vector]

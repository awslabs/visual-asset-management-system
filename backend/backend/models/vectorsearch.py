# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Request and response models of POST /search/nlp.

The request is validated here. The response is an OpenSearch-style envelope the handler assembles as
plain dicts (its keys start with underscores); the response models describe that contract for the
OpenAPI document and the web/CLI/MCP clients. Vector-specific values live under ``_vector`` only;
``_source`` carries the same key set as an OpenSearch file hit.
"""

from typing import Any, Dict, List, Literal, Optional

from aws_lambda_powertools.utilities.parser import BaseModel, Field, root_validator, validator

from common.validators import validate

ENTITY_TYPES = ("file", "asset")
WARNING_CODES = (
    "truncated:window",
    "truncated:targets",
    "databases:none_accessible",
    "opensearch:fields_ignored",
    "opensearch:enrichment_failed",
    "segments:window_full",
)
# Constraints only the OpenSearch file index can answer; ignored with a warning when no OpenSearch
# mode is enabled.
OPENSEARCH_ONLY_FIELDS = ("filters", "metadataQuery", "metadataSearchMode", "geoSearch", "tags")
MAX_QUERY_LENGTH = 1000
MAX_DATABASE_IDS = 100
MAX_SIZE = 100
DEFAULT_SIZE = 25
VECTOR_INDEX_LABEL = "vams-vectors"


def _normalise_list(values: List[str], strip_dot: bool = False) -> List[str]:
    """Lower-cased, trimmed, de-duplicated, in first-seen order; ``strip_dot`` drops a leading dot."""
    seen: List[str] = []
    for value in values or []:
        item = value.strip().lower()
        if strip_dot:
            item = item.lstrip(".")
        if item and item not in seen:
            seen.append(item)
    return seen


class NlpSearchRequestModel(BaseModel):
    query: str = Field(..., min_length=1, max_length=MAX_QUERY_LENGTH)
    entityTypes: List[str] = Field(default_factory=lambda: ["file"])
    databaseIds: List[str] = Field(default_factory=list)
    includeArchived: bool = False
    includeSegments: bool = True
    fileClasses: List[str] = Field(default_factory=list)
    fileExtensions: List[str] = Field(default_factory=list)
    size: int = Field(DEFAULT_SIZE, ge=1, le=MAX_SIZE)
    # OpenSearch-only constraints, typed as the /search request models type them.
    filters: Optional[List[Dict[str, Any]]] = None
    metadataQuery: Optional[str] = Field(None, max_length=MAX_QUERY_LENGTH)
    metadataSearchMode: Optional[Literal["key", "value", "both"]] = None
    geoSearch: Optional[Dict[str, Any]] = None
    tags: Optional[List[str]] = None
    enrich: bool = True

    class Config:
        extra = "ignore"

    @validator("entityTypes")
    def _one_entity_kind(cls, value):
        if len(value) != 1 or value[0] not in ENTITY_TYPES:
            raise ValueError(f"entityTypes must be exactly one of {list(ENTITY_TYPES)}")
        return value

    @validator("fileClasses")
    def _normalise_classes(cls, value):
        return _normalise_list(value)

    @validator("fileExtensions")
    def _normalise_extensions(cls, value):
        return _normalise_list(value, strip_dot=True)

    @root_validator(skip_on_failure=True)
    def _validate_database_ids(cls, values):
        ids = values.get("databaseIds") or []
        if len(ids) > MAX_DATABASE_IDS:
            raise ValueError(f"databaseIds accepts at most {MAX_DATABASE_IDS} entries")
        for database_id in ids:
            (valid, message) = validate({"databaseId": {"value": database_id, "validator": "ID"}})
            if not valid:
                raise ValueError(message)
        return values

    def opensearch_only_fields(self) -> List[str]:
        """Names of the OpenSearch-only constraints the caller set, in declaration order."""
        return [name for name in OPENSEARCH_ONLY_FIELDS if getattr(self, name)]


class SegmentRefModel(BaseModel):
    segmentKey: str
    segmentKind: str
    segmentLabel: str
    segmentStartMs: Optional[int] = None
    segmentEndMs: Optional[int] = None


class NlpVectorInfoModel(BaseModel):
    distance: float
    embeddingModelId: str
    sourceModalities: List[str] = Field(default_factory=list)
    indexedAt: str = ""
    fileClass: str = ""
    segmentHits: int = 0
    bestSegment: Optional[SegmentRefModel] = None


class NlpSearchHitModel(BaseModel):
    index_: str = Field(VECTOR_INDEX_LABEL, alias="_index")
    id_: str = Field(..., alias="_id")
    score: float = Field(..., alias="_score")
    index_type: str = Field(..., alias="_index_type")
    source: Dict[str, Any] = Field(default_factory=dict, alias="_source")
    vector: NlpVectorInfoModel = Field(..., alias="_vector")

    class Config:
        allow_population_by_field_name = True


class NlpWarningModel(BaseModel):
    code: str
    message: str

    @validator("code")
    def _known_code(cls, value):
        if value not in WARNING_CODES:
            raise ValueError(f"unknown warning code {value!r}")
        return value


class NlpSummaryModel(BaseModel):
    query: str
    embeddingModelId: str
    databasesSearched: int
    candidatesEvaluated: int
    itemsCollapsed: int
    classIntent: List[str] = Field(default_factory=list)
    truncated: bool = False


class NlpSearchResponseModel(BaseModel):
    took: int
    timed_out: bool = False
    shards: Dict[str, int] = Field(
        default_factory=lambda: {"total": 1, "successful": 1, "skipped": 0, "failed": 0}, alias="_shards"
    )
    hits: Dict[str, Any]
    aggregations: Dict[str, Any] = Field(default_factory=dict)
    aggregationTotal: int = 0
    nlp: NlpSummaryModel
    warnings: List[NlpWarningModel] = Field(default_factory=list)

    class Config:
        allow_population_by_field_name = True

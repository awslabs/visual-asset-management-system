# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Search API models for VAMS OpenSearch integration."""

from __future__ import annotations
from typing import Dict, List, Optional, Literal, Union, Any
from pydantic import Field
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator
from common.validators import validate
from models.metadata import _validate_geojson_value, _validate_lon_lat
from customLogging.logger import safeLogger

logger = safeLogger(service_name="SearchModels")

######################## Request Bounds ##########################

# Upper bound for every search string forwarded to OpenSearch as query text.
MAX_SEARCH_TEXT_LENGTH = 5000

# Upper bound for a field name referenced in a token or sort entry. OpenSearch field
# names are short; the ceiling is generous so metadata-derived names (MD_/AB_ prefixed
# schema keys, themselves capped at 256 characters) always fit.
MAX_SEARCH_FIELD_LENGTH = 512

# Upper bounds on repeated request elements. Each entry expands into its own OpenSearch
# clause, so an unbounded list lets one request drive an arbitrarily large query.
MAX_SEARCH_TOKENS = 100
MAX_SEARCH_FILTERS = 100
MAX_SEARCH_SORT_ENTRIES = 20
MAX_SEARCH_TAGS = 500
MAX_SEARCH_ENTITY_TYPES = 2

# Coordinate positions accepted in a submitted geoJson shape. Generous — far above any
# polygon a map selector produces — while keeping one request from expanding into an
# arbitrarily large geo_shape clause.
MAX_GEOJSON_POSITIONS = 100000

# query_string options a caller may set on a search filter. The whole sub-object is
# forwarded to OpenSearch verbatim, so the key set is closed.
QUERY_STRING_ALLOWED_KEYS = frozenset({
    "query",
    "fields",
    "default_field",
    "default_operator",
    "analyzer",
    "minimum_should_match",
})

# Shape types OpenSearch accepts on a geo_shape query beyond the GeoJSON geometry set:
# 'envelope' (two-corner bounding box) and 'circle' (centre plus radius).
_OPENSEARCH_SHAPE_TYPES = frozenset({"envelope", "circle"})


def _count_geojson_positions(node: Any) -> int:
    """Count the coordinate positions reachable from a GeoJSON value."""
    if isinstance(node, dict):
        return sum(
            _count_geojson_positions(value)
            for key, value in node.items()
            if key in ("coordinates", "geometries", "geometry", "features")
        )
    if isinstance(node, list):
        if node and all(isinstance(entry, (int, float)) for entry in node):
            return 1
        return sum(_count_geojson_positions(entry) for entry in node)
    return 0


def _validate_opensearch_shape(shape: Dict[str, Any], shape_type: str) -> None:
    """Range check the two OpenSearch-specific geo_shape types."""
    coords = shape.get("coordinates")
    if shape_type == "envelope":
        if not isinstance(coords, list) or len(coords) != 2:
            raise ValueError("geoJson envelope requires two corner coordinates")
        _validate_lon_lat(coords[0], "geoJson.coordinates[0]")
        _validate_lon_lat(coords[1], "geoJson.coordinates[1]")
        return
    _validate_lon_lat(coords, "geoJson.coordinates")
    radius = shape.get("radius")
    if radius is None:
        raise ValueError("geoJson circle requires a radius")
    if isinstance(radius, bool) or not isinstance(radius, (int, float, str)):
        raise ValueError("geoJson circle radius must be a number or a distance string")
    if isinstance(radius, str) and len(radius) > 32:
        raise ValueError("geoJson circle radius is too long")


def _validate_geojson_filter(value: Any) -> None:
    """Validate a geoJson search filter before it becomes a geo_shape clause.

    Runs the structural, coordinate-range and linear-ring checks the GEOJSON metadata
    value type uses, so a malformed shape surfaces as a 400 instead of an OpenSearch
    'invalid_shape_exception' at query time. The two OpenSearch shape extensions
    ('envelope', 'circle') are accepted with coordinate range checks.
    """
    if not isinstance(value, dict):
        raise ValueError("geoJson must be a JSON object")
    if _count_geojson_positions(value) > MAX_GEOJSON_POSITIONS:
        raise ValueError(
            f"geoJson contains more than {MAX_GEOJSON_POSITIONS} coordinate positions"
        )
    shape_type = value.get("type")
    if isinstance(shape_type, str) and shape_type.lower() in _OPENSEARCH_SHAPE_TYPES:
        _validate_opensearch_shape(value, shape_type.lower())
        return
    _validate_geojson_value(value, label="geoJson")


######################## Geo Search Models ##########################

class GeoPointModel(BaseModel, extra='ignore'):
    """Single geographic point with optional radius for proximity queries"""
    lat: float = Field(..., ge=-90.0, le=90.0, description="Latitude in decimal degrees")
    lon: float = Field(..., ge=-180.0, le=180.0, description="Longitude in decimal degrees")
    radiusMeters: Optional[float] = Field(None, gt=0, description="Optional radius in meters for proximity search")


class GeoBoundingBoxModel(BaseModel, extra='ignore'):
    """Axis-aligned geographic bounding box"""
    topLeft: GeoPointModel = Field(..., description="Northwest corner of the bounding box")
    bottomRight: GeoPointModel = Field(..., description="Southeast corner of the bounding box")


class GeoSearchModel(BaseModel, extra='ignore'):
    """
    Geospatial filter against the geo_MD_location field.

    Provide exactly one of: point (with optional radiusMeters), bbox, or geoJson.
    The relation controls how the input shape is matched against indexed shapes:
      - intersects (default): any spatial overlap
      - within: indexed shape lies entirely within the input shape
      - contains: indexed shape contains the input shape
      - disjoint: indexed shape has no spatial overlap with the input shape
    """
    relation: Optional[Literal["intersects", "within", "contains", "disjoint"]] = "intersects"
    point: Optional[GeoPointModel] = None
    bbox: Optional[GeoBoundingBoxModel] = None
    geoJson: Optional[Dict[str, Any]] = Field(None, description="Arbitrary GeoJSON geometry, Feature, or FeatureCollection")

    @root_validator
    def validate_geo_search(cls, values):
        provided = [name for name, val in (
            ("point", values.get("point")),
            ("bbox", values.get("bbox")),
            ("geoJson", values.get("geoJson")),
        ) if val is not None]
        if len(provided) == 0:
            raise ValueError("geoSearch requires one of: point, bbox, geoJson")
        if len(provided) > 1:
            raise ValueError(f"geoSearch accepts only one of point, bbox, geoJson (got: {', '.join(provided)})")
        geo_json = values.get("geoJson")
        if geo_json is not None:
            _validate_geojson_filter(geo_json)
        return values


######################## Search Request Models ##########################

class SimpleSearchRequestModel(BaseModel, extra='ignore'):
    """Simple search request model for easy API calls without complex query construction"""
    
    # General search
    query: Optional[str] = Field(None, max_length=MAX_SEARCH_TEXT_LENGTH, strip_whitespace=True, description="General keyword search across all fields")

    # Entity filtering
    entityTypes: Optional[List[Literal["asset", "file"]]] = Field(None, max_items=MAX_SEARCH_ENTITY_TYPES, description="Filter by entity type (default: both asset and file)")
    
    # Asset-specific search parameters
    assetName: Optional[str] = Field(None, max_length=1000, strip_whitespace=True, description="Search by asset name")
    assetId: Optional[str] = Field(None, max_length=1000, strip_whitespace=True, description="Search by asset ID")
    assetType: Optional[str] = Field(None, max_length=1000, strip_whitespace=True, description="Filter by asset type")
    
    # File-specific search parameters
    fileKey: Optional[str] = Field(None, max_length=2000, strip_whitespace=True, description="Search by S3 file key")
    fileExtension: Optional[str] = Field(None, max_length=100, strip_whitespace=True, description="Filter by file extension")
    
    # Common search parameters
    databaseId: Optional[str] = Field(None, max_length=1000, strip_whitespace=True, description="Filter by database ID")
    tags: Optional[List[str]] = Field(None, max_items=MAX_SEARCH_TAGS, description="Search by tags")

    # Metadata search parameters
    metadataKey: Optional[str] = Field(None, max_length=1000, strip_whitespace=True, description="Search metadata field names")
    metadataValue: Optional[str] = Field(None, max_length=MAX_SEARCH_TEXT_LENGTH, strip_whitespace=True, description="Search metadata field values")
    
    # Options
    includeArchived: Optional[bool] = Field(False, description="Include archived items")

    # Geospatial filter against the geo_MD_location field
    geoSearch: Optional[GeoSearchModel] = Field(None, description="Geospatial filter applied to results")

    # Pagination
    from_: Optional[int] = Field(None, alias="from", ge=0, le=10000, description="Starting offset")
    size: Optional[int] = Field(None, ge=1, le=2000, description="Number of results to return")
    
    @root_validator
    def validate_simple_search_request(cls, values):
        """Validate simple search request parameters"""
        # Set defaults
        if values.get('from_') is None:
            values['from_'] = 0
        if values.get('size') is None:
            values['size'] = 100
        
        # Validate pagination
        from_val = values.get('from_', 0)
        size_val = values.get('size', 100)
        
        if from_val + size_val > 10000:
            raise ValueError("Pagination offset + size cannot exceed 10,000 records")
        
        # Validate at least one search parameter is provided
        search_params = [
            values.get('query'),
            values.get('assetName'),
            values.get('assetId'),
            values.get('assetType'),
            values.get('fileKey'),
            values.get('fileExtension'),
            values.get('databaseId'),
            values.get('tags'),
            values.get('metadataKey'),
            values.get('metadataValue')
        ]
        
        if not any(param for param in search_params):
            # Allow empty search for browsing
            pass
        
        # Validate file extension format
        file_ext = values.get('fileExtension')
        if file_ext:
            # Remove leading dot if present and validate format
            if file_ext.startswith('.'):
                values['fileExtension'] = file_ext[1:]
            
            # Validate extension contains only alphanumeric characters
            import re
            if not re.match(r'^[a-zA-Z0-9]+$', values['fileExtension']):
                raise ValueError("File extension must contain only alphanumeric characters")
        
        # Validate tags format
        tags = values.get('tags')
        if tags:
            (valid, message) = validate({
                'tags': {
                    'value': tags,
                    'validator': 'STRING_256_ARRAY'
                }
            })
            if not valid:
                raise ValueError(message)
        
        return values

class SearchTokenModel(BaseModel, extra='ignore'):
    """Model for individual search tokens"""
    operation: Literal["AND", "OR"] = "AND"
    operator: Literal["=", ":", "!=", "!:"] = "="
    propertyKey: Optional[str] = Field(None, max_length=MAX_SEARCH_FIELD_LENGTH)  # None or "all" for multi-field search
    value: str = Field(min_length=1, max_length=MAX_SEARCH_TEXT_LENGTH, strip_whitespace=True)

class SearchFilterModel(BaseModel, extra='ignore'):
    """Model for search filters using query_string syntax.

    The query_string sub-object is forwarded to OpenSearch as a filter clause, so its
    key set is restricted to the query_string options VAMS supports and each value is
    length-bounded.
    """
    query_string: Dict[str, str] = Field(..., description="OpenSearch query_string filter")

    @validator('query_string')
    def validate_query_string(cls, v):
        """Restrict the query_string sub-object to supported, length-bounded options"""
        unsupported = sorted(set(v.keys()) - QUERY_STRING_ALLOWED_KEYS)
        if unsupported:
            raise ValueError(
                "query_string contains unsupported options: "
                f"{', '.join(unsupported)} (supported: {', '.join(sorted(QUERY_STRING_ALLOWED_KEYS))})"
            )
        if 'query' not in v:
            raise ValueError("query_string must contain a 'query' option")
        for key, value in v.items():
            if len(value) > MAX_SEARCH_TEXT_LENGTH:
                raise ValueError(
                    f"query_string option '{key}' exceeds {MAX_SEARCH_TEXT_LENGTH} characters"
                )
        return v

class SearchSortModel(BaseModel, extra='ignore'):
    """Model for search sorting configuration"""
    field: str = Field(min_length=1, max_length=MAX_SEARCH_FIELD_LENGTH, strip_whitespace=True)
    order: Literal["asc", "desc"] = "asc"

class SearchPaginationModel(BaseModel, extra='ignore'):
    """Model for search pagination parameters"""
    from_: Optional[int] = Field(None, alias="from", ge=0, le=10000)  # OpenSearch limit
    size: Optional[int] = Field(None, ge=1, le=2000)  # Reasonable limit for performance
    
    @root_validator
    def validate_pagination(cls, values):
        """Validate pagination parameters"""
        from_val = values.get('from_')
        size_val = values.get('size')
        
        # Set defaults if not provided
        if from_val is None:
            values['from_'] = 0
        if size_val is None:
            values['size'] = 100
            
        # Validate total offset doesn't exceed OpenSearch limits
        if values.get('from_', 0) + values.get('size', 0) > 10000:
            raise ValueError("Pagination offset + size cannot exceed 10,000 records")
            
        return values

class SearchRequestModel(BaseModel, extra='ignore'):
    """Request model for search operations"""
    query: Optional[str] = Field(None, max_length=MAX_SEARCH_TEXT_LENGTH, strip_whitespace=True)  # General text search
    tokens: Optional[List[SearchTokenModel]] = Field([], max_items=MAX_SEARCH_TOKENS)  # Structured search tokens
    filters: Optional[List[SearchFilterModel]] = Field([], max_items=MAX_SEARCH_FILTERS)  # Additional filters
    sort: Optional[List[Union[SearchSortModel, str]]] = Field(["_score"], max_items=MAX_SEARCH_SORT_ENTRIES)  # Sort configuration
    operation: Literal["AND", "OR"] = "AND"  # Default operation for tokens
    entityTypes: Optional[List[Literal["asset", "file"]]] = Field(None, max_items=MAX_SEARCH_ENTITY_TYPES)  # Filter by entity type
    includeArchived: Optional[bool] = False  # Include archived items
    aggregations: Optional[bool] = True  # Include aggregations in response
    
    # NEW: Metadata search controls
    metadataQuery: Optional[str] = Field(None, max_length=MAX_SEARCH_TEXT_LENGTH, strip_whitespace=True)  # Separate metadata search
    metadataSearchMode: Optional[Literal["key", "value", "both"]] = "both"  # Search metadata keys, values, or both
    includeMetadataInSearch: Optional[bool] = True  # Include metadata fields in general search
    
    # NEW: Result explanation controls
    explainResults: Optional[bool] = False  # Include match explanations
    includeHighlights: Optional[bool] = True  # Enhanced highlighting

    # Geospatial filter against the geo_MD_location field
    geoSearch: Optional[GeoSearchModel] = None

    # Pagination (using from/size for compatibility)
    from_: Optional[int] = Field(None, alias="from", ge=0, le=10000)
    size: Optional[int] = Field(None, ge=1, le=2000)
    
    @root_validator
    def validate_search_request(cls, values):
        """Validate search request parameters"""
        # Validate pagination
        from_val = values.get('from_') or 0
        size_val = values.get('size') or 100
        
        if from_val + size_val > 10000:
            raise ValueError("Pagination offset + size cannot exceed 10,000 records")
        
        # Validate query or tokens provided
        query = values.get('query')
        tokens = values.get('tokens', [])
        filters = values.get('filters', [])
        metadata_query = values.get('metadataQuery')
        
        if not query and not tokens and not filters and not metadata_query:
            # Allow empty search for browsing with aggregations
            pass
        
        # Validate sort fields. A bare string entry bypasses SearchSortModel's field
        # bound, so its length is checked here before the name reaches OpenSearch.
        sort_config = values.get('sort', [])
        if sort_config:

            for sort_item in sort_config:
                if isinstance(sort_item, str):
                    if len(sort_item) > MAX_SEARCH_FIELD_LENGTH:
                        raise ValueError(
                            f"sort field names are limited to {MAX_SEARCH_FIELD_LENGTH} characters"
                        )
                    if not sort_item.startswith(('str_', 'num_', 'date_', 'bool_', 'list_')):
                        logger.warning(f"Sort field {sort_item} may not be properly mapped")
                elif isinstance(sort_item, dict):
                    for field_name in sort_item.keys():
                        if len(field_name) > MAX_SEARCH_FIELD_LENGTH:
                            raise ValueError(
                                f"sort field names are limited to {MAX_SEARCH_FIELD_LENGTH} characters"
                            )
                        if not field_name.startswith(('str_', 'num_', 'date_', 'bool_', 'list_')):
                            logger.warning(f"Sort field {field_name} may not be properly mapped")

        return values

######################## Search Response Models ##########################

class SearchHitSourceModel(BaseModel, extra='allow'):
    """Model for search hit source data"""
    # Core fields that should always be present
    _rectype: str  # 'asset' or 'file'
    str_databaseid: Optional[str] = None
    str_assetid: Optional[str] = None
    str_assetname: Optional[str] = None
    str_key: Optional[str] = None  # S3 key for files
    
    # Optional fields that may be present
    str_description: Optional[str] = None
    str_assettype: Optional[str] = None
    str_fileext: Optional[str] = None
    list_tags: Optional[List[str]] = []
    bool_isdistributable: Optional[bool] = None
    date_lastmodified: Optional[str] = None
    num_size: Optional[int] = None  # S3 file size in bytes
    str_etag: Optional[str] = None
    str_s3_version_id: Optional[str] = None  # S3 version ID (if versioning enabled)
    str_asset_version_id: Optional[str] = None  # Current asset version ID

class SearchHitExplanationModel(BaseModel, extra='ignore'):
    """Model for explaining why a result matched"""
    matched_fields: List[str] = []
    match_reasons: Dict[str, str] = {}
    query_type: str
    index_type: str
    score_breakdown: Optional[Dict[str, Union[int, float]]] = None

class SearchHitModel(BaseModel, extra='allow'):
    """Model for individual search hit"""
    _index: str
    _id: str
    _score: Optional[float] = None
    _source: SearchHitSourceModel
    highlight: Optional[Dict[str, List[str]]] = None
    explanation: Optional[SearchHitExplanationModel] = None  # Match explanation
    _index_type: Optional[str] = None  # Custom field we add for dual-index tracking

class SearchHitsModel(BaseModel, extra='ignore'):
    """Model for search hits container"""
    total: Dict[str, Union[int, str]]  # {"value": 100, "relation": "eq"}
    max_score: Optional[float] = None
    hits: List[SearchHitModel]

class AggregationBucketModel(BaseModel, extra='ignore'):
    """Model for aggregation bucket"""
    key: Union[str, int, float]
    doc_count: int

class AggregationModel(BaseModel, extra='ignore'):
    """Model for search aggregations"""
    doc_count: Optional[int] = None
    buckets: Optional[List[AggregationBucketModel]] = None
    
    # For nested aggregations (filtered aggregations) - using Dict to avoid forward reference
    filtered_assettype: Optional[Dict[str, Any]] = None
    filtered_fileext: Optional[Dict[str, Any]] = None
    filtered_databaseid: Optional[Dict[str, Any]] = None
    filtered_tags: Optional[Dict[str, Any]] = None

class SearchResponseModel(BaseModel, extra='ignore'):
    """Response model for search operations"""
    took: int  # Time in milliseconds
    timed_out: bool
    _shards: Dict[str, int]
    hits: SearchHitsModel
    aggregations: Optional[Dict[str, AggregationModel]] = None
    aggregationTotal: Optional[int] = None  # True total from aggregation bucket sums

######################## Index Mapping Models ##########################

class FieldMappingModel(BaseModel, extra='ignore'):
    """Model for individual field mapping"""
    type: str
    fields: Optional[Dict[str, Any]] = None
    format: Optional[str] = None

class IndexMappingPropertiesModel(BaseModel, extra='allow'):
    """Model for index mapping properties"""
    # Allow dynamic properties since mappings can vary

class IndexMappingModel(BaseModel, extra='ignore'):
    """Model for index mappings"""
    dynamic_templates: Optional[List[Dict[str, Any]]] = None
    properties: Optional[Dict[str, Any]] = None

class IndexMappingResponseModel(BaseModel, extra='ignore'):
    """Response model for index mapping requests"""
    mappings: IndexMappingModel

######################## Error Models ##########################

class SearchErrorModel(BaseModel, extra='ignore'):
    """Model for search error responses"""
    error: str
    details: Optional[str] = None
    suggestion: Optional[str] = None

# No need for model_rebuild() with from __future__ import annotations

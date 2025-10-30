# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Search API models for VAMS OpenSearch integration."""

from __future__ import annotations
from typing import Dict, List, Optional, Literal, Union, Any
from pydantic import Field, Extra
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator
from common.validators import validate
from customLogging.logger import safeLogger

logger = safeLogger(service_name="SearchModels")

######################## Search Request Models ##########################

class SimpleSearchRequestModel(BaseModel, extra=Extra.ignore):
    """Simple search request model for easy API calls without complex query construction"""
    
    # General search
    query: Optional[str] = Field(None, max_length=5000, strip_whitespace=True, description="General keyword search across all fields")
    
    # Entity filtering
    entityTypes: Optional[List[Literal["asset", "file"]]] = Field(None, description="Filter by entity type (default: both asset and file)")
    
    # Asset-specific search parameters
    assetName: Optional[str] = Field(None, max_length=1000, strip_whitespace=True, description="Search by asset name")
    assetId: Optional[str] = Field(None, max_length=1000, strip_whitespace=True, description="Search by asset ID")
    assetType: Optional[str] = Field(None, max_length=1000, strip_whitespace=True, description="Filter by asset type")
    
    # File-specific search parameters
    fileKey: Optional[str] = Field(None, max_length=2000, strip_whitespace=True, description="Search by S3 file key")
    fileExtension: Optional[str] = Field(None, max_length=100, strip_whitespace=True, description="Filter by file extension")
    
    # Common search parameters
    databaseId: Optional[str] = Field(None, max_length=1000, strip_whitespace=True, description="Filter by database ID")
    tags: Optional[List[str]] = Field(None, description="Search by tags")
    
    # Metadata search parameters
    metadataKey: Optional[str] = Field(None, max_length=1000, strip_whitespace=True, description="Search metadata field names")
    metadataValue: Optional[str] = Field(None, max_length=5000, strip_whitespace=True, description="Search metadata field values")
    
    # Options
    includeArchived: Optional[bool] = Field(False, description="Include archived items")
    
    # Pagination
    from_: Optional[int] = Field(None, alias="from", ge=0, le=10000, description="Starting offset")
    size: Optional[int] = Field(None, ge=1, le=1000, description="Number of results to return")
    
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

class SearchTokenModel(BaseModel, extra=Extra.ignore):
    """Model for individual search tokens"""
    operation: Literal["AND", "OR"] = "AND"
    operator: Literal["=", ":", "!=", "!:"] = "="
    propertyKey: Optional[str] = None  # None or "all" for multi-field search
    value: str = Field(min_length=1, strip_whitespace=True)

class SearchFilterModel(BaseModel, extra=Extra.ignore):
    """Model for search filters using query_string syntax"""
    query_string: Dict[str, str] = Field(..., description="OpenSearch query_string filter")

class SearchSortModel(BaseModel, extra=Extra.ignore):
    """Model for search sorting configuration"""
    field: str = Field(min_length=1, strip_whitespace=True)
    order: Literal["asc", "desc"] = "asc"

class SearchPaginationModel(BaseModel, extra=Extra.ignore):
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

class SearchRequestModel(BaseModel, extra=Extra.ignore):
    """Request model for search operations"""
    query: Optional[str] = Field(None, max_length=5000, strip_whitespace=True)  # General text search
    tokens: Optional[List[SearchTokenModel]] = []  # Structured search tokens
    filters: Optional[List[SearchFilterModel]] = []  # Additional filters
    sort: Optional[List[Union[SearchSortModel, str]]] = ["_score"]  # Sort configuration
    operation: Literal["AND", "OR"] = "AND"  # Default operation for tokens
    entityTypes: Optional[List[Literal["asset", "file"]]] = None  # Filter by entity type
    includeArchived: Optional[bool] = False  # Include archived items
    aggregations: Optional[bool] = True  # Include aggregations in response
    
    # NEW: Metadata search controls
    metadataQuery: Optional[str] = Field(None, max_length=5000, strip_whitespace=True)  # Separate metadata search
    metadataSearchMode: Optional[Literal["key", "value", "both"]] = "both"  # Search metadata keys, values, or both
    includeMetadataInSearch: Optional[bool] = True  # Include metadata fields in general search
    
    # NEW: Result explanation controls
    explainResults: Optional[bool] = False  # Include match explanations
    includeHighlights: Optional[bool] = True  # Enhanced highlighting
    
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
        
        # Validate sort fields
        sort_config = values.get('sort', [])
        if sort_config:
        
            for sort_item in sort_config:
                if isinstance(sort_item, str):
                    if not sort_item.startswith(('str_', 'num_', 'date_', 'bool_', 'list_')):
                        logger.warning(f"Sort field {sort_item} may not be properly mapped")
                elif isinstance(sort_item, dict):
                    for field_name in sort_item.keys():
                        if not field_name.startswith(('str_', 'num_', 'date_', 'bool_', 'list_')):
                            logger.warning(f"Sort field {field_name} may not be properly mapped")
        
        return values

######################## Search Response Models ##########################

class SearchHitSourceModel(BaseModel, extra=Extra.allow):
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

class SearchHitExplanationModel(BaseModel, extra=Extra.ignore):
    """Model for explaining why a result matched"""
    matched_fields: List[str] = []
    match_reasons: Dict[str, str] = {}
    query_type: str
    index_type: str
    score_breakdown: Optional[Dict[str, Union[int, float]]] = None

class SearchHitModel(BaseModel, extra=Extra.allow):
    """Model for individual search hit"""
    _index: str
    _id: str
    _score: Optional[float] = None
    _source: SearchHitSourceModel
    highlight: Optional[Dict[str, List[str]]] = None
    explanation: Optional[SearchHitExplanationModel] = None  # Match explanation
    _index_type: Optional[str] = None  # Custom field we add for dual-index tracking

class SearchHitsModel(BaseModel, extra=Extra.ignore):
    """Model for search hits container"""
    total: Dict[str, Union[int, str]]  # {"value": 100, "relation": "eq"}
    max_score: Optional[float] = None
    hits: List[SearchHitModel]

class AggregationBucketModel(BaseModel, extra=Extra.ignore):
    """Model for aggregation bucket"""
    key: Union[str, int, float]
    doc_count: int

class AggregationModel(BaseModel, extra=Extra.ignore):
    """Model for search aggregations"""
    doc_count: Optional[int] = None
    buckets: Optional[List[AggregationBucketModel]] = None
    
    # For nested aggregations (filtered aggregations) - using Dict to avoid forward reference
    filtered_assettype: Optional[Dict[str, Any]] = None
    filtered_fileext: Optional[Dict[str, Any]] = None
    filtered_databaseid: Optional[Dict[str, Any]] = None
    filtered_tags: Optional[Dict[str, Any]] = None

class SearchResponseModel(BaseModel, extra=Extra.ignore):
    """Response model for search operations"""
    took: int  # Time in milliseconds
    timed_out: bool
    _shards: Dict[str, int]
    hits: SearchHitsModel
    aggregations: Optional[Dict[str, AggregationModel]] = None

######################## Index Mapping Models ##########################

class FieldMappingModel(BaseModel, extra=Extra.ignore):
    """Model for individual field mapping"""
    type: str
    fields: Optional[Dict[str, Any]] = None
    format: Optional[str] = None

class IndexMappingPropertiesModel(BaseModel, extra=Extra.allow):
    """Model for index mapping properties"""
    # Allow dynamic properties since mappings can vary

class IndexMappingModel(BaseModel, extra=Extra.ignore):
    """Model for index mappings"""
    dynamic_templates: Optional[List[Dict[str, Any]]] = None
    properties: Optional[Dict[str, Any]] = None

class IndexMappingResponseModel(BaseModel, extra=Extra.ignore):
    """Response model for index mapping requests"""
    mappings: IndexMappingModel

######################## Error Models ##########################

class SearchErrorModel(BaseModel, extra=Extra.ignore):
    """Model for search error responses"""
    error: str
    details: Optional[str] = None
    suggestion: Optional[str] = None

# No need for model_rebuild() with from __future__ import annotations

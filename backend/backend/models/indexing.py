"""
Dual indexing models for VAMS OpenSearch - separate file and asset indexes.
Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: Apache-2.0
"""

from typing import Dict, List, Optional, Any, Union
from pydantic import Field, Extra
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator
from customLogging.logger import safeLogger

logger = safeLogger(service_name="DualIndexingModels")

######################## Field Name Sanitization ##########################

def _sanitize_field_name(field_name: str) -> str:
    """
    Sanitize field name for OpenSearch compatibility.
    Removes or replaces characters that are not allowed in OpenSearch field names.
    
    OpenSearch field names:
    - Cannot contain spaces
    - Should only contain alphanumeric characters, underscores, and hyphens
    - Cannot start with underscore (reserved for system fields)
    
    Args:
        field_name: The original field name
        
    Returns:
        Sanitized field name safe for OpenSearch
    """
    import re
    
    # Convert to lowercase
    sanitized = field_name.lower()
    
    # Replace spaces with underscores
    sanitized = sanitized.replace(' ', '_')
    
    # Remove any characters that are not alphanumeric, underscore, or hyphen
    sanitized = re.sub(r'[^a-z0-9_\-]', '', sanitized)
    
    # Remove leading underscores (reserved for system fields)
    sanitized = sanitized.lstrip('_')
    
    # If the field name is now empty or starts with a number, prefix with 'field_'
    if not sanitized or sanitized[0].isdigit():
        sanitized = f"field_{sanitized}"
    
    # Limit length to 255 characters (OpenSearch limit)
    if len(sanitized) > 255:
        sanitized = sanitized[:255]
    
    return sanitized

######################## Field Type Detection ##########################

def _determine_field_name_and_type(field_name: str, field_value: Any) -> tuple[str, Any]:
    """
    Determine the OpenSearch field name and processed value based on the field type.
    Excludes VAMS_* and _* prefixed fields from indexing.
    
    Args:
        field_name: The original field name
        field_value: The field value
        
    Returns:
        Tuple of (opensearch_field_name, processed_value) or (None, None) if excluded
    """
    # Exclude VAMS_* and _* prefixed fields (internal VAMS fields)
    if field_name.startswith('VAMS_') or field_name.startswith('_'):
        return None, None
    
    # Sanitize the field name for OpenSearch compatibility
    sanitized_name = _sanitize_field_name(field_name)
    
    # Handle None values
    if field_value is None:
        return f"str_{sanitized_name}", ""
    
    # Handle different data types
    if isinstance(field_value, bool):
        return f"bool_{sanitized_name}", field_value
    elif isinstance(field_value, (int, float)):
        return f"num_{sanitized_name}", field_value
    elif isinstance(field_value, list):
        # Convert all list items to strings for consistent indexing
        string_list = [str(item) for item in field_value if item is not None]
        return f"list_{sanitized_name}", string_list
    elif isinstance(field_value, dict):
        # Handle GPS coordinates and other structured data
        if 'lat' in field_value and 'lon' in field_value:
            return f"gp_{sanitized_name}", field_value
        else:
            # Convert dict to JSON string for indexing
            import json
            return f"gs_{sanitized_name}", json.dumps(field_value, default=str)
    elif isinstance(field_value, str):
        # Check if it's a date string
        if _is_date_string(field_value):
            return f"date_{sanitized_name}", field_value
        else:
            return f"str_{sanitized_name}", field_value
    else:
        # Convert everything else to string
        return f"str_{sanitized_name}", str(field_value)

def _is_date_string(value: str) -> bool:
    """Check if a string value appears to be a date/datetime"""
    import re
    # ISO 8601 date patterns
    iso_patterns = [
        r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}',  # 2024-01-01T12:00:00
        r'^\d{4}-\d{2}-\d{2}$',                     # 2024-01-01
        r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}',   # 2024-01-01 12:00:00
    ]
    
    for pattern in iso_patterns:
        if re.match(pattern, value):
            return True
    return False

######################## File Index Models ##########################

class FileDocumentModel(BaseModel, extra=Extra.allow):
    """Model for file documents in the file index"""
    
    # Core identification fields
    str_key: str = Field(..., description="Full S3 file path (relative to bucket)")
    str_databaseid: str = Field(..., description="Database ID")
    str_assetid: str = Field(..., description="Asset ID")
    
    # Asset context fields (from asset lookup)
    str_bucketid: Optional[str] = Field(None, description="Bucket ID")
    str_assetname: Optional[str] = Field(None, description="Asset name")
    str_bucketname: Optional[str] = Field(None, description="Bucket name")
    str_bucketprefix: Optional[str] = Field(None, description="Bucket prefix")
    
    # File-specific fields
    str_fileext: Optional[str] = Field(None, description="File extension")
    date_lastmodified: Optional[str] = Field(None, description="Last modified date")
    num_filesize: Optional[int] = Field(None, description="File size in bytes")
    str_etag: Optional[str] = Field(None, description="S3 ETag")
    str_s3_version_id: Optional[str] = Field(None, description="S3 version ID")
    bool_archived: bool = Field(False, description="Archive status (delete marker present)")
    
    # Asset tags inherited from parent asset
    list_tags: Optional[List[str]] = Field(None, description="Asset tags inherited from parent asset")
    
    # Record type identifier
    _rectype: str = Field("file", description="Record type identifier")
    
    def add_metadata_fields(self, metadata: Dict[str, Any]) -> None:
        """Add metadata fields with MD_ prefix"""
        if not metadata:
            return
            
        for field_name, field_value in metadata.items():
            opensearch_field, processed_value = _determine_field_name_and_type(field_name, field_value)
            if opensearch_field is not None:
                # Add MD_ prefix for metadata fields
                metadata_field_name = f"MD_{opensearch_field}"
                setattr(self, metadata_field_name, processed_value)

class FileIndexRequest(BaseModel, extra=Extra.ignore):
    """Request model for file index operations"""
    
    # Primary identifiers
    databaseId: str = Field(..., description="Database ID")
    assetId: str = Field(..., description="Asset ID") 
    filePath: str = Field(..., description="File path relative to asset")
    
    # S3 information
    bucketName: str = Field(..., description="S3 bucket name")
    s3Key: str = Field(..., description="Full S3 key")
    
    # File metadata
    fileSize: Optional[int] = Field(None, description="File size in bytes")
    lastModified: Optional[str] = Field(None, description="Last modified timestamp")
    etag: Optional[str] = Field(None, description="S3 ETag")
    versionId: Optional[str] = Field(None, description="S3 version ID")
    isArchived: bool = Field(False, description="Whether file is archived")
    
    # Additional metadata
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata fields")
    
    # Operation type
    operation: str = Field(..., description="Operation type: index, delete")

######################## Asset Index Models ##########################

class AssetDocumentModel(BaseModel, extra=Extra.allow):
    """Model for asset documents in the asset index"""
    
    # Core identification fields
    str_databaseid: str = Field(..., description="Database ID")
    str_assetid: str = Field(..., description="Asset ID")
    
    # Asset context fields
    str_bucketid: Optional[str] = Field(None, description="Bucket ID")
    str_assetname: Optional[str] = Field(None, description="Asset name")
    str_bucketname: Optional[str] = Field(None, description="Bucket name")
    str_bucketprefix: Optional[str] = Field(None, description="Bucket prefix")
    
    # Asset-specific fields
    str_assettype: Optional[str] = Field(None, description="Asset type")
    str_description: Optional[str] = Field(None, description="Asset description")
    bool_isdistributable: Optional[bool] = Field(None, description="Distributable flag")
    list_tags: Optional[List[str]] = Field(None, description="Asset tags")
    
    # Version information
    str_asset_version_id: Optional[str] = Field(None, description="Current version ID")
    date_asset_version_createdate: Optional[str] = Field(None, description="Version creation date")
    str_asset_version_comment: Optional[str] = Field(None, description="Version comment")
    
    # Relationship flags
    bool_has_asset_children: bool = Field(False, description="Has child assets")
    bool_has_asset_parents: bool = Field(False, description="Has parent assets")
    bool_has_assets_related: bool = Field(False, description="Has related assets")
    
    # Archive status
    bool_archived: bool = Field(False, description="Archive status (#deleted marker)")
    
    # Record type identifier
    _rectype: str = Field("asset", description="Record type identifier")
    
    def add_metadata_fields(self, metadata: Dict[str, Any]) -> None:
        """Add metadata fields with MD_ prefix"""
        if not metadata:
            return
            
        for field_name, field_value in metadata.items():
            opensearch_field, processed_value = _determine_field_name_and_type(field_name, field_value)
            if opensearch_field is not None:
                # Add MD_ prefix for metadata fields
                metadata_field_name = f"MD_{opensearch_field}"
                setattr(self, metadata_field_name, processed_value)

class AssetIndexRequest(BaseModel, extra=Extra.ignore):
    """Request model for asset index operations"""
    
    # Primary identifiers
    databaseId: str = Field(..., description="Database ID")
    assetId: str = Field(..., description="Asset ID")
    
    # Asset information
    assetName: Optional[str] = Field(None, description="Asset name")
    assetType: Optional[str] = Field(None, description="Asset type")
    description: Optional[str] = Field(None, description="Asset description")
    isDistributable: Optional[bool] = Field(None, description="Distributable flag")
    tags: Optional[List[str]] = Field(None, description="Asset tags")
    
    # Bucket information
    bucketId: Optional[str] = Field(None, description="Bucket ID")
    bucketName: Optional[str] = Field(None, description="Bucket name")
    bucketPrefix: Optional[str] = Field(None, description="Bucket prefix")
    
    # Version information
    currentVersionId: Optional[str] = Field(None, description="Current version ID")
    versionCreatedAt: Optional[str] = Field(None, description="Version creation date")
    versionComment: Optional[str] = Field(None, description="Version comment")
    
    # Archive status
    isArchived: bool = Field(False, description="Whether asset is archived")
    
    # Additional metadata
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata fields")
    
    # Operation type
    operation: str = Field(..., description="Operation type: index, delete")

######################## Common Response Models ##########################

class IndexOperationResponse(BaseModel, extra=Extra.ignore):
    """Response model for index operations"""
    success: bool = Field(..., description="Whether operation was successful")
    message: str = Field(..., description="Operation result message")
    documentId: Optional[str] = Field(None, description="Document ID in index")
    indexName: str = Field(..., description="Target index name")
    operation: str = Field(..., description="Operation performed")

class DualIndexStats(BaseModel, extra=Extra.ignore):
    """Statistics for dual index system"""
    fileIndexCount: int = Field(0, description="Number of documents in file index")
    assetIndexCount: int = Field(0, description="Number of documents in asset index")
    lastUpdated: str = Field(..., description="Last update timestamp")

######################## Index Configuration Models ##########################

class FileIndexMapping(BaseModel, extra=Extra.ignore):
    """OpenSearch mapping configuration for file index"""
    
    @staticmethod
    def get_mapping() -> Dict[str, Any]:
        """Get the OpenSearch mapping for file index"""
        return {
            "mappings": {
                "properties": {
                    # Core identification fields
                    "str_key": {"type": "keyword"},
                    "str_databaseid": {"type": "keyword"},
                    "str_assetid": {"type": "keyword"},
                    
                    # Asset context fields
                    "str_bucketid": {"type": "keyword"},
                    "str_assetname": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
                    "str_bucketname": {"type": "keyword"},
                    "str_bucketprefix": {"type": "keyword"},
                    
                    # File-specific fields
                    "str_fileext": {"type": "keyword"},
                    "date_lastmodified": {"type": "date"},
                    "num_filesize": {"type": "long"},
                    "str_etag": {"type": "keyword"},
                    "str_s3_version_id": {"type": "keyword"},
                    "bool_archived": {"type": "boolean"},
                    
                    # Asset tags inherited from parent asset
                    "list_tags": {"type": "keyword"},
                    
                    # Record type
                    "_rectype": {"type": "keyword"},
                    
                    # Dynamic templates for metadata fields
                    "MD_str_*": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
                    "MD_num_*": {"type": "double"},
                    "MD_bool_*": {"type": "boolean"},
                    "MD_date_*": {"type": "date"},
                    "MD_list_*": {"type": "keyword"},
                    "MD_gp_*": {"type": "geo_point"},
                    "MD_gs_*": {"type": "text"}
                }
            },
            "settings": {
                "number_of_shards": 1,
                "number_of_replicas": 0,
                "analysis": {
                    "analyzer": {
                        "default": {
                            "type": "standard"
                        }
                    }
                }
            }
        }

class AssetIndexMapping(BaseModel, extra=Extra.ignore):
    """OpenSearch mapping configuration for asset index"""
    
    @staticmethod
    def get_mapping() -> Dict[str, Any]:
        """Get the OpenSearch mapping for asset index"""
        return {
            "mappings": {
                "properties": {
                    # Core identification fields
                    "str_databaseid": {"type": "keyword"},
                    "str_assetid": {"type": "keyword"},
                    
                    # Asset context fields
                    "str_bucketid": {"type": "keyword"},
                    "str_assetname": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
                    "str_bucketname": {"type": "keyword"},
                    "str_bucketprefix": {"type": "keyword"},
                    
                    # Asset-specific fields
                    "str_assettype": {"type": "keyword"},
                    "str_description": {"type": "text"},
                    "bool_isdistributable": {"type": "boolean"},
                    "list_tags": {"type": "keyword"},
                    
                    # Version information
                    "str_asset_version_id": {"type": "keyword"},
                    "date_asset_version_createdate": {"type": "date"},
                    "str_asset_version_comment": {"type": "text"},
                    
                    # Relationship flags
                    "bool_has_asset_children": {"type": "boolean"},
                    "bool_has_asset_parents": {"type": "boolean"},
                    "bool_has_assets_related": {"type": "boolean"},
                    
                    # Archive status
                    "bool_archived": {"type": "boolean"},
                    
                    # Record type
                    "_rectype": {"type": "keyword"},
                    
                    # Dynamic templates for metadata fields
                    "MD_str_*": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
                    "MD_num_*": {"type": "double"},
                    "MD_bool_*": {"type": "boolean"},
                    "MD_date_*": {"type": "date"},
                    "MD_list_*": {"type": "keyword"},
                    "MD_gp_*": {"type": "geo_point"},
                    "MD_gs_*": {"type": "text"}
                }
            },
            "settings": {
                "number_of_shards": 1,
                "number_of_replicas": 0,
                "analysis": {
                    "analyzer": {
                        "default": {
                            "type": "standard"
                        }
                    }
                }
            }
        }

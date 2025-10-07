"""
Enhanced search handler for VAMS dual-index OpenSearch system.
Supports searching across separate asset and file indexes with advanced metadata capabilities.

Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: Apache-2.0
"""

import os
import boto3
import json
import re
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple, Set
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from common.dynamodb import validate_pagination_info
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from models.common import APIGatewayProxyResponseV2, internal_error, success, validation_error, general_error, authorization_error, VAMSGeneralErrorResponse
from models.search import (
    SearchRequestModel, SimpleSearchRequestModel, SearchResponseModel, SearchHitModel, SearchHitExplanationModel,
    AggregationModel, IndexMappingResponseModel
)

# Configure AWS clients with retry configuration
retry_config = Config(
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

dynamodb = boto3.resource('dynamodb', config=retry_config)
dynamodb_client = boto3.client('dynamodb', config=retry_config)
logger = safeLogger(service_name="DualIndexSearch")

# Global variables for claims and roles
claims_and_roles = {}

# Load environment variables with error handling
try:
    asset_storage_table_name = os.environ["ASSET_STORAGE_TABLE_NAME"]
    database_storage_table_name = os.environ["DATABASE_STORAGE_TABLE_NAME"]
    opensearch_asset_index_ssm_param = os.environ["OPENSEARCH_ASSET_INDEX_SSM_PARAM"]
    opensearch_file_index_ssm_param = os.environ["OPENSEARCH_FILE_INDEX_SSM_PARAM"]
    opensearch_endpoint_ssm_param = os.environ["OPENSEARCH_ENDPOINT_SSM_PARAM"]
    opensearch_type = os.environ.get("OPENSEARCH_TYPE", "serverless")
    auth_table_name = os.environ["AUTH_TABLE_NAME"]
    user_roles_table_name = os.environ["USER_ROLES_TABLE_NAME"]
    roles_table_name = os.environ["ROLES_TABLE_NAME"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

# Get SSM parameter values
def get_ssm_parameter_value(parameter_name: str) -> str:
    """Get SSM parameter value"""
    try:
        ssm_client = boto3.client('ssm', config=retry_config)
        response = ssm_client.get_parameter(Name=parameter_name)
        return response['Parameter']['Value']
    except Exception as e:
        logger.exception(f"Error getting SSM parameter {parameter_name}: {e}")
        raise VAMSGeneralErrorResponse(f"Error getting configuration parameter: {parameter_name}")

# Load OpenSearch configuration from SSM
opensearch_asset_index = get_ssm_parameter_value(opensearch_asset_index_ssm_param)
opensearch_file_index = get_ssm_parameter_value(opensearch_file_index_ssm_param)
opensearch_endpoint = get_ssm_parameter_value(opensearch_endpoint_ssm_param)

# Initialize DynamoDB tables
asset_storage_table = dynamodb.Table(asset_storage_table_name)
database_storage_table = dynamodb.Table(database_storage_table_name)

#######################
# OpenSearch Client Management
#######################

class DualIndexSearchManager:
    """Manages OpenSearch search operations across dual indexes"""
    
    def __init__(self):
        self.client = None
        self.asset_index = opensearch_asset_index
        self.file_index = opensearch_file_index
        self._initialize_client()
    
    def _initialize_client(self):
        """Initialize OpenSearch client"""
        try:
            from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth
            
            # Create OpenSearch client
            host = opensearch_endpoint.replace('https://', '').replace('http://', '')
            region = os.environ.get('AWS_REGION', 'us-east-1')
            service = 'aoss' if opensearch_type == 'serverless' else 'es'
            
            # Use AWSV4SignerAuth which uses boto3 credentials automatically
            credentials = boto3.Session().get_credentials()
            awsauth = AWSV4SignerAuth(credentials, region, service)
            
            self.client = OpenSearch(
                hosts=[{'host': host, 'port': 443}],
                http_auth=awsauth,
                use_ssl=True,
                verify_certs=True,
                connection_class=RequestsHttpConnection,
                pool_maxsize=20,
                timeout=30,
                max_retries=3,
                retry_on_timeout=True
            )
            
            logger.info(f"Initialized dual-index OpenSearch client - Asset: {self.asset_index}, File: {self.file_index}")
        except Exception as e:
            logger.exception(f"Failed to initialize OpenSearch client: {e}")
            raise VAMSGeneralErrorResponse("Failed to initialize search service")
    
    def is_available(self) -> bool:
        """Check if OpenSearch is available"""
        return self.client is not None
    
    def _sort_combined_results(self, hits: List[Dict], sort_config: List) -> List[Dict]:
        """Sort combined results from multiple indexes according to sort configuration
        
        Args:
            hits: List of search hits to sort
            sort_config: Sort configuration from OpenSearch query
            
        Returns:
            Sorted list of hits
        """
        if not sort_config or not hits:
            return hits
        
        logger.info(f"[Sort] Sorting {len(hits)} combined results with config: {sort_config}")
        
        # Build sort key function based on configuration
        def get_sort_key(hit):
            keys = []
            for sort_item in sort_config:
                if isinstance(sort_item, str):
                    if sort_item == "_score":
                        keys.append(hit.get("_score", 0))
                    else:
                        # Get value from _source
                        keys.append(hit.get("_source", {}).get(sort_item, ""))
                elif isinstance(sort_item, dict):
                    # Extract field name and get value
                    for field, config in sort_item.items():
                        value = hit.get("_source", {}).get(field, "")
                        # Handle None values
                        if value is None:
                            value = ""
                        keys.append(value)
            return tuple(keys) if len(keys) > 1 else (keys[0] if keys else "")
        
        # Determine sort order (check first sort item for direction)
        reverse = False
        if sort_config and isinstance(sort_config[0], dict):
            first_sort = sort_config[0]
            for field, config in first_sort.items():
                if isinstance(config, dict) and config.get("order") == "desc":
                    reverse = True
                    break
        
        # Sort the hits
        try:
            sorted_hits = sorted(hits, key=get_sort_key, reverse=reverse)
            logger.info(f"[Sort] Successfully sorted {len(sorted_hits)} hits (reverse={reverse})")
            return sorted_hits
        except Exception as e:
            logger.warning(f"[Sort] Error sorting combined results: {e}, returning unsorted")
            return hits
    
    def get_index_mappings(self) -> Dict[str, Any]:
        """Get mappings for both indexes"""
        if not self.is_available():
            raise VAMSGeneralErrorResponse("Search service is not available")
        
        try:
            asset_mapping = self.client.indices.get_mapping(self.asset_index)
            file_mapping = self.client.indices.get_mapping(self.file_index)
            
            return {
                "asset_index": asset_mapping.get(self.asset_index, {}),
                "file_index": file_mapping.get(self.file_index, {})
            }
        except Exception as e:
            logger.exception(f"Error getting index mappings: {e}")
            raise VAMSGeneralErrorResponse("Error retrieving search index mappings")
    
    def search_dual_index(self, asset_query: Dict[str, Any], file_query: Dict[str, Any], 
                         entity_types: List[str]) -> Dict[str, Any]:
        """Execute search across both indexes based on entity types"""
        if not self.is_available():
            raise VAMSGeneralErrorResponse("Search service is not available")
        
        try:
            results = {
                "took": 0,
                "timed_out": False,
                "_shards": {"total": 0, "successful": 0, "skipped": 0, "failed": 0},
                "hits": {"hits": [], "total": {"value": 0, "relation": "eq"}},
                "aggregations": {}
            }
            
            # Search asset index if requested
            if not entity_types or "asset" in entity_types:
                logger.info("Searching asset index")
                asset_response = self.client.search(body=asset_query, index=self.asset_index)
                
                # Update timing and shard info
                results["took"] += asset_response.get("took", 0)
                results["timed_out"] = results["timed_out"] or asset_response.get("timed_out", False)
                asset_shards = asset_response.get("_shards", {})
                results["_shards"]["total"] += asset_shards.get("total", 0)
                results["_shards"]["successful"] += asset_shards.get("successful", 0)
                results["_shards"]["skipped"] += asset_shards.get("skipped", 0)
                results["_shards"]["failed"] += asset_shards.get("failed", 0)
                
                # Add hits with index identifier
                for hit in asset_response.get("hits", {}).get("hits", []):
                    hit["_index_type"] = "asset"
                    results["hits"]["hits"].append(hit)
                
                # Merge aggregations
                if "aggregations" in asset_response:
                    results["aggregations"].update(asset_response["aggregations"])
                
                # Update total count
                asset_total = asset_response.get("hits", {}).get("total", {}).get("value", 0)
                results["hits"]["total"]["value"] += asset_total
            
            # Search file index if requested
            if not entity_types or "file" in entity_types:
                logger.info("Searching file index")
                file_response = self.client.search(body=file_query, index=self.file_index)
                
                # Update timing and shard info
                results["took"] += file_response.get("took", 0)
                results["timed_out"] = results["timed_out"] or file_response.get("timed_out", False)
                file_shards = file_response.get("_shards", {})
                results["_shards"]["total"] += file_shards.get("total", 0)
                results["_shards"]["successful"] += file_shards.get("successful", 0)
                results["_shards"]["skipped"] += file_shards.get("skipped", 0)
                results["_shards"]["failed"] += file_shards.get("failed", 0)
                
                # Add hits with index identifier
                for hit in file_response.get("hits", {}).get("hits", []):
                    hit["_index_type"] = "file"
                    results["hits"]["hits"].append(hit)
                
                # Merge aggregations (combine with asset aggregations)
                if "aggregations" in file_response:
                    for agg_name, agg_data in file_response["aggregations"].items():
                        if agg_name in results["aggregations"]:
                            # Merge buckets for same aggregation
                            existing_buckets = results["aggregations"][agg_name].get("buckets", [])
                            new_buckets = agg_data.get("buckets", [])
                            
                            # Combine and deduplicate buckets
                            combined_buckets = {}
                            for bucket in existing_buckets + new_buckets:
                                key = bucket.get("key")
                                if key in combined_buckets:
                                    combined_buckets[key]["doc_count"] += bucket.get("doc_count", 0)
                                else:
                                    combined_buckets[key] = bucket
                            
                            results["aggregations"][agg_name]["buckets"] = list(combined_buckets.values())
                        else:
                            results["aggregations"][agg_name] = agg_data
                
                # Update total count
                file_total = file_response.get("hits", {}).get("total", {}).get("value", 0)
                results["hits"]["total"]["value"] += file_total
            
            # Re-sort combined results according to the sort configuration
            # This is necessary because we're merging results from two indexes
            # and need to maintain global sort order
            # sort_config = asset_query.get("sort", file_query.get("sort", ["_score"]))
            # results["hits"]["hits"] = self._sort_combined_results(results["hits"]["hits"], sort_config)
            
            # # Store sort config in results for use in response processing
            # results["_sort_config"] = sort_config
            
            return results
            
        except Exception as e:
            logger.exception(f"Error executing dual-index search: {e}")
            raise VAMSGeneralErrorResponse("Error executing search query")

#######################
# Authorization Management
#######################

class DatabaseAccessManager:
    """Manages database access permissions with enhanced performance for large datasets"""
    
    @staticmethod
    def get_accessible_database(database_id: str, claims_and_roles: Dict[str, Any]) -> Optional[str]:
        """Check if user has access to a specific database"""
        try:
            db_response = database_storage_table.get_item(
                Key={'databaseId': database_id}
            )
            
            database = db_response.get("Item", {})
            if not database:
                return None
            
            # Add Casbin enforcement
            database.update({"object__type": "asset"})
            if len(claims_and_roles.get("tokens", [])) > 0:
                casbin_enforcer = CasbinEnforcer(claims_and_roles)
                if casbin_enforcer.enforce(database, "GET"):
                    return database_id
            
            return None
        except Exception as e:
            logger.exception(f"Error checking database access: {e}")
            return None
    
    @staticmethod
    def get_accessible_databases(claims_and_roles: Dict[str, Any], show_deleted: bool = False, max_databases: int = 10000) -> List[str]:
        """Get list of databases accessible to the user with enhanced pagination for large datasets"""
        try:
            from boto3.dynamodb.types import TypeDeserializer
            deserializer = TypeDeserializer()
            
            # Build scan filter for deleted databases
            operator = "NOT_CONTAINS" if not show_deleted else "CONTAINS"
            db_filter = {
                "databaseId": {
                    "AttributeValueList": [{"S": "#deleted"}],
                    "ComparisonOperator": operator
                }
            }
            
            accessible_databases = []
            processed_count = 0
            
            # Use paginator for efficient scanning of large database tables
            paginator = dynamodb_client.get_paginator('scan')
            
            # Process databases in chunks to handle large numbers efficiently
            for page in paginator.paginate(
                TableName=database_storage_table_name,
                ScanFilter=db_filter,
                PaginationConfig={
                    'PageSize': 100,  # Smaller page size for better memory management
                    'MaxItems': max_databases  # Configurable limit
                }
            ):
                items = page.get('Items', [])
                if not items:
                    break
                
                # Process items in current page
                for item in items:
                    try:
                        deserialized_document = {k: deserializer.deserialize(v) for k, v in item.items()}
                        
                        # Add Casbin enforcement
                        deserialized_document.update({"object__type": "asset"})
                        if len(claims_and_roles.get("tokens", [])) > 0:
                            casbin_enforcer = CasbinEnforcer(claims_and_roles)
                            if casbin_enforcer.enforce(deserialized_document, "GET"):
                                accessible_databases.append(deserialized_document['databaseId'])
                        
                        processed_count += 1
                        
                        # Log progress for large datasets
                        if processed_count % 1000 == 0:
                            logger.info(f"Processed {processed_count} databases, found {len(accessible_databases)} accessible")
                        
                        # Safety check to prevent excessive processing
                        if len(accessible_databases) >= max_databases:
                            logger.warning(f"Reached maximum database limit of {max_databases}, stopping scan")
                            break
                            
                    except Exception as item_error:
                        logger.warning(f"Error processing database item: {item_error}")
                        continue
                
                # Break if we've reached the limit
                if len(accessible_databases) >= max_databases:
                    break
            
            logger.info(f"Database access scan complete: processed {processed_count} databases, found {len(accessible_databases)} accessible")
            return accessible_databases
            
        except Exception as e:
            logger.exception(f"Error getting accessible databases: {e}")
            return []

#######################
# Field Classification
#######################

class FieldClassifier:
    """Classifies fields as core, metadata, or excluded for dual-index system"""
    
    # Core fields for asset index
    ASSET_CORE_FIELDS = {
        'str_assetname', 'str_description', 'str_assettype', 'str_databaseid',
        'str_assetid', 'str_bucketid', 'str_bucketname', 'str_bucketprefix',
        'bool_isdistributable', 'list_tags', 'str_asset_version_id',
        'date_asset_version_createdate', 'str_asset_version_comment',
        'bool_has_asset_children', 'bool_has_asset_parents', 'bool_has_assets_related',
        'bool_archived', '_rectype', '_id', '_score'
    }
    
    # Core fields for file index
    FILE_CORE_FIELDS = {
        'str_key', 'str_databaseid', 'str_assetid', 'str_bucketid', 'str_assetname',
        'str_bucketname', 'str_bucketprefix', 'str_fileext', 'date_lastmodified',
        'num_filesize', 'str_etag', 'str_s3_version_id', 'bool_archived',
        'list_tags', '_rectype', '_id', '_score'
    }
    
    EXCLUDED_PREFIXES = ['VAMS_', '_']  # Skip these entirely
    METADATA_PREFIX = 'MD_'  # Metadata fields prefix
    
    @staticmethod
    def is_metadata_field(field_name: str) -> bool:
        """Check if field is a metadata field"""
        return field_name.startswith(FieldClassifier.METADATA_PREFIX)
    
    @staticmethod
    def is_core_field(field_name: str, index_type: str = "asset") -> bool:
        """Check if field is a core field for the specified index"""
        if index_type == "asset":
            return field_name in FieldClassifier.ASSET_CORE_FIELDS
        elif index_type == "file":
            return field_name in FieldClassifier.FILE_CORE_FIELDS
        return False
    
    @staticmethod
    def is_excluded_field(field_name: str) -> bool:
        """Check if field should be excluded from search"""
        return any(field_name.startswith(prefix) for prefix in FieldClassifier.EXCLUDED_PREFIXES)
    
    @staticmethod
    def get_searchable_core_fields(index_type: str = "asset") -> List[str]:
        """Get list of core fields that should be included in general text search"""
        if index_type == "asset":
            return [
                "str_assetname", "str_description", "str_assettype",
                "list_tags", "str_asset_version_comment"
            ]
        elif index_type == "file":
            return [
                "str_key", "str_assetname", "str_fileext", "str_etag", "list_tags"
            ]
        return []
    
    @staticmethod
    def escape_opensearch_query_string(query: str, preserve_wildcards: bool = False) -> str:
        """Escape special characters for OpenSearch query_string
        
        Args:
            query: The query string to escape
            preserve_wildcards: If True, don't escape * and ? wildcards
        """
        if not query:
            return query
        
        # Characters that need escaping in OpenSearch query_string
        if preserve_wildcards:
            # Don't escape * and ? if user wants wildcards
            special_chars = r'+-=&|><!(){}[]^"~:\/'
        else:
            special_chars = r'+-=&|><!(){}[]^"~*?:\/'
        
        escaped = query
        for char in special_chars:
            escaped = escaped.replace(char, f'\\{char}')
        return escaped

#######################
# Simple Search Query Building
#######################

class SimpleSearchQueryBuilder:
    """Builds OpenSearch queries for simple search requests"""
    
    def __init__(self, database_access_manager: DatabaseAccessManager):
        self.database_access_manager = database_access_manager
        self.field_classifier = FieldClassifier()
    
    def build_simple_dual_index_queries(self, request: SimpleSearchRequestModel, claims_and_roles: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Build simple queries for both asset and file indexes"""
        
        # Get accessible databases
        # Note: includeArchived controls archived assets/files, not deleted databases
        # Always pass False for show_deleted to exclude deleted databases
        accessible_databases = self.database_access_manager.get_accessible_databases(
            claims_and_roles, show_deleted=False
        )
        
        # Build asset query
        asset_query = self._build_simple_index_query(request, accessible_databases, "asset")
        
        # Build file query
        file_query = self._build_simple_index_query(request, accessible_databases, "file")
        
        return asset_query, file_query
    
    def _build_simple_index_query(self, request: SimpleSearchRequestModel, accessible_databases: List[str], index_type: str) -> Dict[str, Any]:
        """Build simple query for specific index type"""
        try:
            # Build base query structure
            query = {
                "from": request.from_ or 0,
                "size": request.size or 100,
                "sort": ["_score"],
                "query": self._build_simple_query_clause(request, accessible_databases, index_type),
                "highlight": self._build_simple_highlight_config(index_type),
                "_source": True,
                "track_total_hits": True,
            }
            
            return query
        except Exception as e:
            logger.exception(f"Error building simple {index_type} query: {e}")
            raise VAMSGeneralErrorResponse(f"Error building simple {index_type} search query")
    
    def _build_simple_query_clause(self, request: SimpleSearchRequestModel, accessible_databases: List[str], index_type: str) -> Dict[str, Any]:
        """Build the main query clause for simple search"""
        must_clauses = []
        must_not_clauses = []
        should_clauses = []
        filter_clauses = []
        
        # Add database access restrictions
        if accessible_databases:
            db_query_string = " OR ".join([f'"{db_id}"' for db_id in accessible_databases])
            filter_clauses.append({
                "query_string": {
                    "query": f"str_databaseid:({db_query_string})"
                }
            })
        else:
            # No accessible databases - return no results
            filter_clauses.append({
                "query_string": {
                    "query": 'str_databaseid:"NOACCESSDATABASE"'
                }
            })
        
        # Add archive exclusions (unless explicitly included)
        if not request.includeArchived:
            must_not_clauses.append({"term": {"bool_archived": True}})
        
        # Build search queries based on parameters
        search_queries = []
        
        # General keyword search
        if request.query:
            escaped_query = self.field_classifier.escape_opensearch_query_string(request.query)
            searchable_fields = self._get_simple_searchable_fields(index_type)
            search_queries.append({
                "query_string": {
                    "query": f"*{escaped_query}*",
                    "fields": searchable_fields,
                    "default_operator": "OR",
                    "analyze_wildcard": True,
                    "lenient": True
                }
            })
        
        # Asset-specific searches (only for asset index or when searching both)
        if index_type == "asset":
            if request.assetName:
                escaped_name = self.field_classifier.escape_opensearch_query_string(request.assetName)
                search_queries.append({
                    "query_string": {
                        "query": f"str_assetname:*{escaped_name}*",
                        "default_operator": "OR",
                        "analyze_wildcard": True,
                        "lenient": True
                    }
                })
            
            if request.assetId:
                escaped_id = self.field_classifier.escape_opensearch_query_string(request.assetId)
                search_queries.append({
                    "query_string": {
                        "query": f"str_assetid:*{escaped_id}*",
                        "default_operator": "OR",
                        "analyze_wildcard": True,
                        "lenient": True
                    }
                })
            
            if request.assetType:
                escaped_type = self.field_classifier.escape_opensearch_query_string(request.assetType)
                search_queries.append({
                    "query_string": {
                        "query": f"str_assettype:*{escaped_type}*",
                        "default_operator": "OR",
                        "analyze_wildcard": True,
                        "lenient": True
                    }
                })
        
        # File-specific searches (only for file index or when searching both)
        if index_type == "file":
            if request.fileKey:
                escaped_key = self.field_classifier.escape_opensearch_query_string(request.fileKey)
                search_queries.append({
                    "query_string": {
                        "query": f"str_key:*{escaped_key}*",
                        "default_operator": "OR",
                        "analyze_wildcard": True,
                        "lenient": True
                    }
                })
            
            if request.fileExtension:
                # Exact match for file extension
                search_queries.append({
                    "term": {
                        "str_fileext.keyword": request.fileExtension
                    }
                })
        
        # Common searches (apply to both indexes)
        if request.databaseId:
            # Exact match for database ID
            filter_clauses.append({
                "term": {
                    "str_databaseid.keyword": request.databaseId
                }
            })
        
        if request.tags:
            # Search for any of the specified tags
            tag_queries = []
            for tag in request.tags:
                escaped_tag = self.field_classifier.escape_opensearch_query_string(tag)
                tag_queries.append({
                    "query_string": {
                        "query": f"list_tags:*{escaped_tag}*",
                        "default_operator": "OR",
                        "analyze_wildcard": True,
                        "lenient": True
                    }
                })
            
            if tag_queries:
                search_queries.append({
                    "bool": {
                        "should": tag_queries,
                        "minimum_should_match": 1
                    }
                })
        
        # Metadata searches
        if request.metadataKey:
            escaped_key = self.field_classifier.escape_opensearch_query_string(request.metadataKey)
            search_queries.append({
                "wildcard": {
                    "_field_names": {
                        "value": f"MD_*{escaped_key}*",
                        "case_insensitive": True
                    }
                }
            })
        
        if request.metadataValue:
            escaped_value = self.field_classifier.escape_opensearch_query_string(request.metadataValue)
            search_queries.append({
                "query_string": {
                    "query": f"*{escaped_value}*",
                    "fields": ["MD_*"],
                    "default_operator": "OR",
                    "analyze_wildcard": True,
                    "lenient": True
                }
            })
        
        # Combine search queries
        if search_queries:
            if len(search_queries) == 1:
                should_clauses.append(search_queries[0])
            else:
                should_clauses.append({
                    "bool": {
                        "should": search_queries,
                        "minimum_should_match": 1
                    }
                })
        
        # Build final bool query
        bool_query = {}
        if must_clauses:
            bool_query["must"] = must_clauses
        if must_not_clauses:
            bool_query["must_not"] = must_not_clauses
        if should_clauses:
            bool_query["should"] = should_clauses
        if filter_clauses:
            bool_query["filter"] = filter_clauses
        
        # If no search criteria provided, return match_all for browsing
        if not bool_query.get("should") and not bool_query.get("must"):
            if bool_query.get("filter") or bool_query.get("must_not"):
                # We have filters but no search terms - use match_all with filters
                bool_query["must"] = [{"match_all": {}}]
            else:
                return {"match_all": {}}
        
        return {"bool": bool_query}
    
    def _build_simple_highlight_config(self, index_type: str) -> Dict[str, Any]:
        """Build simple highlight configuration"""
        return {
            "pre_tags": ["@opensearch-dashboards-highlighted-field@"],
            "post_tags": ["@/opensearch-dashboards-highlighted-field@"],
            "fields": {
                "str_*": {},
                "MD_*": {},
                "list_*": {}
            },
            "fragment_size": 2147483647
        }
    
    def _get_simple_searchable_fields(self, index_type: str) -> List[str]:
        """Get searchable fields for simple search"""
        if index_type == "asset":
            return [
                "str_assetname", "str_description", "str_assettype",
                "list_tags", "str_asset_version_comment", "MD_*"
            ]
        elif index_type == "file":
            return [
                "str_key", "str_assetname", "str_fileext", "str_etag", 
                "list_tags", "MD_*"
            ]
        return []

#######################
# Query Building
#######################

class DualIndexQueryBuilder:
    """Builds OpenSearch queries for dual-index system"""
    
    def __init__(self, database_access_manager: DatabaseAccessManager):
        self.database_access_manager = database_access_manager
        self.field_classifier = FieldClassifier()
    
    def build_dual_index_queries(self, request: SearchRequestModel, claims_and_roles: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Build queries for both asset and file indexes"""
        
        # Build base query components
        # Note: includeArchived controls archived assets/files, not deleted databases
        # Always pass False for show_deleted to exclude deleted databases
        accessible_databases = self.database_access_manager.get_accessible_databases(
            claims_and_roles, show_deleted=False
        )
        
        # Build asset query
        asset_query = self._build_index_query(request, accessible_databases, "asset")
        
        # Build file query
        file_query = self._build_index_query(request, accessible_databases, "file")
        
        return asset_query, file_query
    
    def _build_index_query(self, request: SearchRequestModel, accessible_databases: List[str], index_type: str) -> Dict[str, Any]:
        """Build query for specific index type"""
        try:
            # Calculate buffer size for authorization filtering
            requested_from = request.from_ or 0
            requested_size = request.size or 100
            buffer_multiplier = 2.0
            opensearch_size = min(int(requested_size * buffer_multiplier), 2000)
            
            # Build base query structure
            query = {
                "from": 0,
                "size": opensearch_size,
                "sort": self._build_sort_config(request.sort, index_type),
                "query": self._build_query_clause(request, accessible_databases, index_type),
                "highlight": self._build_highlight_config(index_type),
                "_source": True,
                "track_total_hits": True,
            }
            
            # Add aggregations if requested
            if request.aggregations:
                query["aggs"] = self._build_aggregations(index_type)
            
            # Add minimum score for text searches
            if request.query or request.metadataQuery:
                query["min_score"] = 0.01
            
            return query
        except Exception as e:
            logger.exception(f"Error building {index_type} query: {e}")
            raise VAMSGeneralErrorResponse(f"Error building {index_type} search query")
    
    def _build_query_clause(self, request: SearchRequestModel, accessible_databases: List[str], index_type: str) -> Dict[str, Any]:
        """Build the main query clause for specific index"""
        must_clauses = []
        must_not_clauses = []
        should_clauses = []
        filter_clauses = []
        
        # Add filters from request (e.g., boolean relationship filters)
        # Skip _rectype filter as it's handled by entityTypes
        if request.filters:
            for filter_item in request.filters:
                # Convert Pydantic model to dict if needed
                filter_dict = None
                if hasattr(filter_item, 'dict'):
                    filter_dict = filter_item.dict()
                elif isinstance(filter_item, dict):
                    filter_dict = filter_item
                else:
                    filter_dict = dict(filter_item)
                
                # Skip _rectype filter - it's redundant with entityTypes
                if filter_dict and 'query_string' in filter_dict:
                    query_str = filter_dict['query_string'].get('query', '')
                    if '_rectype' not in query_str:
                        # Only add non-rectype filters
                        filter_clauses.append(filter_dict)
        
        # Add general text search
        if request.query:
            general_search_query = self._build_general_search_query(request.query, request.includeMetadataInSearch, index_type)
            if general_search_query:
                should_clauses.append(general_search_query)
        
        # Add dedicated metadata search
        if request.metadataQuery:
            metadata_search_query = self._build_metadata_search_query(request.metadataQuery, request.metadataSearchMode)
            if metadata_search_query:
                if request.query:
                    must_clauses.append(metadata_search_query)
                else:
                    should_clauses.append(metadata_search_query)
        
        # Add database access restrictions
        if accessible_databases:
            db_query_string = " OR ".join([f'"{db_id}"' for db_id in accessible_databases])
            filter_clauses.append({
                "query_string": {
                    "query": f"str_databaseid:({db_query_string})"
                }
            })
        else:
            # No accessible databases - return no results
            filter_clauses.append({
                "query_string": {
                    "query": 'str_databaseid:"NOACCESSDATABASE"'
                }
            })
        
        # Add archive exclusions (unless explicitly included)
        if not request.includeArchived:
            must_not_clauses.append({"term": {"bool_archived": True}})
        
        # Build final bool query
        bool_query = {}
        if must_clauses:
            bool_query["must"] = must_clauses
        if must_not_clauses:
            bool_query["must_not"] = must_not_clauses
        if should_clauses:
            bool_query["should"] = should_clauses
        if filter_clauses:
            bool_query["filter"] = filter_clauses
        
        return {"bool": bool_query} if bool_query else {"match_all": {}}
    
    def _build_general_search_query(self, query: str, include_metadata: bool, index_type: str) -> Dict[str, Any]:
        """Build general text search query for specific index"""
        if not query:
            return {}
        
        # Get searchable fields based on index type and metadata inclusion
        searchable_fields = self._get_searchable_fields(include_metadata, index_type)
        
        # Use query_string for better special character handling
        escaped_query = self.field_classifier.escape_opensearch_query_string(query)
        
        return {
            "query_string": {
                "query": f"*{escaped_query}*",
                "fields": searchable_fields,
                "default_operator": "OR",
                "analyze_wildcard": True,
                "lenient": True
            }
        }
    
    def _build_metadata_search_query(self, metadata_query: str, search_mode: str) -> Dict[str, Any]:
        """Build dedicated metadata search query"""
        if not metadata_query:
            return {}
        
        # Check if query contains AND or OR operator for multiple field:value pairs
        operator = None
        pairs = []
        
        if ' AND ' in metadata_query:
            operator = 'AND'
            pairs = [pair.strip() for pair in metadata_query.split(' AND ') if pair.strip()]
        elif ' OR ' in metadata_query:
            operator = 'OR'
            pairs = [pair.strip() for pair in metadata_query.split(' OR ') if pair.strip()]
        
        if operator and pairs:
            # Build individual queries for each pair
            pair_queries = []
            for pair in pairs:
                if ':' in pair:
                    parts = pair.split(':', 1)
                    field_part = parts[0].strip()
                    value_part = parts[1].strip() if len(parts) > 1 else ''
                    
                    # Ensure field has MD_ prefix
                    if not field_part.startswith('MD_'):
                        field_part = f'MD_{field_part}'
                    
                    # Check if user provided wildcards
                    has_wildcards = '*' in value_part or '?' in value_part
                    
                    # Escape the value part (preserve wildcards if user provided them)
                    escaped_value = self.field_classifier.escape_opensearch_query_string(value_part, preserve_wildcards=has_wildcards)
                    
                    # Build query - use exact match unless user provided wildcards
                    if has_wildcards:
                        query_str = f"{field_part}:{escaped_value}"
                    else:
                        # Exact match - quote the value for phrase matching
                        query_str = f'{field_part}:"{escaped_value}"'
                    
                    # Build query for this specific field:value pair
                    pair_queries.append({
                        "query_string": {
                            "query": query_str,
                            "analyze_wildcard": True,
                            "lenient": True
                        }
                    })
            
            # Return bool query with appropriate logic
            if len(pair_queries) == 1:
                return pair_queries[0]
            elif len(pair_queries) > 1:
                if operator == 'AND':
                    # All must match
                    return {
                        "bool": {
                            "must": pair_queries
                        }
                    }
                else:  # OR
                    # Any can match
                    return {
                        "bool": {
                            "should": pair_queries,
                            "minimum_should_match": 1
                        }
                    }
            else:
                return {}
        
        # Single field:value pair
        if ':' in metadata_query:
            parts = metadata_query.split(':', 1)
            field_part = parts[0].strip()
            value_part = parts[1].strip() if len(parts) > 1 else ''
            
            # Ensure field has MD_ prefix
            if not field_part.startswith('MD_'):
                field_part = f'MD_{field_part}'
            
            # Check if user provided wildcards
            has_wildcards = '*' in value_part or '?' in value_part
            
            # Escape the value part for query_string (preserve wildcards if user provided them)
            escaped_value = self.field_classifier.escape_opensearch_query_string(value_part, preserve_wildcards=has_wildcards)
            
            # Build query - use exact match unless user provided wildcards
            if has_wildcards:
                query_str = f"{field_part}:{escaped_value}"
            else:
                # Exact match - quote the value for phrase matching
                query_str = f'{field_part}:"{escaped_value}"'
            
            # Build query for specific field:value pair
            return {
                "query_string": {
                    "query": query_str,
                    "analyze_wildcard": True,
                    "lenient": True
                }
            }
        
        # If no colon, treat as a general metadata search based on mode
        # Check if user provided wildcards
        has_wildcards = '*' in metadata_query or '?' in metadata_query
        escaped_query = self.field_classifier.escape_opensearch_query_string(metadata_query, preserve_wildcards=has_wildcards)
        
        if search_mode == "key":
            # Search for documents that have metadata fields matching the pattern
            # The query comes as "MD_str_product" from frontend
            if has_wildcards:
                # With wildcards, search for any value in fields matching the pattern
                # Use query_string with wildcard on field name
                return {
                    "query_string": {
                        "query": f"{escaped_query}:*",
                        "analyze_wildcard": True,
                        "lenient": True
                    }
                }
            else:
                # Exact field name - use exists query
                return {
                    "exists": {
                        "field": escaped_query
                    }
                }
        
        elif search_mode == "value":
            # Search only metadata field values across all MD_ fields
            # Don't search field names, only values
            if has_wildcards:
                query_str = escaped_query
            else:
                # For value-only search, use wildcards to find partial matches
                query_str = f"*{escaped_query}*"
            
            return {
                "query_string": {
                    "query": query_str,
                    "fields": ["MD_*"],
                    "default_operator": "OR",
                    "analyze_wildcard": True,
                    "lenient": True
                }
            }
        
        else:  # search_mode == "both"
            # Search both field names and values
            # For "both" mode, use exact match unless user provides wildcards
            if has_wildcards:
                query_str = escaped_query
            else:
                # Exact match for "both" mode
                query_str = f'"{escaped_query}"'
            
            return {
                "query_string": {
                    "query": query_str,
                    "fields": ["MD_*"],
                    "default_operator": "OR",
                    "analyze_wildcard": True,
                    "lenient": True
                }
            }
    
    def _build_sort_config(self, sort_config: List[Any], index_type: str) -> List[Any]:
        """Build sort configuration for specific index"""
        logger.info(f"[Sort] Building sort config for {index_type} index with input: {sort_config}")
        
        if not sort_config:
            logger.info(f"[Sort] No sort config provided, using default _score")
            return ["_score"]
        
        sanitized_sort = []
        
        for sort_item in sort_config:
            if isinstance(sort_item, str):
                # Handle special fields based on index type
                if sort_item == "list_tags" and index_type == "asset":
                    sanitized_sort.append({"list_tags": {"order": "asc"}})
                elif sort_item == "str_fileext" and index_type == "file":
                    sanitized_sort.append({"str_fileext": {"order": "asc"}})
                else:
                    sanitized_sort.append(sort_item)
            elif isinstance(sort_item, dict):
                # Check if this is the frontend format: {"field": "fieldname", "order": "asc|desc"}
                if "field" in sort_item and "order" in sort_item:
                    # Transform to OpenSearch format: {"fieldname": {"order": "asc|desc"}}
                    field_name = sort_item["field"]
                    order = sort_item["order"]
                    logger.info(f"[Sort] Transforming frontend format: field={field_name}, order={order}")
                    sanitized_sort.append({field_name: {"order": order}})
                else:
                    # Already in OpenSearch format or other dict format
                    sanitized_sort.append(sort_item)
        
        # Ensure we always have at least one sort field
        if not sanitized_sort:
            logger.info(f"[Sort] No valid sort items, using default _score")
            sanitized_sort = ["_score"]
        
        logger.info(f"[Sort] Final sort config for {index_type}: {sanitized_sort}")
        return sanitized_sort
    
    def _build_highlight_config(self, index_type: str) -> Dict[str, Any]:
        """Build highlight configuration for specific index"""
        return {
            "pre_tags": ["@opensearch-dashboards-highlighted-field@"],
            "post_tags": ["@/opensearch-dashboards-highlighted-field@"],
            "fields": {
                "str_*": {},
                "MD_*": {},
                "list_*": {}
            },
            "fragment_size": 2147483647
        }
    
    def _build_aggregations(self, index_type: str) -> Dict[str, Any]:
        """Build aggregations for specific index"""
        base_filter = {
            "bool": {
                "must_not": [
                    {"term": {"bool_archived": True}}
                ]
            }
        }
        
        if index_type == "asset":
            return {
                "str_assettype": {
                    "filter": base_filter,
                    "aggs": {
                        "filtered_assettype": {
                            "terms": {
                                "field": "str_assettype.keyword",
                                "size": 1000
                            }
                        }
                    }
                },
                "str_databaseid": {
                    "filter": base_filter,
                    "aggs": {
                        "filtered_databaseid": {
                            "terms": {
                                "field": "str_databaseid.keyword",
                                "size": 1000
                            }
                        }
                    }
                },
                "list_tags": {
                    "filter": base_filter,
                    "aggs": {
                        "filtered_tags": {
                            "terms": {
                                "field": "list_tags.keyword",
                                "size": 1000
                            }
                        }
                    }
                }
            }
        elif index_type == "file":
            return {
                "str_fileext": {
                    "filter": base_filter,
                    "aggs": {
                        "filtered_fileext": {
                            "terms": {
                                "field": "str_fileext.keyword",
                                "size": 1000
                            }
                        }
                    }
                },
                "str_databaseid": {
                    "filter": base_filter,
                    "aggs": {
                        "filtered_databaseid": {
                            "terms": {
                                "field": "str_databaseid.keyword",
                                "size": 1000
                            }
                        }
                    }
                },
                "str_assetid": {
                    "filter": base_filter,
                    "aggs": {
                        "filtered_assetid": {
                            "terms": {
                                "field": "str_assetid.keyword",
                                "size": 1000
                            }
                        }
                    }
                },
                "list_tags": {
                    "filter": base_filter,
                    "aggs": {
                        "filtered_tags": {
                            "terms": {
                                "field": "list_tags.keyword",
                                "size": 1000
                            }
                        }
                    }
                }
            }
        
        return {}
    
    def _get_searchable_fields(self, include_metadata: bool, index_type: str) -> List[str]:
        """Get list of fields that should be included in general text search"""
        # Get core searchable fields for the index type
        core_fields = self.field_classifier.get_searchable_core_fields(index_type)
        
        if include_metadata:
            # Add metadata field patterns
            return core_fields + ["MD_*"]
        else:
            return core_fields

#######################
# Response Processing
#######################

class DualIndexResponseProcessor:
    """Processes OpenSearch responses from dual indexes"""
    
    def __init__(self, database_access_manager: DatabaseAccessManager):
        self.database_access_manager = database_access_manager
        self.field_classifier = FieldClassifier()
    
    def process_dual_search_response(self, opensearch_response: Dict[str, Any], 
                                   request: SearchRequestModel, claims_and_roles: Dict[str, Any]) -> SearchResponseModel:
        """Process dual-index search response with authorization filtering"""
        try:
            # Log the raw response for debugging
            logger.info(f"Processing response with {len(opensearch_response.get('hits', {}).get('hits', []))} hits")
            
            # Apply Casbin filtering to hits
            filtered_hits = []
            for hit in opensearch_response.get("hits", {}).get("hits", []):
                # Log hit structure for debugging
                logger.debug(f"Processing hit with keys: {hit.keys()}")
                
                if self._is_hit_authorized(hit, claims_and_roles):
                    # Add explanation if requested
                    if request.explainResults:
                        hit = self._add_search_explanation(hit, request)
                    filtered_hits.append(hit)
            
            logger.info(f"After authorization filtering: {len(filtered_hits)} hits remain")
            
            # Re-sort filtered hits to maintain sort order after authorization filtering
            # Authorization filtering may have disrupted the original sort order
            # sort_config = opensearch_response.get("_sort_config", ["_score"])
            # if sort_config and filtered_hits:
            #     filtered_hits = self._sort_filtered_hits(filtered_hits, sort_config, request.sort)
            
            # Apply pagination to filtered results
            paginated_hits = self._apply_pagination(filtered_hits, request)
            
            logger.info(f"After pagination: {len(paginated_hits)} hits")
            
            # Update response structure
            response_data = opensearch_response.copy()
            response_data["hits"]["hits"] = paginated_hits
            response_data["hits"]["total"]["value"] = len(filtered_hits)
            
            # Fix aggregation structure
            if "aggregations" in response_data:
                response_data["aggregations"] = self._fix_aggregation_structure(response_data["aggregations"])
            
            # Parse into response model
            return parse(response_data, model=SearchResponseModel)
            
        except Exception as e:
            logger.exception(f"Error processing dual search response: {e}")
            raise VAMSGeneralErrorResponse("Error processing search results")
    
    def _add_search_explanation(self, hit: Dict[str, Any], request: SearchRequestModel) -> Dict[str, Any]:
        """Add explanation for why this result matched the search"""
        try:
            source = hit.get("_source", {})
            highlight = hit.get("highlight", {})
            index_type = hit.get("_index_type", "unknown")
            
            # Determine query type
            query_type = "none"
            if request.query and request.metadataQuery:
                query_type = "combined"
            elif request.query:
                query_type = "general"
            elif request.metadataQuery:
                query_type = "metadata"
            
            # Extract matched fields from highlights
            matched_fields = list(highlight.keys()) if highlight else []
            
            # Build match reasons
            match_reasons = {}
            
            # Process highlights to build match reasons
            for field, highlights in highlight.items():
                if highlights:
                    if self.field_classifier.is_core_field(field, index_type):
                        match_reasons[field] = f"Matched core {index_type} field '{field}'"
                    elif self.field_classifier.is_metadata_field(field):
                        match_reasons[field] = f"Matched metadata field '{field}'"
                    else:
                        match_reasons[field] = f"Matched field '{field}'"
            
            # Create explanation object
            explanation = {
                "matched_fields": matched_fields,
                "match_reasons": match_reasons,
                "query_type": query_type,
                "index_type": index_type,
                "score_breakdown": {
                    "total_score": hit.get("_score", 0.0),
                    "field_matches": len(matched_fields),
                    "highlight_matches": len(highlight) if highlight else 0
                }
            }
            
            # Add explanation to hit
            hit["explanation"] = explanation
            
            return hit
            
        except Exception as e:
            logger.warning(f"Error adding search explanation: {e}")
            return hit
    
    def _sort_filtered_hits(self, hits: List[Dict[str, Any]], sort_config: List, request_sort: List) -> List[Dict[str, Any]]:
        """Sort filtered hits after authorization filtering
        
        Args:
            hits: List of filtered hits
            sort_config: Sort configuration from OpenSearch response
            request_sort: Original sort configuration from request
            
        Returns:
            Sorted list of hits
        """
        if not hits:
            return hits
        
        # Use request sort if available, otherwise use response sort config
        active_sort = request_sort if request_sort else sort_config
        if not active_sort:
            logger.warning("[Sort] No sort configuration available, returning unsorted hits")
            return hits
        
        logger.info(f"[Sort] Re-sorting {len(hits)} filtered hits")
        logger.info(f"[Sort] Request sort: {request_sort}")
        logger.info(f"[Sort] Response sort config: {sort_config}")
        logger.info(f"[Sort] Active sort: {active_sort}")
        
        # Log first few hits before sorting for debugging
        if hits:
            logger.info(f"[Sort] First hit before sort - fileext: {hits[0].get('_source', {}).get('str_fileext', 'N/A')}")
        
        # Build sort key function
        def get_sort_key(hit):
            keys = []
            for sort_item in active_sort:
                if isinstance(sort_item, str):
                    if sort_item == "_score":
                        keys.append(hit.get("_score", 0))
                    else:
                        # Remove .keyword suffix if present (OpenSearch mapping convention)
                        field_name = sort_item.replace('.keyword', '')
                        keys.append(hit.get("_source", {}).get(field_name, ""))
                elif isinstance(sort_item, dict):
                    for field, config in sort_item.items():
                        # Remove .keyword suffix if present
                        field_name = field.replace('.keyword', '')
                        value = hit.get("_source", {}).get(field_name, "")
                        if value is None:
                            value = ""
                        keys.append(value)
                elif hasattr(sort_item, 'field'):
                    # Handle Pydantic SearchSortModel objects
                    # Remove .keyword suffix if present
                    field_name = sort_item.field.replace('.keyword', '')
                    value = hit.get("_source", {}).get(field_name, "")
                    if value is None:
                        value = ""
                    keys.append(value)
            return tuple(keys) if len(keys) > 1 else (keys[0] if keys else "")
        
        # Determine sort order - handle both dict and Pydantic model
        reverse = False
        if active_sort:
            first_sort = active_sort[0]
            if isinstance(first_sort, dict):
                for field, config in first_sort.items():
                    if isinstance(config, dict) and config.get("order") == "desc":
                        reverse = True
                        break
            elif hasattr(first_sort, 'order'):
                # Handle Pydantic SearchSortModel
                reverse = first_sort.order == "desc"
        
        logger.info(f"[Sort] Determined reverse={reverse} from active_sort")
        
        try:
            sorted_hits = sorted(hits, key=get_sort_key, reverse=reverse)
            logger.info(f"[Sort] Successfully re-sorted {len(sorted_hits)} filtered hits (reverse={reverse})")
            
            # Log first few hits after sorting for debugging
            if sorted_hits:
                for i, hit in enumerate(sorted_hits[:5]):
                    fileext = hit.get('_source', {}).get('str_fileext', 'N/A')
                    logger.info(f"[Sort] Hit {i} after sort - fileext: {fileext}")
            
            return sorted_hits
        except Exception as e:
            logger.warning(f"[Sort] Error re-sorting filtered hits: {e}, returning unsorted")
            return hits
    
    def _is_hit_authorized(self, hit: Dict[str, Any], claims_and_roles: Dict[str, Any]) -> bool:
        """Check if user is authorized to see this search hit"""
        try:
            source = hit.get("_source", {})
            
            # Skip deleted items (additional safety check)
            if source.get("str_databaseid", "").endswith("#deleted"):
                return False
            
            # Build document for Casbin check
            hit_document = {
                "databaseId": source.get("str_databaseid", ""),
                "assetName": source.get("str_assetname", ""),
                "tags": source.get("list_tags", []),
                "assetType": source.get("str_assettype", ""),
                "object__type": "asset"  # For ABAC purposes, treat all as assets
            }
            
            # Apply Casbin enforcement
            if len(claims_and_roles.get("tokens", [])) > 0:
                casbin_enforcer = CasbinEnforcer(claims_and_roles)
                return casbin_enforcer.enforce(hit_document, "GET")
            
            return False
        except Exception as e:
            logger.warning(f"Error checking hit authorization: {e}")
            return False
    
    def _apply_pagination(self, hits: List[Dict[str, Any]], request: SearchRequestModel) -> List[Dict[str, Any]]:
        """Apply pagination to filtered hits"""
        from_index = request.from_ or 0
        size = request.size or 100
        
        if from_index >= len(hits):
            return []
        
        end_index = min(from_index + size, len(hits))
        return hits[from_index:end_index]
    
    def _fix_aggregation_structure(self, aggregations: Dict[str, Any]) -> Dict[str, Any]:
        """Fix nested aggregation structure to match expected format"""
        fixed_aggregations = {}
        
        # Extract nested aggregations
        for agg_name, agg_data in aggregations.items():
            if isinstance(agg_data, dict) and f"filtered_{agg_name.replace('str_', '').replace('list_', '')}" in agg_data:
                nested_key = f"filtered_{agg_name.replace('str_', '').replace('list_', '')}"
                fixed_aggregations[agg_name] = agg_data[nested_key]
            else:
                fixed_aggregations[agg_name] = agg_data
        
        return fixed_aggregations

#######################
# Request Handlers
#######################

def handle_get_request(event: Dict[str, Any], search_manager: DualIndexSearchManager) -> APIGatewayProxyResponseV2:
    """Handle GET request for index mappings"""
    try:
        if not search_manager.is_available():
            return general_error(
                body={"message": "Search is not available when OpenSearch feature is not enabled"},
                status_code=404
            )
        
        # Get index mappings
        mappings = search_manager.get_index_mappings()
        
        return success(body={"mappings": mappings})
        
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)})
    except Exception as e:
        logger.exception(f"Error handling GET request: {e}")
        return internal_error()

def handle_post_request(event: Dict[str, Any], search_manager: DualIndexSearchManager, 
                       query_builder: DualIndexQueryBuilder, response_processor: DualIndexResponseProcessor,
                       claims_and_roles: Dict[str, Any]) -> APIGatewayProxyResponseV2:
    """Handle POST request for search operations"""
    try:
        if not search_manager.is_available():
            return general_error(
                body={"message": "Search is not available when OpenSearch feature is not enabled"},
                status_code=404
            )
        
        # Parse request body
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"})
        
        # Parse JSON body safely
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"})
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string or dict")
            return validation_error(body={'message': "Request body cannot be parsed"})
        
        # Parse and validate request model
        request_model = parse(body, model=SearchRequestModel)
        
        # Build dual-index queries
        asset_query, file_query = query_builder.build_dual_index_queries(request_model, claims_and_roles)
        
        # Execute dual-index search
        opensearch_response = search_manager.search_dual_index(
            asset_query, file_query, request_model.entityTypes or ["asset", "file"]
        )
        
        # Process response with authorization filtering
        processed_response = response_processor.process_dual_search_response(
            opensearch_response, request_model, claims_and_roles
        )
        
        return success(body=processed_response.dict())
        
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Error handling POST request: {e}")
        return internal_error()

def handle_simple_post_request(event: Dict[str, Any], search_manager: DualIndexSearchManager, 
                              simple_query_builder: SimpleSearchQueryBuilder, response_processor: DualIndexResponseProcessor,
                              claims_and_roles: Dict[str, Any]) -> APIGatewayProxyResponseV2:
    """Handle POST request for simple search operations"""
    try:
        if not search_manager.is_available():
            return general_error(
                body={"message": "Search is not available when OpenSearch feature is not enabled"},
                status_code=404
            )
        
        # Parse request body
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"})
        
        # Parse JSON body safely
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"})
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string or dict")
            return validation_error(body={'message': "Request body cannot be parsed"})
        
        # Parse and validate simple search request model
        request_model = parse(body, model=SimpleSearchRequestModel)
        
        # Build simple dual-index queries
        asset_query, file_query = simple_query_builder.build_simple_dual_index_queries(request_model, claims_and_roles)
        
        # Execute dual-index search
        opensearch_response = search_manager.search_dual_index(
            asset_query, file_query, request_model.entityTypes or ["asset", "file"]
        )
        
        # Create a compatible SearchRequestModel for response processing
        # This allows us to reuse the existing response processor
        compatible_request = SearchRequestModel(
            from_=request_model.from_,
            size=request_model.size,
            entityTypes=request_model.entityTypes,
            includeArchived=request_model.includeArchived,
            explainResults=False,  # Simple search doesn't include explanations
            aggregations=False     # Simple search doesn't include aggregations
        )
        
        # Process response with authorization filtering
        processed_response = response_processor.process_dual_search_response(
            opensearch_response, compatible_request, claims_and_roles
        )
        
        return success(body=processed_response.dict())
        
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Error handling simple POST request: {e}")
        return internal_error()

#######################
# Lambda Handler
#######################

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for dual-index search API"""
    global claims_and_roles
    claims_and_roles = request_to_claims(event)
    
    try:
        # Parse request
        path = event['requestContext']['http']['path']
        method = event['requestContext']['http']['method']
        
        logger.info(f"Processing {method} request to {path}")
        
        # Check API authorization
        method_allowed_on_api = False
        if len(claims_and_roles.get("tokens", [])) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True
        
        if not method_allowed_on_api:
            return authorization_error()
        
        # Initialize components
        search_manager = DualIndexSearchManager()
        database_access_manager = DatabaseAccessManager()
        response_processor = DualIndexResponseProcessor(database_access_manager)
        
        # Route based on path and method
        if method == 'GET':
            # GET requests are only supported on the main /search endpoint for mappings
            if path == '/search':
                return handle_get_request(event, search_manager)
            else:
                return validation_error(body={'message': "GET method only supported on /search endpoint"})
        
        elif method == 'POST':
            if path == '/search':
                # Regular complex search
                query_builder = DualIndexQueryBuilder(database_access_manager)
                return handle_post_request(event, search_manager, query_builder, response_processor, claims_and_roles)
            
            elif path == '/search/simple':
                # Simple search
                simple_query_builder = SimpleSearchQueryBuilder(database_access_manager)
                return handle_simple_post_request(event, search_manager, simple_query_builder, response_processor, claims_and_roles)
            
            else:
                return validation_error(body={'message': f"POST method not supported on path: {path}"})
        
        else:
            return validation_error(body={'message': f"Method {method} not allowed"})
    
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error()

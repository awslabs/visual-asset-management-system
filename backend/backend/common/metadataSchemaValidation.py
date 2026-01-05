# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Metadata schema validation utilities for VAMS.

This module provides functions for:
- Fetching and aggregating metadata schemas
- Validating metadata against schemas
- Enriching metadata with schema information
"""

import json
import time
from typing import Dict, List, Optional, Tuple, Any
from boto3.dynamodb.types import TypeDeserializer
from customLogging.logger import safeLogger
from models.metadata import MetadataValueType

logger = safeLogger(service_name="MetadataSchemaValidation")

# In-memory cache for schemas with 60-second TTL
_schema_cache: Dict[str, Tuple[Any, float]] = {}
_cache_ttl = 60  # seconds


def _get_from_cache(cache_key: str) -> Optional[Dict]:
    """Get schema from cache if not expired
    
    Args:
        cache_key: Cache key
        
    Returns:
        Cached data or None if expired/not found
    """
    if cache_key in _schema_cache:
        cached_data, timestamp = _schema_cache[cache_key]
        if time.time() - timestamp < _cache_ttl:
            logger.info(f"Cache hit for key: {cache_key}")
            return cached_data
        else:
            # Remove expired entry
            del _schema_cache[cache_key]
            logger.info(f"Cache expired for key: {cache_key}")
    return None


def _set_in_cache(cache_key: str, data: Dict):
    """Store schema in cache with current timestamp
    
    Args:
        cache_key: Cache key
        data: Data to cache
    """
    _schema_cache[cache_key] = (data, time.time())
    logger.info(f"Cached data for key: {cache_key}")


def extract_file_extension(file_path: str) -> Optional[str]:
    """Extract file extension from file path
    
    Args:
        file_path: File path (e.g., "folder/file.pdf")
        
    Returns:
        File extension with dot (e.g., ".pdf") or None if no extension
    """
    if not file_path or '.' not in file_path:
        return None
    
    # Get the last part after the last dot
    extension = '.' + file_path.rsplit('.', 1)[-1].lower()
    return extension


def is_empty_value(value: Any) -> bool:
    """Check if a value is considered empty for validation
    
    Args:
        value: Value to check
        
    Returns:
        True if value is None, empty string, or represents DynamoDB NULL
    """
    if value is None:
        return True
    if isinstance(value, str) and value == "":
        return True
    # Note: Strings "NULL" or "UNDEFINED" are actual values, not empty
    return False


def get_aggregated_schemas(
    database_ids: List[str],
    entity_type: str,
    file_path: Optional[str],
    dynamodb_client,
    schema_table_name: str
) -> Dict[str, Dict]:
    """Fetch and aggregate metadata schemas for given parameters
    
    Args:
        database_ids: List of database IDs (can include "GLOBAL")
        entity_type: Entity type (assetMetadata, fileMetadata, fileAttribute, databaseMetadata, assetLinkMetadata)
        file_path: Optional file path for extension filtering (fileMetadata/fileAttribute only)
        dynamodb_client: DynamoDB client
        schema_table_name: Schema table name
        
    Returns:
        Dictionary of aggregated schema fields: {fieldName: {field_definition}}
    """
    # Extract file extension if applicable
    file_extension = None
    if file_path and entity_type in ['fileMetadata', 'fileAttribute']:
        file_extension = extract_file_extension(file_path)
    
    # Build cache key
    extension_key = file_extension if file_extension else "no_ext"
    cache_key = f"{':'.join(sorted(database_ids))}:{entity_type}:{extension_key}"
    
    # Check cache
    cached_result = _get_from_cache(cache_key)
    if cached_result is not None:
        return cached_result
    
    # Fetch schemas from DynamoDB
    all_schemas = []
    deserializer = TypeDeserializer()
    query_successful = True  # Track if all queries succeed
    
    logger.info(f"Fetching schemas for databases: {database_ids}, entity_type: {entity_type}, file_extension: {file_extension}")
    
    for database_id in database_ids:
        try:
            # Query schemas for this database + entity type
            composite_key = f"{database_id}:{entity_type}"
            
            #logger.info(f"Querying schema table: {schema_table_name}")
            #logger.info(f"Using GSI: DatabaseIdMetadataEntityTypeIndex")
            #logger.info(f"Composite key: {composite_key}")
            
            response = dynamodb_client.query(
                TableName=schema_table_name,
                IndexName='DatabaseIdMetadataEntityTypeIndex',
                KeyConditionExpression='#pk = :pkValue',
                ExpressionAttributeNames={'#pk': 'databaseId:metadataEntityType'},
                ExpressionAttributeValues={':pkValue': {'S': composite_key}}
            )
            
            items_found = len(response.get('Items', []))
            logger.info(f"Query result for {composite_key}: {items_found} schemas found")
            
            # Log schema IDs found for debugging
            if items_found > 0:
                schema_ids = []
                for item in response.get('Items', []):
                    schema_id = deserializer.deserialize(item.get('metadataSchemaId', {}))
                    schema_ids.append(schema_id)
                #logger.info(f"Schema IDs found: {schema_ids}")
            
            for item in response.get('Items', []):
                deserialized_item = {k: deserializer.deserialize(v) for k, v in item.items()}
                
                # Log schema structure for debugging
                schema_id = deserialized_item.get('metadataSchemaId', 'unknown')
                
                # CRITICAL FIX: The field is stored as 'fields' not 'metadataSchemaFields'
                # Try both field names for backward compatibility
                fields = deserialized_item.get('fields') or deserialized_item.get('metadataSchemaFields', {})
                
                # Parse and validate fields using Pydantic MetadataSchemaFieldModel
                if isinstance(fields, str):
                    try:
                        parsed_fields = json.loads(fields)
                    except json.JSONDecodeError as e:
                        logger.error(f"Schema {schema_id}: Failed to parse fields JSON, skipping schema: {e}")
                        continue  # Skip this schema
                    fields = parsed_fields
                
                # Handle nested structure: {"fields": [...]} or just [...]
                if isinstance(fields, dict) and 'fields' in fields:
                    # Extract the fields array from nested structure
                    fields = fields.get('fields', [])
                    #logger.info(f"Schema {schema_id}: Extracted fields array from nested structure")
                
                # At this point, fields should be a list
                if isinstance(fields, list):
                    # Validate each field object using Pydantic
                    try:
                        from models.metadataSchema import MetadataSchemaFieldModel
                        validated_fields = []
                        for field_obj in fields:
                            try:
                                # Validate field using Pydantic model
                                validated_field = MetadataSchemaFieldModel(**field_obj)
                                validated_fields.append(validated_field.dict())
                            except Exception as field_error:
                                logger.warning(f"Schema {schema_id}: Invalid field object, skipping field: {field_error}")
                                continue
                        
                        if len(validated_fields) == 0:
                            logger.warning(f"Schema {schema_id}: No valid fields after validation, skipping schema")
                            continue  # Skip this schema if no valid fields
                        
                        # Convert validated list to dictionary keyed by metadataFieldKeyName
                        fields_dict = {}
                        for field_obj in validated_fields:
                            field_name = field_obj.get('metadataFieldKeyName')
                            if field_name:
                                fields_dict[field_name] = field_obj
                                # DEBUG: Log sequence for each field
                                seq = field_obj.get('sequence')
                                #logger.info(f"Schema {schema_id}: Field '{field_name}' has sequence: {seq}")
                        
                        fields = fields_dict
                        deserialized_item['fields'] = fields
                        deserialized_item['metadataSchemaFields'] = fields
                        logger.info(f"Schema {schema_id}: Validated and converted {len(fields_dict)} fields")
                        
                    except Exception as e:
                        logger.error(f"Schema {schema_id}: Failed to validate fields, skipping schema: {e}")
                        continue  # Skip this schema
                else:
                    logger.warning(f"Schema {schema_id}: Expected fields to be a list, got {type(fields)}, skipping schema")
                    continue  # Skip this schema
                
                logger.info(f"Schema {schema_id}: has {len(fields)} fields defined")
                
                # Log field structure if empty to help diagnose
                if len(fields) == 0:
                    logger.warning(f"Schema {schema_id} has no fields! Schema keys: {list(deserialized_item.keys())}")
                    # Log the raw fields value to see what's there
                    raw_fields = deserialized_item.get('fields')
                    #logger.warning(f"Raw 'fields' value: {raw_fields} (type: {type(raw_fields)})")
                else:
                    # Log first few field names for verification
                    field_names = list(fields.keys())[:5]
                    #logger.info(f"Schema {schema_id} field names (first 5): {field_names}")
                
                # Filter by file extension if applicable
                if file_extension and entity_type in ['fileMetadata', 'fileAttribute']:
                    # Try both possible field names for file extension restrictions
                    file_ext_restrictions = deserialized_item.get('fileKeyTypeRestriction') or deserialized_item.get('fileExtensionRestrictions', '')
                    
                    # Only apply filtering if restrictions are non-empty
                    if file_ext_restrictions and file_ext_restrictions.strip():
                        # Parse comma-delimited extensions
                        allowed_extensions = [ext.strip().lower() for ext in file_ext_restrictions.split(',')]
                        
                        # Check if ".all" is in the list (means no restriction)
                        if '.all' not in allowed_extensions:
                            # Apply restriction - file extension must match
                            if file_extension not in allowed_extensions:
                                logger.info(f"Skipping schema {deserialized_item.get('metadataSchemaId')} - file extension {file_extension} not in allowed list: {allowed_extensions}")
                                continue  # Skip this schema
                        else:
                            logger.info(f"Schema {deserialized_item.get('metadataSchemaId')} has '.all' in restrictions - applies to all file types")
                    else:
                        logger.info(f"Schema {deserialized_item.get('metadataSchemaId')} has no file extension restrictions - applies to all file types")
                
                all_schemas.append(deserialized_item)
                
        except Exception as e:
            logger.exception(f"Error fetching schemas for database {database_id}, entity_type {entity_type}: {e}")
            query_successful = False
            # Continue with other databases
    
    # Aggregate schema fields
    aggregated_fields = aggregate_schema_fields(all_schemas)
    
    logger.info(f"Aggregated {len(aggregated_fields)} schema fields from {len(all_schemas)} schemas")
    
    # Only cache if all queries were successful
    if query_successful:
        _set_in_cache(cache_key, aggregated_fields)
    else:
        logger.warning(f"Not caching results for {cache_key} due to query failures")
    
    return aggregated_fields


def aggregate_schema_fields(schemas_list: List[Dict]) -> Dict[str, Dict]:
    """Aggregate schema fields from multiple schemas with conflict resolution
    
    Args:
        schemas_list: List of schema dictionaries
        
    Returns:
        Dictionary of aggregated fields: {fieldName: {field_definition}}
    """
    aggregated = {}
    field_sources = {}  # Track which schemas define each field
    
    for schema in schemas_list:
        schema_id = schema.get('metadataSchemaId', 'unknown')
        schema_name = schema.get('schemaName', 'Unknown')  # NEW: Track schema name
        database_id = schema.get('databaseId', 'unknown')  # NEW: Track database ID
        
        fields = schema.get('metadataSchemaFields') or schema.get('fields', {})
        
        for field_name, field_def in fields.items():
            if field_name not in aggregated:
                # First occurrence of this field
                # Fields are already validated by Pydantic, use property names directly
                aggregated[field_name] = {
                    'metadataFieldValueType': field_def.get('metadataFieldValueType'),
                    'required': field_def.get('required', False),
                    'sequence': field_def.get('sequence'),
                    'dependsOnFieldKeyName': field_def.get('dependsOnFieldKeyName'),
                    'defaultMetadataFieldValue': field_def.get('defaultMetadataFieldValue'),
                    'controlledListKeys': field_def.get('controlledListKeys', []),
                    'metadataSchemaMultiFieldConflict': False,
                    'schemaNames': [(schema_name, database_id)]
                }
                field_sources[field_name] = [schema_id]
            else:
                # Field already exists - check for conflicts
                field_sources[field_name].append(schema_id)
                existing = aggregated[field_name]
                
                # NEW: Add this schema to the list of sources
                aggregated[field_name]['schemaNames'].append((schema_name, database_id))
                
                # Check if definitions conflict
                has_conflict = (
                    existing['metadataFieldValueType'] != field_def.get('metadataFieldValueType') or
                    existing['required'] != field_def.get('required', False) or
                    existing['dependsOnFieldKeyName'] != field_def.get('dependsOnFieldKeyName') or
                    existing['defaultMetadataFieldValue'] != field_def.get('defaultMetadataFieldValue')
                )
                
                if has_conflict:
                    # Apply conflict resolution: most permissive settings
                    aggregated[field_name] = {
                        'metadataFieldValueType': 'string',  # Default to string
                        'required': False,  # Not required
                        'sequence': None,  # No sequence in conflict
                        'dependsOnFieldKeyName': None,  # No dependencies
                        'defaultMetadataFieldValue': None,  # No default
                        'controlledListKeys': [],  # No controlled list
                        'metadataSchemaMultiFieldConflict': True,
                        'schemaNames': aggregated[field_name]['schemaNames']  # Preserve schema sources
                    }
                    logger.info(f"Conflict detected for field '{field_name}' across schemas: {field_sources[field_name]}")
                else:
                    # No conflict - preserve the lowest sequence number if multiple schemas define it
                    existing_seq = existing.get('sequence')
                    new_seq = field_def.get('sequence')
                    if existing_seq is None or (new_seq is not None and new_seq < existing_seq):
                        aggregated[field_name]['sequence'] = new_seq
    
    return aggregated


def validate_metadata_keys_against_schema(
    metadata_dict: Dict[str, Any],
    aggregated_schema: Dict[str, Dict],
    restrict_to_schema: bool
) -> Tuple[bool, List[str]]:
    """Validate that metadata keys are allowed based on schema restrictions
    
    Args:
        metadata_dict: Dictionary of metadata {fieldName: {value, valueType}}
        aggregated_schema: Aggregated schema fields
        restrict_to_schema: If True, only schema-defined fields are allowed
        
    Returns:
        Tuple of (is_valid, error_messages)
    """
    errors = []
    
    # If restriction is disabled or no schema exists, allow all keys
    if not restrict_to_schema or not aggregated_schema:
        return True, []
    
    # Check each metadata key
    for field_name in metadata_dict.keys():
        if field_name not in aggregated_schema:
            errors.append(
                f"Field '{field_name}' is not defined in the metadata schema. "
                f"Only schema-defined fields are allowed when restrictMetadataOutsideSchemas is enabled."
            )
    
    is_valid = len(errors) == 0
    return is_valid, errors


def validate_depends_on_chain(
    metadata_dict: Dict[str, Any],
    aggregated_schema: Dict[str, Dict]
) -> Tuple[bool, List[str]]:
    """Validate that all dependsOn chains are satisfied recursively
    
    This recursively checks that:
    1. All fields in dependsOn list exist in metadata
    2. All fields in dependsOn list have non-empty values
    3. Dependencies don't create circular references
    
    Args:
        metadata_dict: Dictionary of metadata {fieldName: {value, valueType}}
        aggregated_schema: Aggregated schema fields
        
    Returns:
        Tuple of (is_valid, error_messages)
    """
    errors = []
    visited = set()  # Track visited fields to detect circular dependencies
    
    def check_field_dependencies(field_name: str, path: List[str]) -> bool:
        """Recursively check dependencies for a field"""
        if field_name in path:
            # Circular dependency detected
            errors.append(f"Circular dependency detected: {' -> '.join(path + [field_name])}")
            return False
        
        if field_name in visited:
            return True  # Already validated this field
        
        visited.add(field_name)
        
        if field_name not in aggregated_schema:
            return True  # Not a schema field, no dependencies to check
        
        depends_on_list = aggregated_schema[field_name].get('dependsOnFieldKeyName')
        if not depends_on_list:
            return True  # No dependencies
        
        # Handle both list and single string (for backward compatibility)
        if isinstance(depends_on_list, str):
            depends_on_list = [depends_on_list]
        
        for depends_on_field in depends_on_list:
            # Check if dependency exists in metadata
            if depends_on_field not in metadata_dict:
                errors.append(
                    f"Field '{field_name}' depends on '{depends_on_field}', "
                    f"but '{depends_on_field}' is not provided"
                )
                return False
            
            # Check if dependency has a non-empty value
            depends_on_value = metadata_dict[depends_on_field].get('metadataValue')
            if is_empty_value(depends_on_value):
                errors.append(
                    f"Field '{field_name}' depends on '{depends_on_field}', "
                    f"but '{depends_on_field}' is empty"
                )
                return False
            
            # Recursively check the dependency's dependencies
            if not check_field_dependencies(depends_on_field, path + [field_name]):
                return False
        
        return True
    
    # Check all fields in metadata
    for field_name in metadata_dict.keys():
        if field_name in aggregated_schema:
            check_field_dependencies(field_name, [])
    
    is_valid = len(errors) == 0
    return is_valid, errors


def validate_metadata_against_schema(
    metadata_dict: Dict[str, Any],
    aggregated_schema: Dict[str, Dict],
    operation_type: str,
    existing_metadata: Optional[Dict[str, Any]] = None
) -> Tuple[bool, List[str], Dict[str, Any]]:
    """Validate metadata against aggregated schema with comprehensive checks
    
    This function validates:
    1. Required fields are present and non-empty
    2. Field value types match schema definitions
    3. DependsOn chains are satisfied (recursive validation)
    4. Controlled list values are valid
    5. No circular dependencies exist
    6. Default values are applied where needed
    7. Type changes are prevented when schema defines the field
    
    For REPLACE_ALL operations (operation_type="PUT"):
    - metadata_dict contains ONLY the provided metadata (final state)
    - Validates that final state satisfies ALL schema requirements
    
    Args:
        metadata_dict: Dictionary of metadata {fieldName: {value, valueType}}
        aggregated_schema: Aggregated schema fields
        operation_type: "POST" or "PUT"
        existing_metadata: Optional existing metadata for type change validation
        
    Returns:
        Tuple of (is_valid, error_messages, metadata_with_defaults)
    """
    errors = []
    metadata_with_defaults = metadata_dict.copy()
    
    # If no schema exists, validation passes
    if not aggregated_schema:
        return True, [], metadata_with_defaults
    
    # Step 1: Apply default values for missing fields
    for field_name, field_def in aggregated_schema.items():
        if field_name not in metadata_with_defaults:
            default_value = field_def.get('defaultMetadataFieldValue')
            if default_value is not None:
                # Add field with default value
                metadata_with_defaults[field_name] = {
                    'metadataValue': default_value,
                    'metadataValueType': field_def.get('metadataFieldValueType', 'string')
                }
                logger.info(f"Applied default value for field '{field_name}': {default_value}")
    
    # Step 2: Validate required fields
    for field_name, field_def in aggregated_schema.items():
        if field_def.get('required', False):
            if field_name not in metadata_with_defaults:
                errors.append(f"Required field '{field_name}' is missing")
            else:
                field_value = metadata_with_defaults[field_name].get('metadataValue')
                if is_empty_value(field_value):
                    errors.append(f"Required field '{field_name}' cannot be empty")
    
    # Step 3: Validate field value types and prevent type changes
    for field_name, metadata_item in metadata_with_defaults.items():
        if field_name in aggregated_schema:
            expected_type = aggregated_schema[field_name].get('metadataFieldValueType')
            actual_type = metadata_item.get('metadataValueType')
            
            if expected_type and actual_type:
                # Normalize types for comparison
                expected_type_normalized = expected_type.lower()
                actual_type_normalized = actual_type.lower() if isinstance(actual_type, str) else actual_type
                
                if expected_type_normalized != actual_type_normalized:
                    errors.append(
                        f"Field '{field_name}' has incorrect type. "
                        f"Expected '{expected_type}', got '{actual_type}'"
                    )
                
                # NEW: Prevent type changes when field exists in current metadata with schema
                if existing_metadata and field_name in existing_metadata:
                    existing_type = existing_metadata[field_name].get('metadataValueType')
                    if existing_type:
                        existing_type_normalized = existing_type.lower() if isinstance(existing_type, str) else existing_type
                        if actual_type_normalized != existing_type_normalized:
                            errors.append(
                                f"Cannot change type of field '{field_name}' from '{existing_type}' to '{actual_type}'. "
                                f"Field type is defined by schema and cannot be changed."
                            )
    
    # Step 4: Validate dependsOn chains recursively (NEW - comprehensive)
    depends_valid, depends_errors = validate_depends_on_chain(
        metadata_with_defaults, aggregated_schema
    )
    if not depends_valid:
        errors.extend(depends_errors)
    
    # Step 5: Validate controlled list values (NEW)
    for field_name, metadata_item in metadata_with_defaults.items():
        if field_name in aggregated_schema:
            schema_def = aggregated_schema[field_name]
            value_type = schema_def.get('metadataFieldValueType')
            
            if value_type and value_type.lower() == 'inline_controlled_list':
                controlled_list = schema_def.get('controlledListKeys', [])
                actual_value = metadata_item.get('metadataValue')
                
                if controlled_list and actual_value not in controlled_list:
                    errors.append(
                        f"Field '{field_name}' value '{actual_value}' is not in "
                        f"controlled list: {controlled_list}"
                    )
    
    is_valid = len(errors) == 0
    return is_valid, errors, metadata_with_defaults


def validate_metadata_deletion(
    keys_to_delete: List[str],
    remaining_metadata: Dict[str, Any],
    aggregated_schema: Dict[str, Dict]
) -> Tuple[bool, List[str]]:
    """Validate that deletion won't violate schema constraints
    
    This function ensures:
    1. Required fields are not deleted
    2. Fields that other fields depend on are not deleted
    
    Args:
        keys_to_delete: List of metadata keys to delete
        remaining_metadata: Metadata that will remain after deletion
        aggregated_schema: Aggregated schema fields
        
    Returns:
        Tuple of (is_valid, error_messages)
    """
    errors = []
    
    if not aggregated_schema:
        return True, []  # No schema, no constraints
    
    for key_to_delete in keys_to_delete:
        if key_to_delete not in aggregated_schema:
            continue  # Not a schema field, can delete freely
        
        schema_def = aggregated_schema[key_to_delete]
        
        # Check 1: Cannot delete required fields
        if schema_def.get('required', False):
            errors.append(
                f"Cannot delete required field '{key_to_delete}'. "
                f"Required fields must always have a value."
            )
        
        # Check 2: Cannot delete if other fields depend on it
        for field_name, field_def in aggregated_schema.items():
            if field_name in remaining_metadata:  # Only check fields that will remain
                depends_on_list = field_def.get('dependsOnFieldKeyName')
                if depends_on_list:
                    # Handle both list and single string
                    if isinstance(depends_on_list, str):
                        depends_on_list = [depends_on_list]
                    
                    if key_to_delete in depends_on_list:
                        errors.append(
                            f"Cannot delete field '{key_to_delete}' because "
                            f"field '{field_name}' depends on it"
                        )
    
    is_valid = len(errors) == 0
    return is_valid, errors


def enrich_metadata_with_schema(
    metadata_list: List[Dict],
    aggregated_schema: Dict[str, Dict]
) -> List[Dict]:
    """Enrich metadata items with schema information and order by sequence
    
    This function:
    1. Enriches metadata with schema information
    2. Separates schema fields from non-schema fields
    3. Sorts schema fields by sequence number
    4. Returns schema fields first, then non-schema fields
    
    Args:
        metadata_list: List of metadata items from DynamoDB
        aggregated_schema: Aggregated schema fields
        
    Returns:
        List of enriched metadata items ordered by schema sequence
    """
    schema_fields = []      # Fields that match schema (with or without data)
    non_schema_fields = []  # Fields not in schema
    processed_fields = set()
    
    # Step 1: Process existing metadata items
    for metadata_item in metadata_list:
        field_name = metadata_item.get('metadataKey')
        processed_fields.add(field_name)
        
        enriched_item = metadata_item.copy()
        
        if field_name in aggregated_schema:
            schema_def = aggregated_schema[field_name]
            
            # NEW: Format schema names with databaseIds
            schema_names = schema_def.get('schemaNames', [])
            if schema_names:
                formatted_names = [f"{name} ({db_id})" for name, db_id in schema_names]
                enriched_item['metadataSchemaName'] = ", ".join(formatted_names)
            else:
                enriched_item['metadataSchemaName'] = None
            
            enriched_item['metadataSchemaField'] = True
            enriched_item['metadataSchemaRequired'] = schema_def.get('required', False)
            enriched_item['metadataSchemaSequence'] = schema_def.get('sequence')
            enriched_item['metadataSchemaDefaultValue'] = schema_def.get('defaultMetadataFieldValue')
            enriched_item['metadataSchemaDependsOn'] = schema_def.get('dependsOnFieldKeyName')
            enriched_item['metadataSchemaMultiFieldConflict'] = schema_def.get('metadataSchemaMultiFieldConflict', False)
            enriched_item['metadataSchemaControlledListKeys'] = schema_def.get('controlledListKeys', [])
            schema_fields.append(enriched_item)
        else:
            # Field not in schema
            enriched_item['metadataSchemaName'] = None
            enriched_item['metadataSchemaField'] = False
            enriched_item['metadataSchemaRequired'] = False
            enriched_item['metadataSchemaSequence'] = None
            enriched_item['metadataSchemaDefaultValue'] = None
            enriched_item['metadataSchemaDependsOn'] = None
            enriched_item['metadataSchemaMultiFieldConflict'] = False
            enriched_item['metadataSchemaControlledListKeys'] = []
            non_schema_fields.append(enriched_item)
    
    # Step 2: Add ALL schema fields that aren't in metadata (even without default values)
    #logger.info(f"Checking {len(aggregated_schema)} schema fields to add to response")
    for field_name, schema_def in aggregated_schema.items():
        if field_name not in processed_fields:
            default_value = schema_def.get('defaultMetadataFieldValue')
            #logger.info(f"Schema field '{field_name}' not in metadata. Default value: {default_value}")
            
            # NEW: Format schema names with databaseIds
            schema_names = schema_def.get('schemaNames', [])
            if schema_names:
                formatted_names = [f"{name} ({db_id})" for name, db_id in schema_names]
                schema_name_str = ", ".join(formatted_names)
            else:
                schema_name_str = None
            
            # ALWAYS add schema fields to response, even without default values
            # Use default value if available, otherwise empty string
            metadata_value = default_value if default_value is not None else ""
            
            # Create a metadata item for the schema field
            enriched_item = {
                'metadataKey': field_name,
                'metadataValue': metadata_value,
                'metadataValueType': schema_def.get('metadataFieldValueType', 'string'),
                'metadataSchemaName': schema_name_str,
                'metadataSchemaField': True,
                'metadataSchemaRequired': schema_def.get('required', False),
                'metadataSchemaSequence': schema_def.get('sequence'),
                'metadataSchemaDefaultValue': default_value,
                'metadataSchemaDependsOn': schema_def.get('dependsOnFieldKeyName'),
                'metadataSchemaMultiFieldConflict': schema_def.get('metadataSchemaMultiFieldConflict', False),
                'metadataSchemaControlledListKeys': schema_def.get('controlledListKeys', [])
            }
            schema_fields.append(enriched_item)
            logger.info(f"Added schema field '{field_name}' to response (value: '{metadata_value}')")
    
    # Step 3: Sort schema fields by sequence (None values go to end)
    def sort_key(item):
        seq = item.get('metadataSchemaSequence')
        if seq is None:
            return (1, item.get('metadataKey', ''))  # No sequence: sort by name at end
        return (0, seq, item.get('metadataKey', ''))  # Has sequence: sort by sequence, then name
    
    schema_fields.sort(key=sort_key)
    
    # Step 4: Return schema fields first, then non-schema fields
    return schema_fields + non_schema_fields
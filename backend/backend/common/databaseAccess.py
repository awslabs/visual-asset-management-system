# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Which databases a caller may read.

The database pre-filter every search route applies before it touches OpenSearch or the vector store:
a paginated scan of the database table (rows in a ``#deleted`` partition excluded) with a Casbin
``database`` GET enforcement per row. Hits are still enforced per asset by the caller; this narrows
the candidate set and supplies the database clause of the query.
"""

from typing import Any, Dict, List, Optional, Tuple

import boto3
from boto3.dynamodb.types import TypeDeserializer
from botocore.config import Config

from common.resourceNames import get_table_name, ResourceKeys
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger

logger = safeLogger(service_name="DatabaseAccess")

retry_config = Config(
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

dynamodb = boto3.resource('dynamodb', config=retry_config)
dynamodb_client = boto3.client('dynamodb', config=retry_config)

try:
    database_storage_table_name = get_table_name(ResourceKeys.DATABASE_STORAGE_TABLE)
except Exception:
    logger.exception("Failed resolving the database storage table name")
    raise

database_storage_table = dynamodb.Table(database_storage_table_name)


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

            # Casbin enforcement of the database record against database constraints; asset
            # constraints are enforced per search hit by the caller
            database.update({"object__type": "database"})
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
        return DatabaseAccessManager.get_accessible_databases_with_count(claims_and_roles, show_deleted, max_databases)[0]

    @staticmethod
    def get_accessible_databases_with_count(claims_and_roles: Dict[str, Any], show_deleted: bool = False, max_databases: int = 10000) -> Tuple[List[str], int]:
        """The accessible database ids and the number of database rows the scan visited.

        The count is taken before the Casbin check, so a caller can tell an all-access caller (every
        scanned database accessible) from one whose reach is a subset."""
        try:
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
                        processed_count += 1

                        # Casbin enforcement of the database record against database constraints;
                        # asset constraints are enforced per search hit by the caller
                        deserialized_document.update({"object__type": "database"})
                        if len(claims_and_roles.get("tokens", [])) > 0:
                            casbin_enforcer = CasbinEnforcer(claims_and_roles)
                            if casbin_enforcer.enforce(deserialized_document, "GET"):
                                accessible_databases.append(deserialized_document['databaseId'])

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
            return accessible_databases, processed_count

        except Exception as e:
            logger.exception(f"Error getting accessible databases: {e}")
            return [], 0

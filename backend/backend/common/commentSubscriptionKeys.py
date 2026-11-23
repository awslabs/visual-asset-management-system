#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Keys of the comment and subscription tables, which identify an asset by its database and asset id.

An asset id is unique within its database only, so a comment or a subscription is stored under both
ids. The table shapes are defined in infra/lib/nestedStacks/storage/storageBuilder-nestedStack.ts
(CommentStorageTableV2, SubscriptionsStorageTableV2); the v2.6 to v2.7 migration step
commentSubscriptionKeys copies the rows of the V1 tables into these keys.
"""

# Comment table: one partition per asset, one row per comment.
COMMENT_PARTITION_KEY = "databaseId:assetId"
COMMENT_SORT_KEY = "commentId"
# Local secondary index that lists one asset's comments on one asset version.
COMMENT_VERSION_INDEX = "AssetVersionIdLSI"
# A soft-deleted comment moves to the "<databaseId>:<assetId>#deleted" partition.
COMMENT_DELETED_SUFFIX = "#deleted"

# Subscription table: partition eventName, sort "<entityName>:<databaseId>:<entityId>".
SUBSCRIPTION_PARTITION_KEY = "eventName"
SUBSCRIPTION_SORT_KEY = "entityName:databaseId:entityId"

# The table's own key attribute is not part of a comment as the API returns it.
COMMENT_STORAGE_ONLY_ATTRIBUTES = (COMMENT_PARTITION_KEY,)


def comment_partition(database_id: str, asset_id: str, deleted: bool = False) -> str:
    """The comment table partition of one asset; ``deleted`` names its soft-deleted partition."""
    partition = f"{database_id}:{asset_id}"
    return f"{partition}{COMMENT_DELETED_SUFFIX}" if deleted else partition


def comment_response(item: dict) -> dict:
    """A stored comment row as the API returns it, without the table's composite key attribute."""
    return {k: v for k, v in (item or {}).items() if k not in COMMENT_STORAGE_ONLY_ATTRIBUTES}


def subscription_sort_value(entity_name: str, database_id: str, entity_id: str) -> str:
    """The subscription row's sort key value. Entity names (OBJECT_NAME) and database ids (ID) never
    contain ':', so the first two ':' separate the parts and an entity id may contain ':'."""
    return f"{entity_name}:{database_id}:{entity_id}"


def subscription_key(event_name: str, entity_name: str, database_id: str, entity_id: str) -> dict:
    """The full primary key of one subscription row, in the document (boto3 resource) form."""
    return {
        SUBSCRIPTION_PARTITION_KEY: event_name,
        SUBSCRIPTION_SORT_KEY: subscription_sort_value(entity_name, database_id, entity_id),
    }


def subscription_audit_id(event_name: str, entity_name: str, database_id: str, entity_id: str) -> str:
    """The subscriptionId an audit record carries for one subscription row."""
    return f"{event_name}#{entity_name}#{database_id}#{entity_id}"

# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Scope helpers for database-specific (per-database namespaced) tags and tag types.

A tag/tag-type is *global* when its ``databaseId`` is absent or the sentinel
``"GLOBAL"``; otherwise it is scoped to that database. Names are unique per
``(databaseId, name)`` rather than globally, so the same name may exist in
multiple databases (this is why ``name_used_by_any_database`` exists — a GLOBAL
name may not collide with a database-specific one). Assets still reference tags
by bare name, resolved within the asset's own database plus GLOBAL.
"""

from typing import Optional
from boto3.dynamodb.conditions import Key
from models.common import VAMSGeneralErrorResponse

GLOBAL_SCOPE = "GLOBAL"


def normalize_scope(database_id: Optional[str]) -> str:
    """Return the canonical scope for a stored/absent databaseId.

    Missing/empty -> GLOBAL sentinel; otherwise the value unchanged.
    """
    if database_id is None or database_id == "":
        return GLOBAL_SCOPE
    return database_id


def is_visible_in_scope(
    entity_database_id: Optional[str],
    requested_database_id: Optional[str],
    global_only: bool = False,
) -> bool:
    """Whether an entity with entity_database_id is visible for a request scope.

    - global_only=True: only global entities are visible.
    - requested_database_id set to X: global entities + entities scoped to X.
    - requested_database_id None (admin "all" view): everything visible.
    """
    scope = normalize_scope(entity_database_id)
    if global_only:
        return scope == GLOBAL_SCOPE
    if requested_database_id is None:
        return True
    return scope == GLOBAL_SCOPE or scope == requested_database_id


def verify_database_exists(database_id: str, database_table) -> bool:
    """Verify a database exists before scoping a tag/tag-type to it.

    GLOBAL is a sentinel, not a real database, so it is always valid.
    Raises VAMSGeneralErrorResponse if a non-global databaseId is not found.
    """
    if normalize_scope(database_id) == GLOBAL_SCOPE:
        return True
    response = database_table.get_item(Key={"databaseId": database_id})
    if "Item" not in response:
        raise VAMSGeneralErrorResponse("Referenced database does not exist.")
    return True


def name_used_by_any_database(table, index_name: str, name_attr: str, name: str) -> bool:
    """Whether a name is already used by any database-specific (non-GLOBAL) entry.

    Looks the name up via its GSI (``tagNameIndex`` / ``tagTypeNameIndex``) and
    reports True if any returned row is scoped to a real database. Used by the
    no-conflict rule when creating a GLOBAL tag/tag-type: a name may not exist as
    both a GLOBAL entry and a database-specific one.
    """
    response = table.query(
        IndexName=index_name,
        KeyConditionExpression=Key(name_attr).eq(name),
    )
    for item in response.get("Items", []):
        if normalize_scope(item.get("databaseId")) != GLOBAL_SCOPE:
            return True
    return False

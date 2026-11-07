# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Whether a database id names a database, for the compliance reads that take one.

A compliance read of a database id that names no database would answer like a read of an empty
database: an overview with no assets, an empty page. A mistyped id would then read as "no assets".
So the database-level compliance reads (the database overview, and the quarantine, cascade and
audit listings filtered by `databaseId`) answer `400` "Database not found" for such an id, as the
other compliance routes and the core handlers answer for a database a route names that does not
exist, once the caller has passed the read's own authorization check on it. A caller refused on the id gets the
answer it got before, whether the database exists or not, so the answer tells it nothing of the
database's existence.

A deleted database still names one: deleting it moves its row to `<databaseId>#deleted`, and its
compliance data moves under that id (`databaseRetirement`), where the history reads take it
(`validate_scope`). `<databaseId>#deleted` stays known after a database re-created with the id
removed that row, for as long as the retirement record of the move exists, so the deleted
database's history stays readable. `GLOBAL`, the scope of global schemas, names no database row
and is always accepted.
"""

from urllib.parse import unquote

from common.compliance import databaseRetirement
from common.validators import validate
from models.common import general_error

# The answer of a database-level compliance read for an id that names no database: the 400 of
# `general_error`, as `evaluate_database` and the binding routes answer it.
DATABASE_NOT_FOUND_STATUS = 400
DATABASE_NOT_FOUND_MESSAGE = "Database not found"
# The scope keyword the compliance validators accept beside database ids (`allowGlobalKeyword`).
GLOBAL_SCOPE = "GLOBAL"
# The suffix of a deleted database's row id (`databaseService.delete_database`).
DELETED_DATABASE_ID_SUFFIX = "#deleted"


def database_known(database_table, database_id: str, evaluation_table=None) -> bool:
    """Whether `database_id` names a database: `GLOBAL`, a database row, or the row a deleted
    database leaves under `<databaseId>#deleted`. One strongly-consistent GetItem (keys only), and
    a second only for an id with no live row. A `<databaseId>#deleted` id with no row is known
    while the retirement record of its move exists (one more GetItem, given `evaluation_table`)."""
    if database_id == GLOBAL_SCOPE:
        return True
    if databaseRetirement.is_deleted_scope(database_id):
        response = database_table.get_item(
            Key={"databaseId": database_id}, ConsistentRead=True, ProjectionExpression="databaseId")
        if response.get("Item"):
            return True
        return evaluation_table is not None and databaseRetirement.has_history(
            evaluation_table, database_id)
    for key in (database_id, f"{database_id}{DELETED_DATABASE_ID_SUFFIX}"):
        response = database_table.get_item(
            Key={"databaseId": key}, ConsistentRead=True, ProjectionExpression="databaseId")
        if response.get("Item"):
            return True
    return False


def decoded_scope(database_id):
    """A database id as a path or query parameter carries it: `%23deleted` decoded to `#deleted`
    (a `#` cannot appear unencoded in a URL)."""
    return unquote(database_id) if isinstance(database_id, str) and "%" in database_id else database_id


def validate_scope(database_id, optional=False, allow_global=True):
    """`validate` of a compliance history read's database id: an `ID` (or `GLOBAL`), or a deleted
    database's `<ID>#deleted`, whose part before the suffix is checked as an `ID`."""
    value = database_id
    if isinstance(value, str) and databaseRetirement.is_deleted_scope(value):
        value = databaseRetirement.base_id(value)
    return validate({"databaseId": {"value": value, "validator": "ID",
                                    "allowGlobalKeyword": allow_global, "optional": optional}})


def database_not_found(event):
    """The `400` "Database not found" response of a database-level compliance read."""
    return general_error(status_code=DATABASE_NOT_FOUND_STATUS,
                         body={"message": DATABASE_NOT_FOUND_MESSAGE}, event=event)
